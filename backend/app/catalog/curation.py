"""Ручная правка справочника категорий после автоматического вывода.

Автоматический разбор (ingredients.py, consolidate.py) даёт черновик с неточностями: категории в неподходящих
группах, блюда вместо продуктов, близкие дубликаты. Исправления записаны здесь явным списком операций, чтобы их
можно было прочитать, проверить и повторить. Правка применяется к базе один раз, после неё справочник
фиксируется в снимке (catalog/snapshot.py).

Операции выполняются по порядку; названия сравниваются по ключу нормализации (регистр, порядок слов).
    ("merge", основная, [дубликаты])  — перенести позиции в основную, названия дубликатов стать синонимами
    ("rename", старое, новое)         — переименовать (старое название остаётся синонимом)
    ("move", категория, группа)       — перенести в другую группу
    ("delete", категория)             — удалить (не продукт); позиции остаются без этой категории
"""

from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.consolidate import MergeOut, apply_merges
from app.catalog.taxonomy import display_name, normalize_key
from app.db.models import Category

CURATION: list[tuple] = [
    # --- азиатские продукты, овощи ---
    ("merge", "Имбирь маринованный", ["Имбирь"]),  # имбирь для суши — один и тот же закупаемый продукт
    ("move", "Салат чука", "Водоросли и азиатские продукты"),
    ("move", "Тофу", "Водоросли и азиатские продукты"),
    ("move", "Бобы эдамаме", "Овощи, фрукты и зелень"),
    ("merge", "Томат", ["Черри", "Помидоры черри"]),
    ("merge", "Зелень", ["Сезонная зелень"]),
    # --- крупы, мука, тесто ---
    ("merge", "Рис для суши", ["Рис"]),
    ("merge", "Панировочные сухари", ["Сухари панко"]),
    ("rename", "Тонкое тесто", "Тесто для пиццы"),
    ("rename", "Картошка фри", "Картофель фри"),
    ("move", "Картофель фри", "Овощи, фрукты и зелень"),
    ("delete", "Пицца Сырная"),  # готовое блюдо, не продукт
    ("move", "Шоколадный бисквит", "Сладости и десерты"),
    # --- молочные продукты ---
    ("merge", "Сливочный сыр", ["Творожный сливочный сыр", "Творожный сыр", "Творожно-сливочный крем"]),
    ("move", "Заварной крем", "Сладости и десерты"),
    # --- мясо и птица ---
    ("merge", "Курица", ["Курица гриль"]),
    ("merge", "Куриная грудка", ["Куриное филе"]),
    ("merge", "Колбаски", ["Баварские колбаски"]),
    ("move", "Куриный бульон", "Прочее"),
    # --- рыба и морепродукты ---
    ("merge", "Угорь", ["Угорь копчёный"]),
    ("merge", "Креветка", ["Креветка темпура"]),
    ("merge", "Снежный краб", ["Сурими"]),  # крабовые палочки и сурими — один продукт
    # --- напитки ---
    ("merge", "Вода без газа", ["Вода с газом"]),
    ("rename", "Вода без газа", "Вода"),
    ("merge", "Сок", ["Сок лайма", "Сок вишни"]),
    ("rename", "Американо", "Кофе"),
    ("merge", "Чай", ["Каркадэ"]),
    ("merge", "Ванильный сироп", ["Сироп манго"]),
    ("rename", "Ванильный сироп", "Сироп"),
    ("move", "Сироп", "Напитки"),
    # --- соусы и специи ---
    ("merge", "Средиземноморские приправы", ["Средиземноморские травы", "Средиземноморские специи"]),
    ("merge", "Специи", ["Смесь пряностей"]),
    ("merge", "Соус соевый", ["Соус на основе соевого"]),
    # --- яйца, сладости, прочее ---
    ("merge", "Японский омлет", ["Омлет"]),
    ("merge", "Мёд", ["Клубничный мёд"]),
    ("rename", "Пломбир", "Мороженое"),
    ("rename", "Демерара", "Сахар демерара"),
    ("rename", "Джус-боллы с манго", "Джус-боллы"),
]


@dataclass
class Report:
    merged: int = 0
    renamed: int = 0
    moved: int = 0
    deleted: int = 0
    problems: list[str] = field(default_factory=list)  # не найденные названия и конфликты

    def as_dict(self) -> dict:
        return self.__dict__.copy()


async def apply_curation(session: AsyncSession, operations: list[tuple] | None = None) -> Report:
    report = Report()
    for operation in CURATION if operations is None else operations:
        categories = list((await session.execute(select(Category))).scalars())
        by_key = {c.normalized_name: c for c in categories if not c.is_group}
        groups = {c.normalized_name: c for c in categories if c.is_group}
        kind, name = operation[0], operation[1]
        target = by_key.get(normalize_key(name))
        if target is None:
            report.problems.append(f"{kind}: нет категории «{name}»")
            continue

        if kind == "merge":
            present = [d for d in operation[2] if normalize_key(d) in by_key]
            report.problems += [f"merge: нет категории «{d}»" for d in operation[2] if d not in present]
            report.merged += await apply_merges(session, categories, [MergeOut(keep=name, merge=present)])
        elif kind == "rename":
            new_name = display_name(operation[2])
            new_key = normalize_key(new_name)
            if new_key in by_key and by_key[new_key].id != target.id:
                report.problems.append(f"rename: «{new_name}» уже существует")
                continue
            target.synonyms = [*(target.synonyms or []), target.name]
            target.name, target.normalized_name = new_name, new_key
            report.renamed += 1
        elif kind == "move":
            group = groups.get(normalize_key(operation[2]))
            if group is None:
                report.problems.append(f"move: нет группы «{operation[2]}»")
                continue
            target.parent_id, target.kind = group.id, group.kind
            report.moved += 1
        elif kind == "delete":
            await session.execute(text("DELETE FROM categories WHERE id = :id"), {"id": target.id})
            session.expunge(target)
            report.deleted += 1
        else:
            report.problems.append(f"неизвестная операция {kind}")
        await session.flush()
    return report
