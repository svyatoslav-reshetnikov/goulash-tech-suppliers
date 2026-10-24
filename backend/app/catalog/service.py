"""Конвейер обновления каталога: обход сайта, сравнение, разбор на категории, журнал и блокировка.

В приложении не используется (справочник зафиксирован в снимке, см. snapshot.py): запускается только
вручную скриптом scripts/rebuild_catalog.py, потому что разбор делает платные запросы к модели.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.catalog.diff import Existing, check_sane, plan_changes
from app.catalog.eda1 import CrawlResult, Eda1Source, MenuItem
from app.catalog.consolidate import build_categories
from app.catalog.ingredients import count_active_categories, count_pending
from app.catalog.progress import SyncProgress, registry
from app.config import get_settings
from app.db.models import CatalogSync, Item
from app.db.session import SessionLocal, engine
from app.llm import get_llm

log = logging.getLogger(__name__)

LOCK_KEY = 726001  # произвольное число для pg_advisory_lock: одна синхронизация одновременно
_tasks: set[asyncio.Task] = set()
_current: dict[str, int | None] = {"id": None}

# подменяется в тестах
source_factory: Callable[[], Eda1Source] = lambda: Eda1Source(get_settings())
llm_factory = get_llm


class SyncAlreadyRunning(Exception):
    pass


def _sync_dict(sync: CatalogSync | None) -> dict | None:
    if sync is None:
        return None
    return {
        "id": sync.id,
        "trigger": sync.trigger,
        "status": sync.status,
        "started_at": sync.started_at,
        "finished_at": sync.finished_at,
        "partial": sync.partial,
        "stats": sync.stats,
        "error": sync.error,
    }


async def get_status(session: AsyncSession) -> dict:
    active = await session.scalar(select(func.count()).select_from(Item).where(Item.status == "active"))
    archived = await session.scalar(select(func.count()).select_from(Item).where(Item.status == "archived"))
    last = await session.scalar(
        select(CatalogSync).where(CatalogSync.status != "running").order_by(CatalogSync.id.desc()).limit(1)
    )
    last_success = await session.scalar(
        select(CatalogSync).where(CatalogSync.status == "success").order_by(CatalogSync.id.desc()).limit(1)
    )
    categories = await count_active_categories(session)
    pending = await count_pending(session)
    running_id = _current["id"]
    if running_id is not None:
        state = "loading"
    elif last_success is not None and active and categories:
        state = "ready"  # каталог есть, когда загружены позиции и построены категории
    else:
        state = "empty"
    return {
        "state": state,
        "running_sync_id": running_id,
        "active_items": active or 0,
        "archived_items": archived or 0,
        "categories_active": categories,
        "pending_items": pending,  # позиции, которые ещё не разобраны на категории
        "last_sync": _sync_dict(last),
        "last_success_at": last_success.finished_at if last_success else None,
    }


async def _release(lock_conn: AsyncConnection) -> None:
    """Снимает блокировку до возврата соединения в пул: иначе она осталась бы на нём."""
    try:
        await lock_conn.execute(select(func.pg_advisory_unlock(LOCK_KEY)))
        await lock_conn.commit()
    except Exception:
        log.exception("Не удалось снять блокировку синхронизации")
    finally:
        await lock_conn.close()


async def start_sync(trigger: str) -> int:
    """Запускает синхронизацию фоновой задачей. SyncAlreadyRunning, если она уже идёт."""
    if _current["id"] is not None:
        raise SyncAlreadyRunning()
    lock_conn = await engine.connect()
    try:
        got = (await lock_conn.execute(select(func.pg_try_advisory_lock(LOCK_KEY)))).scalar()
        if not got:
            await lock_conn.close()
            raise SyncAlreadyRunning()
        async with SessionLocal() as session:
            # блокировка у нас, значит запуски в статусе running — остатки прерванных процессов
            await session.execute(
                update(CatalogSync)
                .where(CatalogSync.status == "running")
                .values(status="failed", error="прервана перезапуском", finished_at=func.now())
            )
            sync = CatalogSync(trigger=trigger, status="running")
            session.add(sync)
            await session.commit()
            sync_id = sync.id
    except SyncAlreadyRunning:
        raise
    except BaseException:
        await _release(lock_conn)
        raise

    progress = registry.create(sync_id)
    _current["id"] = sync_id
    task = asyncio.create_task(_run(sync_id, lock_conn, progress))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return sync_id


def _result_stats(result: CrawlResult) -> dict:
    return {
        "cities_ok": len(result.cities_ok),
        "cities_failed": result.cities_failed,
        "conflicts": result.conflicts,
        "fetched": len(result.items),
    }


def _fill(obj: Item, item: MenuItem) -> None:
    obj.name = item.name
    obj.description = item.description
    obj.weight = item.weight
    obj.url = item.url
    obj.sections = list(item.sections)
    obj.content_hash = item.content_hash


async def _apply(session: AsyncSession, sync_id: int, result: CrawlResult) -> tuple[str, dict, str | None]:
    """Сравнивает результат обхода с базой и применяет одной транзакцией. Возвращает (статус, статистика, ошибка)."""
    stats = _result_stats(result)
    existing = {item.source_id: item for item in (await session.execute(select(Item))).scalars()}
    active_before = sum(1 for item in existing.values() if item.status == "active")

    reason = check_sane(active_before, len(result.items))
    if reason:
        return "rejected", stats, reason

    complete = not result.cities_failed
    plan = plan_changes(
        {sid: Existing(item.content_hash, item.status) for sid, item in existing.items()}, result.items, complete
    )
    now = datetime.now(UTC)
    for item in plan.add:
        obj = Item(source_id=item.source_id, status="active", first_seen_sync_id=sync_id, last_seen_sync_id=sync_id)
        _fill(obj, item)
        session.add(obj)
    for item in plan.update + plan.restore:
        obj = existing[item.source_id]
        _fill(obj, item)
        obj.status = "active"
        obj.archived_at = None
        obj.last_seen_sync_id = sync_id
    for source_id in plan.unchanged:
        existing[source_id].last_seen_sync_id = sync_id
    for source_id in plan.archive:
        obj = existing[source_id]
        obj.status = "archived"
        obj.archived_at = now
    await session.commit()

    stats.update(
        added=len(plan.add),
        updated=len(plan.update),
        restored=len(plan.restore),
        archived=len(plan.archive),
        unchanged=len(plan.unchanged),
        total_active=active_before + len(plan.add) + len(plan.restore) - len(plan.archive),
    )
    return "success", stats, None


async def _derive_categories(progress: SyncProgress) -> dict:
    """Разбор позиций на категории. Сбой не отменяет загрузку позиций: итог попадёт в статистику."""
    try:
        return await build_categories(SessionLocal, llm_factory(), progress.emit)
    except Exception as exc:
        log.exception("Не удалось построить категории")
        return {"error": str(exc)[:300], "items_total": None}


async def _finish(sync_id: int, status: str, partial: bool, stats: dict, error: str | None) -> None:
    async with SessionLocal() as session:
        await session.execute(
            update(CatalogSync)
            .where(CatalogSync.id == sync_id)
            .values(status=status, partial=partial, stats=stats, error=error, finished_at=func.now())
        )
        await session.commit()


async def _run(sync_id: int, lock_conn: AsyncConnection, progress: SyncProgress) -> None:
    status, error, partial, stats = "failed", None, False, {}
    try:
        await progress.emit(type="started", sync_id=sync_id)
        result = await source_factory().crawl(progress.emit)
        partial = bool(result.cities_failed)
        await progress.emit(type="applying", fetched=len(result.items))
        async with SessionLocal() as session:
            status, stats, error = await _apply(session, sync_id, result)
        if status == "success":
            stats["ingredients"] = await _derive_categories(progress)
    except asyncio.CancelledError:
        error = "остановлена"
        raise
    except Exception as exc:
        log.exception("Синхронизация каталога не удалась")
        error = str(exc)[:500]
    finally:
        try:
            await _finish(sync_id, status, partial, stats, error)
            await progress.emit(type="done", status=status, partial=partial, stats=stats, error=error)
            await progress.close()
        finally:
            _current["id"] = None
            await _release(lock_conn)


async def shutdown() -> None:
    for task in list(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
