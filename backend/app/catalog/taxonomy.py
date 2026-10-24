"""Справочник категорий: нормализация названий и начальное заполнение (группы и упаковка вручную)."""

import json
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category

SEED_DIR = Path(__file__).parent / "seed"
FALLBACK_GROUP = "Прочее"
PACKAGING_KIND = "packaging"


def normalize_key(name: str) -> str:
    """Ключ уникальности: регистр, ё→е, без знаков препинания, слова по алфавиту.

    «Сыр сливочный» и «сливочный сыр» дают один ключ.
    """
    words = re.findall(r"[a-zа-я0-9]+", name.lower().replace("ё", "е"))
    return " ".join(sorted(words))


def display_name(name: str) -> str:
    cleaned = re.sub(r"\s+", " ", name.strip().strip("\"'«»")).strip()
    return cleaned[:1].upper() + cleaned[1:]


def load_groups() -> list[dict]:
    return json.loads((SEED_DIR / "category_groups.json").read_text(encoding="utf-8"))


def load_packaging() -> dict:
    return json.loads((SEED_DIR / "packaging.json").read_text(encoding="utf-8"))


async def ensure_seed(session: AsyncSession) -> None:
    """Создаёт группы и ручные категории упаковки, если их ещё нет. Существующие не перезаписывает."""
    existing = {c.normalized_name: c for c in (await session.execute(select(Category))).scalars()}

    groups: dict[str, Category] = {}
    for entry in load_groups():
        key = normalize_key(entry["name"])
        group = existing.get(key)
        if group is None:
            group = Category(
                name=entry["name"], normalized_name=key, is_group=True, kind=entry["kind"], origin="manual"
            )
            session.add(group)
            existing[key] = group
        groups[entry["name"]] = group
    await session.flush()

    packaging = load_packaging()
    parent = groups[packaging["group"]]
    for entry in packaging["categories"]:
        key = normalize_key(entry["name"])
        if key not in existing:
            session.add(
                Category(
                    name=entry["name"],
                    normalized_name=key,
                    parent_id=parent.id,
                    kind=PACKAGING_KIND,
                    synonyms=entry.get("synonyms", []),
                    origin="manual",
                )
            )
    await session.flush()
