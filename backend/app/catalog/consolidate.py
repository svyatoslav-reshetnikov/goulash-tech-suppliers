"""Слияние дубликатов в справочнике категорий.

После параллельного вывода в справочнике остаются варианты одного продукта («Нори» и «Водоросли нори»,
«Чука» и «Чукка»). Модель просматривает категории по одной группе за запрос и предлагает, что слить;
код проверяет предложение и переносит позиции в основную категорию, а название дубликата
добавляет в её синонимы (чтобы при следующем разборе он сразу сопоставлялся с ней).
"""

import asyncio
import logging
from collections import defaultdict

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog.ingredients import (
    CONCURRENCY,
    Emit,
    LLMClient,
    Stats,
    derive,
    extract_json,
    sync_category_status,
)
from app.catalog.taxonomy import normalize_key
from app.db.models import Category, Item, ItemCategory

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты наводишь порядок в справочнике закупаемых продуктов. Дан список категорий одной группы \
(в скобках число блюд, где категория встречается). Найди категории, которые обозначают ОДИН И ТОТ ЖЕ закупаемый \
продукт: разное написание или опечатка («Чука» и «Чукка», «Пеперони» и «Пепперони»), единственное и множественное \
число («Огурец» и «Огурцы»), синонимы («Картофель» и «Картошка»), лишнее слово при том же продукте \
(«Нори» и «Водоросли нори»).

НЕ объединяй разные продукты, даже родственные: «Сыр» и «Сливочный сыр», «Томат» и «Томаты черри», \
«Лосось» и «Тунец», «Курица» и «Куриное филе», «Соус терияки» и «Соус унаги».

Для каждой группы дубликатов выбери основное название из списка (общее и понятное, обычно с большим числом блюд).
Ответ — только JSON без пояснений:
{"merges": [{"keep": "Нори", "merge": ["Водоросли нори"]}]}
Если объединять нечего: {"merges": []}. Используй названия только из списка, дословно."""


class MergeOut(BaseModel):
    keep: str
    merge: list[str]


class MergesOut(BaseModel):
    merges: list[MergeOut] = []


def build_messages(group_name: str, entries: list[tuple[str, int]]) -> list[dict]:
    lines = "\n".join(f"- {name} ({count})" for name, count in entries)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Группа: {group_name}\nКатегории:\n{lines}"},
    ]


def parse_merges(text_: str) -> list[MergeOut]:
    try:
        return MergesOut.model_validate(extract_json(text_)).merges
    except ValidationError as exc:
        raise ValueError(f"ответ модели не по формату: {exc.error_count()} ошибок") from None


async def apply_merges(session: AsyncSession, categories: list[Category], merges: list[MergeOut]) -> int:
    """Применяет проверенные слияния внутри одной группы. Возвращает число слитых категорий."""
    by_key = {c.normalized_name: c for c in categories if c.origin == "derived" and not c.is_group}
    gone: set[int] = set()
    merged = 0
    for entry in merges:
        keep = by_key.get(normalize_key(entry.keep))
        if keep is None or keep.id in gone:
            continue
        for name in entry.merge:
            duplicate = by_key.get(normalize_key(name))
            if duplicate is None or duplicate.id == keep.id or duplicate.id in gone:
                continue
            await session.execute(
                text(
                    "INSERT INTO item_categories (item_id, category_id) "
                    "SELECT item_id, :keep FROM item_categories WHERE category_id = :dup ON CONFLICT DO NOTHING"
                ),
                {"keep": keep.id, "dup": duplicate.id},
            )
            synonyms = list(keep.synonyms or [])
            for synonym in [duplicate.name, *(duplicate.synonyms or [])]:
                if normalize_key(synonym) != keep.normalized_name and synonym not in synonyms:
                    synonyms.append(synonym)
            keep.synonyms = synonyms
            await session.execute(text("DELETE FROM categories WHERE id = :dup"), {"dup": duplicate.id})
            session.expunge(duplicate)  # строки уже нет: не даём сессии работать с ней дальше
            gone.add(duplicate.id)
            merged += 1
    return merged


async def consolidate(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    stats: Stats,
    emit: Emit | None = None,
    concurrency: int = CONCURRENCY,
) -> None:
    """Сливает дубликаты в группах, где в этом запуске появились новые категории."""
    if not stats.new_category_ids:
        return

    async with session_factory() as session:
        counts = dict(
            (
                await session.execute(
                    select(ItemCategory.category_id, func.count())
                    .join(Item, Item.id == ItemCategory.item_id)
                    .where(Item.status == "active")
                    .group_by(ItemCategory.category_id)
                )
            ).all()
        )
        rows = list(
            (
                await session.execute(
                    select(Category).where(
                        Category.origin == "derived", Category.is_group.is_(False), Category.status == "active"
                    )
                )
            ).scalars()
        )
        groups = {g.id: g.name for g in (await session.execute(select(Category).where(Category.is_group))).scalars()}
    by_group: dict[int, list[Category]] = defaultdict(list)
    for row in rows:
        if row.parent_id in groups:
            by_group[row.parent_id].append(row)
    targets = [
        (gid, items)
        for gid, items in by_group.items()
        if len(items) >= 2 and any(item.id in stats.new_category_ids for item in items)
    ]
    if not targets:
        return

    if emit:
        await emit(type="consolidating", groups=len(targets))
    semaphore = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()

    async def one(group_id: int, items: list[Category]) -> None:
        entries = sorted(((c.name, counts.get(c.id, 0)) for c in items), key=lambda e: (-e[1], e[0]))
        async with semaphore:
            try:
                merges = parse_merges(await llm.complete(build_messages(groups[group_id], entries)))
            except Exception as exc:
                log.warning("Слияние в группе %s не удалось: %s", groups[group_id], exc)
                if len(stats.errors) < 3:
                    stats.errors.append(f"слияние «{groups[group_id]}»: {str(exc)[:150]}")
                return
        if not merges:
            return
        async with lock, session_factory() as session:
            current = list(
                (
                    await session.execute(
                        select(Category).where(
                            Category.parent_id == group_id, Category.origin == "derived", Category.is_group.is_(False)
                        )
                    )
                ).scalars()
            )
            stats.categories_merged += await apply_merges(session, current, merges)
            await session.commit()

    await asyncio.gather(*(one(gid, items) for gid, items in targets))


async def build_categories(
    session_factory: async_sessionmaker[AsyncSession], llm: LLMClient, emit: Emit | None = None
) -> dict:
    """Полный проход: разбор новых и изменённых позиций, затем слияние дубликатов."""
    stats = Stats()
    await derive(session_factory, llm, emit, stats=stats)
    await consolidate(session_factory, llm, stats, emit)
    async with session_factory() as session:
        await sync_category_status(session, stats)
        await session.commit()
    return stats.as_dict()
