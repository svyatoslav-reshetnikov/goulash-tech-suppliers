"""Снимок каталога: готовый справочник в одном файле, который развёртывается без сети и без модели.

Справочник построен один раз (обход eda1.ru, разбор через YandexGPT, ручная правка curation.py) и зафиксирован
в `seed/catalog_snapshot.json`. При старте на пустой базе (новая машина, чистый том) приложение само
загружает снимок за доли секунды. Обновление каталога из приложения отключено намеренно: оно стоит денег.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.eda1 import content_hash
from app.catalog.ingredients import count_active_categories
from app.catalog.taxonomy import normalize_key
from app.db.models import CatalogSync, Category, Item, ItemCategory
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).parent / "seed" / "catalog_snapshot.json"
SNAPSHOT_VERSION = 1


async def export_snapshot(session: AsyncSession) -> dict:
    """Справочник (активные категории и группы) и позиции меню со связями, в порядке, пригодном для diff."""
    categories = list(
        (await session.execute(select(Category).where(Category.status == "active").order_by(Category.id))).scalars()
    )
    group_names = {c.id: c.name for c in categories if c.is_group}
    items = list((await session.execute(select(Item).where(Item.status == "active").order_by(Item.source_id))).scalars())
    links = (await session.execute(select(ItemCategory.item_id, ItemCategory.category_id))).all()
    names = {c.id: c.name for c in categories}
    by_item: dict[int, list[str]] = {}
    for item_id, category_id in links:
        if category_id in names:
            by_item.setdefault(item_id, []).append(names[category_id])

    return {
        "version": SNAPSHOT_VERSION,
        "source": "eda1.ru",
        "groups": [{"name": c.name, "kind": c.kind} for c in categories if c.is_group],
        "categories": [
            {
                "name": c.name,
                "group": group_names.get(c.parent_id),
                "kind": c.kind,
                "synonyms": c.synonyms or [],
                "origin": c.origin,
            }
            for c in categories
            if not c.is_group
        ],
        "items": [
            {
                "source_id": i.source_id,
                "name": i.name,
                "description": i.description,
                "weight": i.weight,
                "url": i.url,
                "sections": i.sections,
                "categories": sorted(by_item.get(i.id, [])),
            }
            for i in items
        ],
    }


async def import_snapshot(session: AsyncSession, data: dict) -> dict:
    """Загружает снимок в базу. Существующие категории и позиции (по ключу и по id с сайта) обновляются, а не дублируются."""
    if data.get("version") != SNAPSHOT_VERSION:
        raise ValueError(f"неизвестная версия снимка: {data.get('version')}")

    existing = {c.normalized_name: c for c in (await session.execute(select(Category))).scalars()}

    def upsert_category(entry: dict, parent_id: int | None, is_group: bool) -> Category:
        key = normalize_key(entry["name"])
        category = existing.get(key)
        if category is None:
            category = Category(name=entry["name"], normalized_name=key)
            session.add(category)
            existing[key] = category
        category.parent_id = parent_id
        category.is_group = is_group
        category.kind = entry["kind"]
        category.synonyms = entry.get("synonyms", [])
        category.origin = "manual" if is_group else entry.get("origin", "derived")
        category.status = "active"
        return category

    groups: dict[str, Category] = {}
    for entry in data["groups"]:
        groups[entry["name"]] = upsert_category(entry, None, True)
    await session.flush()
    leaves: dict[str, Category] = {}
    for entry in data["categories"]:
        leaves[entry["name"]] = upsert_category(entry, groups[entry["group"]].id, False)
    await session.flush()

    sync = CatalogSync(
        trigger="snapshot",
        status="success",
        finished_at=datetime.now(UTC),
        stats={"snapshot": True, "categories": len(leaves), "items": len(data["items"])},
    )
    session.add(sync)
    await session.flush()

    known_items = {i.source_id: i for i in (await session.execute(select(Item))).scalars()}
    for entry in data["items"]:
        item = known_items.get(entry["source_id"])
        if item is None:
            item = Item(source_id=entry["source_id"], first_seen_sync_id=sync.id)
            session.add(item)
            known_items[entry["source_id"]] = item
        item.name, item.description, item.weight = entry["name"], entry["description"], entry["weight"]
        item.url, item.sections = entry["url"], entry["sections"]
        item.content_hash = content_hash(entry["name"], entry["description"], entry["weight"], entry["sections"])
        item.ingredients_hash = item.content_hash  # разбор на категории не требуется: он уже сделан
        item.status, item.archived_at, item.last_seen_sync_id = "active", None, sync.id
    await session.flush()

    await session.execute(delete(ItemCategory).where(ItemCategory.item_id.in_([i.id for i in known_items.values()])))
    session.add_all(
        ItemCategory(item_id=known_items[entry["source_id"]].id, category_id=leaves[name].id)
        for entry in data["items"]
        for name in entry["categories"]
        if name in leaves
    )
    return sync.stats


async def ensure_loaded() -> None:
    """При старте: если справочника в базе нет, загружает снимок; если есть, ничего не делает. Сеть и модель не нужны."""
    try:
        async with SessionLocal() as session:
            if await count_active_categories(session) > 0:
                log.info("Справочник категорий уже в базе: ничего не делаю")
                return
            if not SNAPSHOT_PATH.is_file():
                log.error("Справочника в базе нет, а файла снимка %s тоже нет", SNAPSHOT_PATH)
                return
            stats = await import_snapshot(session, json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")))
            await session.commit()
            log.info("Справочник загружен из снимка: %s категорий, %s позиций", stats["categories"], stats["items"])
    except Exception:
        log.exception("Не удалось загрузить справочник из снимка")
