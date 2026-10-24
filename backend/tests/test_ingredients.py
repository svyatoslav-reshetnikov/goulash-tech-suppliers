"""Категории-ингредиенты: чистые функции и вывод через LLM на настоящем Postgres.

Тесты с базой запускаются, только если задана TEST_DATABASE_URL, например:
    TEST_DATABASE_URL=postgresql+asyncpg://test@127.0.0.1:54329/suppliers_test pytest
"""

import asyncio
import json
import os
import re

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.ingredients import (
    BATCH_SIZE,
    build_messages,
    count_pending,
    derive,
    is_skippable,
    parse_response,
)
from app.catalog.consolidate import build_categories, parse_merges
from app.catalog.taxonomy import ensure_seed, normalize_key
from app.db.models import Base, Category, Item, ItemCategory
from app.llm import LLMTruncated

# --- чистые функции ---


def test_normalize_key_merges_word_order_and_yo():
    assert normalize_key("Сливочный сыр") == normalize_key("сыр сливочный")
    assert normalize_key("Кунжут «белый», ёж") == normalize_key("ЕЖ белый кунжут")
    assert normalize_key("!!!") == ""


def test_is_skippable_only_when_all_sections_are_sets():
    assert is_skippable(["Сеты"]) and is_skippable(("Сеты", "Наборы"))
    assert not is_skippable(["Сеты", "Роллы"]) and not is_skippable([])


def test_parse_response_accepts_objects_and_plain_strings_and_limits():
    text = '```json\n{"items": [{"id": 1, "ingredients": [{"name": "лосось", "group": "Рыба"}, "рис", "", "!!!"]}]}\n```'
    assert parse_response(text) == {1: [("Лосось", "Рыба"), ("Рис", None)]}
    many = {"items": [{"id": 2, "ingredients": [f"Продукт {i}" for i in range(40)]}]}
    assert len(parse_response(json.dumps(many))[2]) == 15


@pytest.mark.parametrize("bad", ["нет json", '{"items": "не список"}', '{"данные": []}', '{"items": [{"id": "x"}]}'])
def test_parse_response_rejects_bad_format(bad):
    with pytest.raises(ValueError):
        parse_response(bad)


def test_build_messages_contains_groups_existing_categories_and_dishes():
    item = Item(source_id=7, name="Имбирь", description="")
    messages = build_messages([item], ["Рыба и морепродукты", "Прочее"], {"Рыба и морепродукты": ["Лосось"], "Прочее": []})
    user = messages[1]["content"]
    assert "Рыба и морепродукты: Лосось" in user and "7. Название: Имбирь. Описание: (нет)" in user
    assert "Прочее:" not in user.split("Существующие категории")[1].split("Блюда")[0]  # пустые группы не показываем


# --- вывод категорий на настоящем Postgres ---

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="нужна TEST_DATABASE_URL")


class FakeLLM:
    """Отвечает по таблице {source_id: [(название, группа)]}; на запросы слияния отдаёт merges."""

    def __init__(self, table: dict[int, list], fail_on: set[int] | None = None, garbage_first: bool = False,
                 fail_if_multi: bool = False, truncated: bool = False, delay: float = 0.0, merges: list | None = None):
        self.table, self.fail_on, self.garbage = table, fail_on or set(), garbage_first
        self.fail_if_multi, self.truncated, self.delay = fail_if_multi, truncated, delay
        self.merges = merges or []
        self.calls: list[str] = []
        self.merge_calls: list[str] = []

    async def complete(self, messages: list[dict]) -> str:
        user = messages[1]["content"]
        if "наводишь порядок" in messages[0]["content"]:
            self.merge_calls.append(user)
            return json.dumps({"merges": self.merges}, ensure_ascii=False)
        self.calls.append(user)
        ids = [int(i) for i in re.findall(r"^(\d+)\. Название:", user, re.M)]
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.truncated:
            raise LLMTruncated("ответ модели обрезан по длине")
        if self.garbage:
            self.garbage = False
            return "не знаю, что ответить"
        if self.fail_on & set(ids) or (self.fail_if_multi and len(ids) > 1):
            raise RuntimeError("модель недоступна")
        return json.dumps(
            {"items": [{"id": i, "ingredients": [{"name": n, "group": g} for n, g in self.table.get(i, [])]} for i in ids]},
            ensure_ascii=False,
        )


def with_db(fn):
    def wrapper():  # без functools.wraps: pytest не должен видеть параметр factory
        async def run():
            engine = create_async_engine(TEST_DB)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            try:
                await fn(factory)
            finally:
                await engine.dispose()

        asyncio.run(run())

    wrapper.__name__ = fn.__name__
    return wrapper


def item(source_id: int, name: str, description: str = "", sections=("Роллы",), version: str = "v1") -> Item:
    return Item(source_id=source_id, name=name, description=description, sections=list(sections),
                content_hash=f"{source_id}-{version}", status="active", url="")


async def add_items(factory, *items: Item) -> None:
    async with factory() as session:
        session.add_all(items)
        await session.commit()


async def categories_of(factory, source_id: int) -> set[str]:
    async with factory() as session:
        rows = await session.execute(
            select(Category.name).join(ItemCategory, ItemCategory.category_id == Category.id)
            .join(Item, Item.id == ItemCategory.item_id).where(Item.source_id == source_id)
        )
        return set(rows.scalars())


async def category(factory, name: str) -> Category | None:
    async with factory() as session:
        return await session.scalar(select(Category).where(Category.normalized_name == normalize_key(name)))


@needs_db
@with_db
async def test_seed_creates_groups_and_manual_packaging(factory):
    async with factory() as session:
        await ensure_seed(session)
        await session.commit()
        await ensure_seed(session)  # повторный вызов ничего не дублирует
        await session.commit()
        groups = (await session.execute(select(Category).where(Category.is_group))).scalars().all()
        pack = await session.scalar(select(Category).where(Category.normalized_name == normalize_key("Палочки для еды")))
    assert len(groups) == 15 and pack.origin == "manual" and pack.kind == "packaging" and pack.parent_id is not None


@needs_db
@with_db
async def test_derive_builds_categories_links_and_skips_sets(factory):
    await add_items(
        factory,
        item(1, "Филадельфия", "Лосось, сливочный сыр, рис и нори"),
        item(2, "Имбирь", ""),
        item(3, "Сет Свежесть", "Состав: Филадельфия, Калифорния", sections=("Сеты",)),
    )
    llm = FakeLLM({1: [("Лосось", "Рыба и морепродукты"), ("Сливочный сыр", "Молочные продукты и сыры"), ("Рис", "Крупы, мука и тесто")],
                   2: [("Имбирь", "Соусы, приправы и специи")]})
    stats = await derive(factory, llm)

    assert stats["items_total"] == 2 and stats["items_done"] == 2 and stats["items_skipped"] == 1
    assert stats["categories_added"] == 4 and stats["batches_failed"] == 0
    assert len(llm.calls) == 1 and "Сет Свежесть" not in llm.calls[0]
    assert await categories_of(factory, 1) == {"Лосось", "Сливочный сыр", "Рис"}
    assert await categories_of(factory, 3) == set()
    salmon = await category(factory, "Лосось")
    async with factory() as session:
        parent = await session.get(Category, salmon.parent_id)
        assert parent.name == "Рыба и морепродукты" and parent.is_group and salmon.origin == "derived"
        assert await count_pending(session) == 0


@needs_db
@with_db
async def test_second_run_makes_no_llm_calls_and_changed_item_is_redone(factory):
    await add_items(factory, item(1, "Ролл", "лосось и рис"), item(2, "Суп", "курица"))
    llm = FakeLLM({1: [("Лосось", None), ("Рис", None)], 2: [("Курица", "Мясо и птица")]})
    await derive(factory, llm)
    calls = len(llm.calls)

    stats = await derive(factory, llm)
    assert stats["items_total"] == 0 and len(llm.calls) == calls  # ничего не изменилось: модель не вызывали

    async with factory() as session:  # описание позиции 1 изменилось на сайте
        await session.execute(update(Item).where(Item.source_id == 1).values(content_hash="1-v2", description="тунец"))
        await session.commit()
    llm.table[1] = [("Тунец", "Рыба и морепродукты")]
    stats = await derive(factory, llm)
    assert stats["items_total"] == 1 and await categories_of(factory, 1) == {"Тунец"}
    assert await categories_of(factory, 2) == {"Курица"}


@needs_db
@with_db
async def test_unused_derived_category_is_archived_and_restored_manual_never_archived(factory):
    await add_items(factory, item(1, "Ролл", "лосось"), item(2, "Палочки", ""))
    llm = FakeLLM({1: [("Лосось", "Рыба и морепродукты")], 2: [("Бамбуковые палочки", "Упаковка и расходники")]})
    await derive(factory, llm)
    assert (await category(factory, "Лосось")).status == "active"
    # синоним из seed: «Бамбуковые палочки» — это ручная категория «Палочки для еды», новая не создаётся
    assert await categories_of(factory, 2) == {"Палочки для еды"}
    assert await category(factory, "Бамбуковые палочки") is None

    async with factory() as session:  # позиции пропали с сайта (мягкое удаление)
        await session.execute(update(Item).values(status="archived"))
        await session.commit()
    stats = await derive(factory, llm)
    assert stats["categories_archived"] == 1  # только выведенная «Лосось»
    assert (await category(factory, "Лосось")).status == "archived"
    assert (await category(factory, "Палочки для еды")).status == "active"  # ручная не архивируется

    async with factory() as session:  # позиции вернулись
        await session.execute(update(Item).values(status="active"))
        await session.commit()
    stats = await derive(factory, llm)
    assert stats["categories_restored"] == 1 and (await category(factory, "Лосось")).status == "active"


@needs_db
@with_db
async def test_reuses_existing_category_regardless_of_word_order_and_filters_group_names(factory):
    await add_items(factory, item(1, "А", "сыр"), item(2, "Б", "сыр"))
    llm = FakeLLM({1: [("Сливочный сыр", "Молочные продукты и сыры"), ("Рыба и морепродукты", None)],
                   2: [("Сыр сливочный", "Молочные продукты и сыры"), ("Нечто странное", "Несуществующая группа")]})
    await derive(factory, llm, batch_size=1, concurrency=1)  # последовательно: вторая позиция видит категорию первой
    assert "Молочные продукты и сыры: Сливочный сыр" in llm.calls[1]
    assert await categories_of(factory, 1) == {"Сливочный сыр"}
    assert await categories_of(factory, 2) == {"Сливочный сыр", "Нечто странное"}  # слова в другом порядке: та же категория
    odd = await category(factory, "Нечто странное")
    async with factory() as session:
        assert (await session.get(Category, odd.parent_id)).name == "Прочее"  # неизвестная группа → «Прочее»


@needs_db
@with_db
async def test_failed_batch_keeps_items_pending_and_other_batches_proceed(factory):
    await add_items(factory, item(1, "А", "лосось"), item(2, "Б", "рис"), item(3, "В", "нори"))
    llm = FakeLLM({1: [("Лосось", None)], 2: [("Рис", None)], 3: [("Нори", None)]}, fail_on={2})
    stats = await derive(factory, llm, batch_size=1)
    assert stats["items_done"] == 2 and stats["items_failed"] == 1 and stats["batches_failed"] == 1
    assert "модель недоступна" in stats["errors"][0]
    async with factory() as session:
        assert await count_pending(session) == 1  # позиция 2 дождётся следующего запуска

    llm.fail_on = set()
    stats = await derive(factory, llm, batch_size=1)
    assert stats["items_total"] == 1 and stats["items_done"] == 1 and await categories_of(factory, 2) == {"Рис"}


@needs_db
@with_db
async def test_malformed_answer_is_retried_once(factory):
    await add_items(factory, item(1, "А", "лосось"))
    llm = FakeLLM({1: [("Лосось", None)]}, garbage_first=True)
    stats = await derive(factory, llm)
    assert len(llm.calls) == 2 and stats["items_done"] == 1 and stats["batches_failed"] == 0


@needs_db
@with_db
async def test_batches_follow_batch_size_and_emit_progress(factory):
    await add_items(factory, *[item(i, f"Блюдо {i}", "рис") for i in range(1, 6)])
    llm = FakeLLM({i: [("Рис", None)] for i in range(1, 6)})
    events: list[dict] = []

    async def emit(**event):
        events.append(event)

    await derive(factory, llm, emit, batch_size=2)
    assert len(llm.calls) == 3 and BATCH_SIZE >= 2
    progress = [(e["done"], e["total"]) for e in events]
    # пакеты завершаются в любом порядке, но прогресс идёт по возрастанию и заканчивается полным числом
    assert len(progress) == 4 and progress[0] == (0, 5) and progress[-1] == (5, 5)
    assert [d for d, _ in progress] == sorted(d for d, _ in progress) and all(total == 5 for _, total in progress)


# --- номера групп, параллельность, деление пакетов, слияние дубликатов ---


def test_parse_response_translates_numeric_groups():
    text = '{"items": [{"id": 1, "ingredients": [{"name": "Лосось", "group": 2}, {"name": "Рис", "group": 99}, {"name": "Соль", "group": "Прочее"}]}]}'
    assert parse_response(text, ["Мясо", "Рыба"]) == {1: [("Лосось", "Рыба"), ("Рис", None), ("Соль", "Прочее")]}


def test_parse_merges_validates_format():
    assert parse_merges('{"merges": [{"keep": "Нори", "merge": ["Водоросли нори"]}]}')[0].keep == "Нори"
    assert parse_merges('{"merges": []}') == []
    with pytest.raises(ValueError):
        parse_merges('{"merges": [{"keep": "Нори"}]}')


@needs_db
@with_db
async def test_failed_multi_item_batch_is_split_until_it_succeeds(factory):
    await add_items(factory, *[item(i, f"Блюдо {i}", "рис") for i in range(1, 6)])
    llm = FakeLLM({i: [("Рис", None)] for i in range(1, 6)}, fail_if_multi=True)
    stats = await derive(factory, llm, batch_size=5)
    assert stats["items_done"] == 5 and stats["batches_failed"] == 0  # пакет из 5 разделился до одиночных


@needs_db
@with_db
async def test_truncated_answer_is_not_retried_but_split(factory):
    await add_items(factory, item(1, "А", "рис"), item(2, "Б", "рис"))
    llm = FakeLLM({1: [("Рис", None)], 2: [("Рис", None)]}, truncated=True)
    stats = await derive(factory, llm, batch_size=2)
    # пакет из двух: один запрос, затем две половины по одному запросу (повторов нет); одиночные падают
    assert len(llm.calls) == 3 and stats["items_failed"] == 2 and stats["batches_failed"] == 2


@needs_db
@with_db
async def test_parallel_batches_do_not_create_the_same_category_twice(factory):
    await add_items(factory, *[item(i, f"Блюдо {i}", "лосось") for i in range(1, 9)])
    llm = FakeLLM({i: [("Лосось", "Рыба и морепродукты")] for i in range(1, 9)}, delay=0.05)
    stats = await derive(factory, llm, batch_size=1, concurrency=4)
    assert stats["items_done"] == 8 and stats["batches_failed"] == 0 and stats["categories_added"] == 1
    async with factory() as session:
        count = len((await session.execute(select(Category).where(Category.normalized_name == normalize_key("Лосось")))).scalars().all())
    assert count == 1


@needs_db
@with_db
async def test_consolidation_merges_duplicates_moves_items_and_keeps_synonyms(factory):
    await add_items(factory, item(1, "А", "нори"), item(2, "Б", "нори"), item(3, "В", "водоросли нори"), item(4, "Г", "сыр"))
    llm = FakeLLM(
        {1: [("Нори", "Водоросли и азиатские продукты")], 2: [("Нори", "Водоросли и азиатские продукты")],
         3: [("Водоросли нори", "Водоросли и азиатские продукты"), ("Сыр", "Молочные продукты и сыры")],
         4: [("Сыр", "Молочные продукты и сыры")]},
        merges=[{"keep": "Нори", "merge": ["Водоросли нори", "Не существует"]}, {"keep": "Сыр", "merge": ["Сыр"]}],
    )
    stats = await build_categories(factory, llm)
    assert stats["categories_added"] == 3 and stats["categories_merged"] == 1
    assert await category(factory, "Водоросли нори") is None
    assert await categories_of(factory, 3) == {"Нори", "Сыр"}  # позиция перенесена в основную категорию
    nori = await category(factory, "Нори")
    assert nori.synonyms == ["Водоросли нори"]
    assert len(llm.merge_calls) == 1  # слияние только в группе, где есть хотя бы две категории (у «Сыр» категория одна)


@needs_db
@with_db
async def test_consolidation_is_skipped_without_new_categories_and_merged_name_maps_to_keeper_later(factory):
    await add_items(factory, item(1, "А", "нори"), item(2, "Б", "водоросли нори"))
    llm = FakeLLM({1: [("Нори", "Водоросли и азиатские продукты")], 2: [("Водоросли нори", "Водоросли и азиатские продукты")]},
                  merges=[{"keep": "Нори", "merge": ["Водоросли нори"]}])
    await build_categories(factory, llm)
    merge_calls = len(llm.merge_calls)

    stats = await build_categories(factory, llm)  # ничего нового: слияние не запускается
    assert stats["items_total"] == 0 and len(llm.merge_calls) == merge_calls

    await add_items(factory, item(3, "В", "ещё водоросли нори"))  # новая позиция: модель снова пишет старое название
    llm.table[3] = [("Водоросли нори", "Водоросли и азиатские продукты")]
    stats = await build_categories(factory, llm)
    assert stats["categories_added"] == 0 and await categories_of(factory, 3) == {"Нори"}  # синоним сработал


@needs_db
@with_db
async def test_consolidation_never_touches_manual_categories(factory):
    await add_items(factory, item(1, "Палочки", ""), item(2, "Коробка", ""))
    llm = FakeLLM({1: [("Палочки для еды", "Упаковка и расходники")], 2: [("Коробки для пиццы", "Упаковка и расходники")]},
                  merges=[{"keep": "Палочки для еды", "merge": ["Коробки для пиццы"]}])
    stats = await build_categories(factory, llm)
    assert stats["categories_merged"] == 0 and await category(factory, "Коробки для пиццы") is not None
