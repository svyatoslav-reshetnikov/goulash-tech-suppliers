"""Реализации интерфейсов агента на Postgres: поставщики с цитатами, кэш страниц, события запуска."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.models import SaveOutcome, SupplierDraft
from app.agent.pages import Page
from app.agent.validation import DEDUP_FIELDS, near_duplicate
from app.db.models import FetchedPage, RunEvent, RunSupplier, Supplier, SupplierField

PAGE_TTL = timedelta(days=7)


class DbSink:
    """Сохраняет поставщика по домену; одинаковые значения полей не дублируются, а обновляют цитату и дату проверки."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], run_id: int):
        self._factory, self._run_id = session_factory, run_id

    async def save(self, draft: SupplierDraft) -> SaveOutcome:
        async with self._factory() as session:
            # Два запуска могут одновременно найти одного нового поставщика: сохранения одного домена идут по очереди
            await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"supplier:{draft.domain}"))))
            inns = draft.values("inn")
            if inns:
                twin = await session.scalar(
                    select(Supplier.domain).where(Supplier.inn.in_(inns), Supplier.domain != draft.domain)
                )
                if twin:
                    return SaveOutcome(0, "", False, duplicate_of=twin)

            supplier = await session.scalar(select(Supplier).where(Supplier.domain == draft.domain))
            created = supplier is None
            if created:
                names = draft.values("name")
                supplier = Supplier(domain=draft.domain, website=draft.website, name=(names[0] if names else draft.domain)[:300])
                session.add(supplier)
                await session.flush()
            if inns and not supplier.inn:
                supplier.inn = inns[0]

            existing = {
                (f.field, f.value): f
                for f in (await session.execute(select(SupplierField).where(SupplierField.supplier_id == supplier.id))).scalars()
            }
            now = datetime.now(UTC)
            for item in draft.fields:
                known = existing.get((item.field, item.value))
                if known is None and item.field in DEDUP_FIELDS:
                    similar = next(
                        (f for (name, value), f in existing.items() if name == item.field and near_duplicate(name, value, item.value)), None
                    )
                    if similar is not None:  # тот же адрес или описание в другой записи: дубль не копим
                        if len(item.value) > len(similar.value):  # новая запись полнее: заменяем значение вместе с цитатой
                            existing.pop((similar.field, similar.value), None)
                            similar.value, similar.quote, similar.source_url = item.value, item.quote, item.source_url
                            existing[(similar.field, similar.value)] = similar
                        similar.run_id, similar.verified_at = self._run_id, now
                        continue
                if known is None:
                    created_field = SupplierField(supplier_id=supplier.id, field=item.field, value=item.value,
                                                  source_url=item.source_url, quote=item.quote, run_id=self._run_id)
                    session.add(created_field)
                    existing[(item.field, item.value)] = created_field
                else:  # то же значение найдено снова: обновляем источник и дату проверки
                    known.source_url, known.quote, known.run_id, known.verified_at = item.source_url, item.quote, self._run_id, now
            await session.execute(
                insert(RunSupplier).values(run_id=self._run_id, supplier_id=supplier.id).on_conflict_do_nothing()
            )
            await session.commit()
            return SaveOutcome(supplier.id, supplier.name, created)


    async def record_research(self, domain: str, pages: list[str], missing: list[str]) -> None:
        """Какие страницы сайта изучены и каких обязательных полей на них не нашлось (последнее изучение)."""
        async with self._factory() as session:
            supplier = await session.scalar(select(Supplier).where(Supplier.domain == domain))
            if supplier is not None:
                supplier.research = {"pages": pages[:40], "missing": missing, "researched_at": datetime.now(UTC).isoformat()}
                await session.commit()


class DbPageCache:
    """Кэш страниц между запусками: повторный запуск не нагружает сайты поставщиков."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._factory = session_factory

    async def get(self, url: str) -> Page | None:
        async with self._factory() as session:
            row = await session.scalar(select(FetchedPage).where(FetchedPage.url == url))
        if row is None or datetime.now(UTC) - row.fetched_at > PAGE_TTL:
            return None
        from app.agent.validation import host_of

        return Page(url=row.url, title=row.title, text=row.text, links=[tuple(link) for link in row.links], site=host_of(row.url) or "")

    async def put(self, page: Page) -> None:
        async with self._factory() as session:
            values = {"url": page.url, "title": page.title, "text": page.text, "links": [list(link) for link in page.links],
                      "fetched_at": datetime.now(UTC)}
            await session.execute(
                insert(FetchedPage).values(**values).on_conflict_do_update(index_elements=["url"], set_=values)
            )
            await session.commit()


class EventWriter:
    """Пишет события запуска с порядковым номером. Используется как emit агента."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], run_id: int):
        self._factory, self._run_id, self._seq = session_factory, run_id, 0

    async def __call__(self, type_: str, message: str = "", **data) -> None:
        self._seq += 1
        async with self._factory() as session:
            session.add(RunEvent(run_id=self._run_id, seq=self._seq, type=type_, payload={"message": message, **data}))
            await session.commit()


async def count_run_suppliers(session: AsyncSession, run_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(RunSupplier).where(RunSupplier.run_id == run_id)) or 0
