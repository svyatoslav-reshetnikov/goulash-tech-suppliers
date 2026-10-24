"""Перестраивает каталог с нуля: обход eda1.ru и разбор через YandexGPT. ПЛАТНО: десятки запросов к модели.

В приложении обновление отключено: справочник зафиксирован в снимке. Этот скрипт нужен, только если
каталог действительно требуется пересобрать; после него нужно заново применить правку (curate_catalog)
и выгрузить снимок (export_catalog).

    python -m scripts.rebuild_catalog --yes
"""

import argparse
import asyncio
import sys

from app.catalog import service
from app.db.session import engine


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="подтверждаю, что запросы к модели платные")
    if not parser.parse_args().yes:
        print("Скрипт делает платные запросы к модели. Повторите с --yes.", file=sys.stderr)
        return 1
    await service.start_sync("manual")
    await asyncio.gather(*service._tasks)
    await engine.dispose()
    return 0


raise SystemExit(asyncio.run(main()))
