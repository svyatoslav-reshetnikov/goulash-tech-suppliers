from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Supplier
from app.db.session import get_session

router = APIRouter(prefix="/suppliers", tags=["поставщики"])

MAX_NOTE_LENGTH = 2000


class NoteBody(BaseModel):
    note: str = Field("", max_length=MAX_NOTE_LENGTH)

    @field_validator("note")
    @classmethod
    def strip(cls, value: str) -> str:
        return value.strip()


@router.put("/{supplier_id}/note")
async def save_note(supplier_id: int, body: NoteBody, session: AsyncSession = Depends(get_session)) -> dict:
    """Сохраняет заметку о поставщике (пустая заметка удаляет её). Заметка принадлежит поставщику, а не запуску."""
    supplier = await session.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден")
    supplier.note = body.note or None
    await session.commit()
    return {"id": supplier.id, "note": supplier.note}
