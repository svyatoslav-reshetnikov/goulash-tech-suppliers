import os

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["служебное"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    """Состояние сервиса и доступность базы."""
    try:
        await session.execute(text("select 1"))
        database = "ok"
    except Exception:
        database = "error"
    healthy = database == "ok"
    return JSONResponse(
        status_code=200 if healthy else 503,
        # version: the image tag written by the deployment (IMAGE_TAG in .env); "dev" when running locally
        content={"status": "ok" if healthy else "degraded", "database": database, "version": os.environ.get("IMAGE_TAG", "dev")},
    )
