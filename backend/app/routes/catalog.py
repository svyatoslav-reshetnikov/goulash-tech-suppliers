from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import service
from app.db.models import Item
from app.db.session import get_session

router = APIRouter(prefix="/catalog", tags=["каталог"])


@router.get("/status")
async def status(session: AsyncSession = Depends(get_session)) -> dict:
    """Состояние справочника. Обновление из приложения отключено: справочник зафиксирован в снимке."""
    return await service.get_status(session)


@router.get("/items")
async def items(
    q: str | None = None,
    status: str = Query("active", pattern="^(active|archived)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    condition = [Item.status == status]
    if q:
        like = f"%{q.strip()}%"
        condition.append(or_(Item.name.ilike(like), Item.description.ilike(like)))
    total = await session.scalar(select(func.count()).select_from(Item).where(*condition))
    rows = (
        await session.execute(select(Item).where(*condition).order_by(Item.source_id).limit(limit).offset(offset))
    ).scalars()
    return JSONResponse(
        {
            "total": total or 0,
            "items": [
                {
                    "source_id": i.source_id,
                    "name": i.name,
                    "description": i.description,
                    "weight": i.weight,
                    "sections": i.sections,
                }
                for i in rows
            ],
        }
    )
