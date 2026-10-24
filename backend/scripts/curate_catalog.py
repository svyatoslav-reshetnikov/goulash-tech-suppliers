"""Применяет ручную правку (app/catalog/curation.py) к справочнику в базе. Запрос к модели не делается.

    python -m scripts.curate_catalog
"""

import asyncio
import json

from app.catalog.curation import apply_curation
from app.db.session import SessionLocal, engine


async def main() -> None:
    async with SessionLocal() as session:
        report = await apply_curation(session)
        await session.commit()
    await engine.dispose()
    print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))


asyncio.run(main())
