"""Ручная правка справочника, снимок каталога (экспорт и импорт), целостность файла снимка."""

import asyncio
import json
import os
from collections import Counter

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.catalog.curation import CURATION, apply_curation
from app.catalog.ingredients import count_active_categories, count_pending
from app.catalog.snapshot import SNAPSHOT_PATH, SNAPSHOT_VERSION, export_snapshot, import_snapshot
from app.catalog.taxonomy import ensure_seed, normalize_key
from app.db.models import Base, CatalogSync, Category, Item, ItemCategory

TEST_DB = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="нужна TEST_DATABASE_URL")


def with_db(fn):
    def wrapper():
        async def run():
            engine = create_async_engine(TEST_DB)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)
            try:
                await fn(async_sessionmaker(engine, expire_on_commit=False))
            finally:
                await engine.dispose()

        asyncio.run(run())

    wrapper.__name__ = fn.__name__
    return wrapper


async def build_catalog(factory) -> None:
    """Небольшой справочник: группы из seed, несколько выведенных категорий и позиции со связями."""
    async with factory() as session:
        await ensure_seed(session)
        groups = {c.name: c for c in (await session.execute(select(Category).where(Category.is_group))).scalars()}

        def cat(name: str, group: str) -> Category:
            return Category(name=name, normalized_name=normalize_key(name), parent_id=groups[group].id,
                            kind=groups[group].kind, origin="derived")

        fish, rice, nori, nori2, dish = (cat("Лосось", "Рыба и морепродукты"), cat("Рис", "Крупы, мука и тесто"),
                                         cat("Нори", "Водоросли и азиатские продукты"),
                                         cat("Водоросли нори", "Водоросли и азиатские продукты"),
                                         cat("Пицца Сырная", "Крупы, мука и тесто"))
        beans = cat("Бобы эдамаме", "Грибы")
        session.add_all([fish, rice, nori, nori2, dish, beans])
        await session.flush()
        rows = [
            (1, "Филадельфия", "лосось, рис, нори", [fish, rice, nori]),
            (2, "Ролл с нори", "рис и водоросли нори", [rice, nori2]),
            (3, "Пицца", "сырная", [dish]),
            (4, "Эдамаме", "бобы", [beans]),
        ]
        for source_id, name, description, cats in rows:
            item = Item(source_id=source_id, name=name, description=description, sections=["Роллы"],
                        content_hash=f"h{source_id}", ingredients_hash=f"h{source_id}", status="active", url="")
            session.add(item)
            await session.flush()
            session.add_all(ItemCategory(item_id=item.id, category_id=c.id) for c in cats)
        await session.commit()


async def category_names(factory, source_id: int) -> set[str]:
    async with factory() as session:
        return set(
            (await session.execute(
                select(Category.name).join(ItemCategory, ItemCategory.category_id == Category.id)
                .join(Item, Item.id == ItemCategory.item_id).where(Item.source_id == source_id)
            )).scalars()
        )


@needs_db
@with_db
async def test_curation_merge_rename_move_delete_and_missing_names(factory):
    await build_catalog(factory)
    operations = [
        ("merge", "Нори", ["Водоросли нори", "Несуществующая"]),
        ("rename", "Рис", "Рис для суши"),
        ("move", "Бобы эдамаме", "Овощи, фрукты и зелень"),
        ("delete", "Пицца Сырная"),
        ("move", "Нет такой", "Прочее"),
        ("rename", "Лосось", "Рис для суши"),  # конфликт имён
        ("move", "Нори", "Нет такой группы"),
    ]
    async with factory() as session:
        report = await apply_curation(session, operations)
        await session.commit()

    assert (report.merged, report.renamed, report.moved, report.deleted) == (1, 1, 1, 1)
    assert len(report.problems) == 4  # несуществующая категория, «нет такой», конфликт, нет группы
    assert await category_names(factory, 2) == {"Рис для суши", "Нори"}  # позиция перенесена в основную категорию
    assert await category_names(factory, 3) == set()  # блюдо удалено из справочника
    async with factory() as session:
        nori = await session.scalar(select(Category).where(Category.normalized_name == normalize_key("Нори")))
        rice = await session.scalar(select(Category).where(Category.normalized_name == normalize_key("Рис для суши")))
        beans = await session.scalar(select(Category).where(Category.normalized_name == normalize_key("Бобы эдамаме")))
        parent = await session.get(Category, beans.parent_id)
        assert nori.synonyms == ["Водоросли нори"] and rice.synonyms == ["Рис"]
        assert parent.name == "Овощи, фрукты и зелень"
        assert await session.scalar(select(Category).where(Category.normalized_name == normalize_key("Водоросли нори"))) is None


def test_curation_list_is_well_formed():
    kinds = {"merge": 3, "rename": 3, "move": 3, "delete": 2}
    for operation in CURATION:
        assert operation[0] in kinds and len(operation) == kinds[operation[0]], operation
    merged_away = [d for op in CURATION if op[0] == "merge" for d in op[2]]
    assert len(merged_away) == len(set(merged_away)), "категория сливается дважды"
    assert not set(merged_away) & {op[1] for op in CURATION if op[0] in {"merge", "rename", "delete"}} - {
        "Вода без газа"  # её сначала сливают с «Вода с газом», потом переименовывают: порядок операций важен
    }


@needs_db
@with_db
async def test_export_then_import_into_empty_database_restores_everything(factory):
    await build_catalog(factory)
    async with factory() as session:
        await apply_curation(session, [("merge", "Нори", ["Водоросли нори"]), ("delete", "Пицца Сырная")])
        await session.commit()
        snapshot = await export_snapshot(session)
    snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False))  # как после записи в файл и чтения

    assert snapshot["version"] == SNAPSHOT_VERSION
    derived = {c["name"] for c in snapshot["categories"] if c["origin"] == "derived"}
    manual = [c for c in snapshot["categories"] if c["origin"] == "manual"]
    assert derived == {"Лосось", "Рис", "Нори", "Бобы эдамаме"} and len(manual) == 12  # упаковка из seed
    assert [c for c in snapshot["categories"] if c["name"] == "Нори"][0]["synonyms"] == ["Водоросли нори"]
    assert len(snapshot["items"]) == 4

    # «другая машина»: чистая база
    engine = factory.kw["bind"]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        await import_snapshot(session, snapshot)
        await session.commit()

    async with factory() as session:
        assert await count_active_categories(session) == 16 and await count_pending(session) == 0
        assert (await session.scalar(select(CatalogSync).where(CatalogSync.status == "success"))).trigger == "snapshot"
    assert await category_names(factory, 1) == {"Лосось", "Рис", "Нори"}
    assert await category_names(factory, 2) == {"Рис", "Нори"}
    assert await category_names(factory, 3) == set()

    async with factory() as session:  # повторный импорт ничего не дублирует
        await import_snapshot(session, snapshot)
        await session.commit()
        assert await count_active_categories(session) == 16
        assert len((await session.execute(select(Item))).scalars().all()) == 4
        assert snapshot == await export_snapshot(session)  # снимок воспроизводится байт в байт


@needs_db
@with_db
async def test_import_rejects_unknown_version(factory):
    async with factory() as session:
        with pytest.raises(ValueError):
            await import_snapshot(session, {"version": 999})


@pytest.mark.skipif(not SNAPSHOT_PATH.is_file(), reason="файл снимка ещё не создан")
def test_committed_snapshot_is_consistent():
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert data["version"] == SNAPSHOT_VERSION
    groups = {g["name"] for g in data["groups"]}
    names = [c["name"] for c in data["categories"]]
    keys = [normalize_key(n) for n in names]
    assert len(keys) == len(set(keys)), "названия категорий не уникальны"
    assert not {normalize_key(g) for g in groups} & set(keys), "категория совпадает с группой"
    assert all(c["group"] in groups for c in data["categories"])
    known = set(names)
    assert all(name in known for item in data["items"] for name in item["categories"])
    assert Counter(i["source_id"] for i in data["items"]).most_common(1)[0][1] == 1, "дубли позиций"
    assert all(c["origin"] in {"derived", "manual"} for c in data["categories"])
