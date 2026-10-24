"""Вывод категорий-ингредиентов из позиций меню с помощью LLM.

Разбираются только новые и изменённые позиции. Модель видит текущий справочник и должна сначала
сопоставить ингредиент с существующей категорией, а новую создавать, только если подходящей нет.
Запросы к модели идут параллельно, а запись в справочник строго по очереди (под замком), чтобы
параллельные пакеты не создавали одну категорию дважды. Дубликаты, которые всё же возникли
(«Нори» и «Водоросли нори»), потом сливает consolidate.py.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog.taxonomy import FALLBACK_GROUP, display_name, ensure_seed, normalize_key
from app.db.models import Category, Item, ItemCategory
from app.llm import LLMTruncated

log = logging.getLogger(__name__)

# Описания этих разделов перечисляют названия других блюд, а не ингредиенты
SKIP_SECTIONS = {"Сеты", "Наборы", "WOW price"}
BATCH_SIZE = 12
CONCURRENCY = 4
MAX_SPLIT_DEPTH = 3  # неразобранный пакет делится пополам не более трёх раз
MAX_INGREDIENTS_PER_ITEM = 15
MAX_NAME_LENGTH = 80

Emit = Callable[..., Awaitable[None]]


class LLMClient(Protocol):
    """Интерфейс модели; реализация: app.llm.LLM."""

    async def complete(self, messages: list[dict]) -> str: ...


class IngredientOut(BaseModel):
    name: str
    group: int | str | None = None  # номер группы из списка в запросе (или её название)


class ItemOut(BaseModel):
    id: int
    ingredients: list[IngredientOut | str] = []


class ResponseOut(BaseModel):
    items: list[ItemOut]


@dataclass(frozen=True)
class ItemRow:
    """Позиция для запроса к модели (отдельно от ORM, чтобы не зависеть от состояния сессии)."""

    id: int
    source_id: int
    name: str
    description: str


def is_skippable(sections: list[str] | tuple[str, ...]) -> bool:
    return bool(sections) and all(section in SKIP_SECTIONS for section in sections)


# --- запрос к модели и разбор ответа ---

SYSTEM_PROMPT = """Ты помогаешь закупщику сети суши- и пицца-доставки. По названию и описанию блюда выдели \
ингредиенты: продукты, которые кухня закупает у поставщиков. По этим категориям потом ищут поставщиков.

Правила:
1. Категория — закупаемый продукт, а не блюдо. Не включай блюда и десерты (пицца, чизкейк, пирожное, фокачча), \
способы приготовления, маркетинговые и фирменные слова, вес, количество, игрушки и подарки, и всё, чего нет \
в названии или описании. Исключение: готовые полуфабрикаты, которые закупают как продукт (тесто, тортилья, \
панировочные сухари).
2. Название в именительном падеже, в единственном числе, как продукт закупают: «Лосось», «Сливочный сыр», \
«Огурец», «Томат», «Рис для суши». Без слов «фирменный», «домашний», «свежий».
3. Соусы и специи называй конкретно («Соус терияки», «Соус унаги», «Перец чили»): это разные закупаемые продукты.
4. Напитки называй родовым словом без вкуса и бренда: «Лимонад», «Морс», «Сок», «Чай», «Кофе», «Вода», «Пиво», \
«Вино», «Коктейль» (вместо «Лимонад Арбуз-кизил» пиши «Лимонад»).
5. Если подходящая категория уже есть в списке «Существующие категории», используй её название дословно. \
Новую создавай, только если подходящей нет.
6. Для каждого ингредиента укажи номер группы из списка групп.
7. Упаковку и расходники (палочки, контейнеры, пакеты) относи к группе «Упаковка и расходники».
8. Если в описании нет ингредиентов (там только названия других блюд), верни пустой список. Если описания нет: \
когда название — сам продукт, напиток или расходник (Имбирь, Васаби, Палочки, Сок), верни его; когда название — \
готовое блюдо, верни пустой список.

Ответ — только JSON без пояснений:
{"items": [{"id": 1, "ingredients": [{"name": "Лосось", "group": 1}, {"name": "Рис для суши", "group": 7}]}]}"""


def build_messages(batch: list, groups: list[str], existing: dict[str, list[str]]) -> list[dict]:
    """batch: объекты с полями source_id, name, description (ItemRow или Item)."""
    numbered = "\n".join(f"{n}. {name}" for n, name in enumerate(groups, start=1))
    known = "\n".join(f"{group}: {', '.join(names)}" for group, names in existing.items() if names) or "(пока пусто)"
    dishes = "\n".join(
        f"{item.source_id}. Название: {item.name}. Описание: {item.description or '(нет)'}" for item in batch
    )
    user = f"Группы:\n{numbered}\n\nСуществующие категории (по группам):\n{known}\n\nБлюда:\n{dishes}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def extract_json(text_: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text_)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("в ответе модели нет JSON")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"ответ модели не разобрался как JSON: {exc}") from None


def parse_response(text_: str, groups: list[str] | None = None) -> dict[int, list[tuple[str, str | None]]]:
    """Возвращает {source_id: [(название, группа)]}. Выбрасывает ValueError, если ответ не по формату.

    Группа в ответе — номер из списка в запросе (1…N) или её название; номера переводятся в названия по groups.
    """
    try:
        parsed = ResponseOut.model_validate(extract_json(text_))
    except ValidationError as exc:
        raise ValueError(f"ответ модели не по формату: {exc.error_count()} ошибок") from None
    result: dict[int, list[tuple[str, str | None]]] = {}
    for item in parsed.items:
        pairs: list[tuple[str, str | None]] = []
        for ingredient in item.ingredients[:MAX_INGREDIENTS_PER_ITEM]:
            name, group = (ingredient, None) if isinstance(ingredient, str) else (ingredient.name, ingredient.group)
            if isinstance(group, int):
                group = groups[group - 1] if groups and 1 <= group <= len(groups) else None
            name = display_name(name)
            if name and len(name) <= MAX_NAME_LENGTH and normalize_key(name):
                pairs.append((name, group))
        result[item.id] = pairs
    return result


# --- запись в справочник ---


@dataclass
class Stats:
    items_total: int = 0
    items_done: int = 0
    items_failed: int = 0
    items_skipped: int = 0  # сеты и наборы
    categories_added: int = 0
    categories_restored: int = 0
    categories_archived: int = 0
    categories_merged: int = 0
    batches_failed: int = 0
    errors: list[str] = field(default_factory=list)
    new_category_ids: set[int] = field(default_factory=set)

    def as_dict(self) -> dict:
        data = self.__dict__.copy()
        data.pop("new_category_ids")
        return data


@dataclass
class Context:
    by_key: dict[str, Category]
    by_synonym: dict[str, Category]
    groups: dict[str, Category]  # по нормализованному названию группы

    @classmethod
    async def load(cls, session: AsyncSession) -> "Context":
        categories = list((await session.execute(select(Category))).scalars())
        by_key: dict[str, Category] = {}
        by_synonym: dict[str, Category] = {}
        groups: dict[str, Category] = {}
        for category in categories:
            if category.is_group:
                groups[category.normalized_name] = category
                continue
            by_key[category.normalized_name] = category
            for synonym in category.synonyms or []:
                by_synonym.setdefault(normalize_key(synonym), category)
        return cls(by_key, by_synonym, groups)

    def group_names(self) -> list[str]:
        return [group.name for group in self.groups.values()]

    def existing_by_group(self) -> dict[str, list[str]]:
        names = {group.id: group.name for group in self.groups.values()}
        result: dict[str, list[str]] = {group.name: [] for group in self.groups.values()}
        for category in self.by_key.values():
            if category.status == "active" and category.parent_id in names:
                result[names[category.parent_id]].append(category.name)
        return {group: sorted(items) for group, items in result.items()}


async def _resolve(session: AsyncSession, ctx: Context, stats: Stats, name: str, group: str | None) -> Category | None:
    key = normalize_key(name)
    if key in ctx.groups:  # модель вернула название группы вместо ингредиента
        return None
    category = ctx.by_key.get(key) or ctx.by_synonym.get(key)
    if category is None:
        parent = ctx.groups.get(normalize_key(group or "")) or ctx.groups[normalize_key(FALLBACK_GROUP)]
        category = Category(
            name=name, normalized_name=key, parent_id=parent.id, kind=parent.kind, origin="derived", status="active"
        )
        session.add(category)
        await session.flush()
        ctx.by_key[key] = category
        stats.categories_added += 1
        stats.new_category_ids.add(category.id)
    elif category.status == "archived":
        category.status = "active"
        stats.categories_restored += 1
    return category


async def _apply_batch(
    session: AsyncSession, ctx: Context, stats: Stats, batch: list[Item], parsed: dict[int, list[tuple[str, str | None]]]
) -> int:
    done = 0
    for item in batch:
        if item.source_id not in parsed:
            continue
        categories: dict[int, Category] = {}
        for name, group in parsed[item.source_id]:
            category = await _resolve(session, ctx, stats, name, group)
            if category is not None:
                categories[category.id] = category
        await session.execute(delete(ItemCategory).where(ItemCategory.item_id == item.id))
        session.add_all(ItemCategory(item_id=item.id, category_id=cid) for cid in categories)
        item.ingredients_hash = item.content_hash
        done += 1
    return done


async def sync_category_status(session: AsyncSession, stats: Stats) -> None:
    """Выведенная категория без активных позиций уходит в архив, а при возвращении позиций оживает.

    Ручные категории (группы, упаковка) не архивируются.
    """
    has_active_items = (
        "EXISTS (SELECT 1 FROM item_categories ic JOIN items i ON i.id = ic.item_id "
        "WHERE ic.category_id = categories.id AND i.status = 'active')"
    )
    archived = await session.execute(
        text(
            "UPDATE categories SET status = 'archived' WHERE origin = 'derived' AND is_group = false "
            f"AND status = 'active' AND NOT {has_active_items}"
        )
    )
    restored = await session.execute(
        text(
            "UPDATE categories SET status = 'active' WHERE origin = 'derived' AND is_group = false "
            f"AND status = 'archived' AND {has_active_items}"
        )
    )
    stats.categories_archived += archived.rowcount or 0
    stats.categories_restored += restored.rowcount or 0


async def _ask(llm: LLMClient, messages: list[dict], groups: list[str]) -> dict[int, list[tuple[str, str | None]]]:
    """Спрашивает модель; при ответе не по формату повторяет запрос один раз (обрезанный ответ не повторяем:
    результат был бы тем же, пакет делится на части выше по стеку)."""
    last: Exception | None = None
    for _ in range(2):
        try:
            return parse_response(await llm.complete(messages), groups)
        except LLMTruncated:
            raise
        except ValueError as exc:
            last = exc
    raise ValueError(str(last))


async def derive(
    session_factory: async_sessionmaker[AsyncSession],
    llm: LLMClient,
    emit: Emit | None = None,
    batch_size: int = BATCH_SIZE,
    concurrency: int = CONCURRENCY,
    stats: Stats | None = None,
) -> dict:
    """Разбирает на категории новые и изменённые позиции. Сбой пакета не останавливает остальные."""
    stats = stats or Stats()

    async def notify(**event) -> None:
        if emit:
            await emit(**event)

    async with session_factory() as session:
        await ensure_seed(session)
        items = list((await session.execute(select(Item).where(Item.status == "active").order_by(Item.source_id))).scalars())
        skippable = [item for item in items if is_skippable(item.sections)]
        stats.items_skipped = len(skippable)
        if skippable:  # позиция могла попасть в «Сеты» уже после разбора: её категории больше не нужны
            await session.execute(delete(ItemCategory).where(ItemCategory.item_id.in_([i.id for i in skippable])))
        pending_ids = [
            item.id for item in items if not is_skippable(item.sections) and item.ingredients_hash != item.content_hash
        ]
        await session.commit()

    stats.items_total = len(pending_ids)
    await notify(type="deriving", done=0, total=stats.items_total)

    apply_lock = asyncio.Lock()  # запись в справочник по очереди, модель опрашиваем параллельно
    semaphore = asyncio.Semaphore(max(1, concurrency))

    def record_failure(count: int, exc: Exception, source_ids: list[int]) -> None:
        stats.batches_failed += 1
        stats.items_failed += count
        if len(stats.errors) < 3:
            stats.errors.append(str(exc)[:200])
        log.warning("Пакет позиций %s не разобран: %s", source_ids, exc)

    async def process(ids: list[int], depth: int = 0) -> None:
        async with semaphore:
            async with session_factory() as session:
                rows = (await session.execute(select(Item).where(Item.id.in_(ids)).order_by(Item.source_id))).scalars()
                snapshot = [ItemRow(i.id, i.source_id, i.name, i.description) for i in rows]
                ctx = await Context.load(session)
                groups, existing = ctx.group_names(), ctx.existing_by_group()
            pieces: list[list[int]] = []
            parsed: dict[int, list[tuple[str, str | None]]] = {}
            try:
                parsed = await _ask(llm, build_messages(snapshot, groups, existing), groups)
            except Exception as exc:
                if len(ids) > 1 and depth < MAX_SPLIT_DEPTH:  # длинный ответ мог оборваться: пробуем меньшими частями
                    half = len(ids) // 2
                    pieces = [ids[:half], ids[half:]]
                else:
                    record_failure(len(ids), exc, [r.source_id for r in snapshot])
                    return
        if pieces:
            await asyncio.gather(*(process(piece, depth + 1) for piece in pieces))
            return
        async with apply_lock:
            async with session_factory() as session:
                try:
                    batch = list(
                        (await session.execute(select(Item).where(Item.id.in_(ids)).order_by(Item.source_id))).scalars()
                    )
                    apply_ctx = await Context.load(session)
                    done = await _apply_batch(session, apply_ctx, stats, batch, parsed)
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    record_failure(len(ids), exc, [r.source_id for r in snapshot])
                    return
            stats.items_done += done
            stats.items_failed += len(ids) - done
            # событие внутри замка: числа прогресса приходят по возрастанию, даже если пакеты завершаются в другом порядке
            await notify(type="deriving", done=stats.items_done + stats.items_failed, total=stats.items_total)

    chunks = [pending_ids[i : i + batch_size] for i in range(0, len(pending_ids), batch_size)]
    await asyncio.gather(*(process(chunk) for chunk in chunks))

    async with session_factory() as session:
        await sync_category_status(session, stats)
        await session.commit()
    return stats.as_dict()


async def count_pending(session: AsyncSession) -> int:
    rows = (await session.execute(select(Item).where(Item.status == "active"))).scalars()
    return sum(1 for item in rows if not is_skippable(item.sections) and item.ingredients_hash != item.content_hash)


async def count_active_categories(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(Category).where(Category.status == "active", Category.is_group.is_(False))
        )
        or 0
    )
