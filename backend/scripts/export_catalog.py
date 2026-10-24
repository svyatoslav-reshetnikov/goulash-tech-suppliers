"""Выгружает справочник из базы в снимок (JSON на стандартный вывод).

    python -m scripts.export_catalog > app/catalog/seed/catalog_snapshot.json
"""

import asyncio
import json
import sys

from app.catalog.snapshot import export_snapshot
from app.db.session import SessionLocal, engine


async def main() -> None:
    async with SessionLocal() as session:
        data = await export_snapshot(session)
    await engine.dispose()
    json.dump(data, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


asyncio.run(main())
