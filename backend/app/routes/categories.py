from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Item, ItemCategory
from app.db.session import get_session

router = APIRouter(prefix="/categories", tags=["категории"])


@router.get("")
async def categories(session: AsyncSession = Depends(get_session)) -> dict:
    """Дерево категорий: группы и ингредиенты с числом активных позиций каталога, где они встречаются."""
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
    rows = list((await session.execute(select(Category).where(Category.status == "active"))).scalars())
    children: dict[int, list[dict]] = {}
    for row in rows:
        if row.is_group or row.parent_id is None:
            continue
        children.setdefault(row.parent_id, []).append(
            {
                "id": row.id,
                "name": row.name,
                "synonyms": row.synonyms,
                "origin": row.origin,
                "items": counts.get(row.id, 0),
            }
        )
    groups = []
    for group in sorted((r for r in rows if r.is_group), key=lambda g: (g.kind == "packaging", g.name)):
        leaves = sorted(children.get(group.id, []), key=lambda c: (-c["items"], c["name"]))
        if leaves:
            groups.append({"id": group.id, "name": group.name, "kind": group.kind, "categories": leaves})
    return {"total": sum(len(g["categories"]) for g in groups), "groups": groups}
