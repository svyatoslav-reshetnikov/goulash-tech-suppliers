"""Запуск агента в фоне: создание записи, выполнение с событиями и сохранением итога."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from collections.abc import AsyncIterator

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.config import Limits
from app.agent.loop import run_agent
from app.agent.models import PlacesProvider, RegionRef, RunRequest, SearchProvider
from app.agent.pages import HttpFetcher
from app.agent.search import DGisPlaces, YandexSearch
from app.agent.tools import ToolBox
from app.config import Settings, get_settings
from app.db.models import Category, Run, RunEvent
from app.db.session import SessionLocal
from app.llm import get_llm
from app.locations import yandex_region
from app.net import settings_ssl_context
from app.runs.store import DbPageCache, DbSink, EventWriter

log = logging.getLogger(__name__)

MAX_ACTIVE_RUNS = 2  # запуск стоит денег (модель и поиск): параллельных запусков немного
_tasks: set[asyncio.Task] = set()


@dataclass
class Deps:
    llm: object
    searcher: SearchProvider
    fetcher: object
    places: PlacesProvider | None = None


def default_deps(settings: Settings) -> Deps:
    places = DGisPlaces(settings) if settings.dgis_api_key and settings.dgis_api_key.get_secret_value() else None
    return Deps(
        llm=get_llm(),
        searcher=YandexSearch(settings),
        fetcher=HttpFetcher(settings.crawl_user_agent, settings_ssl_context(settings)),
        places=places,
    )


# подменяется в тестах
deps_factory: Callable[[Settings], Deps] = default_deps


def limits_from(settings: Settings) -> Limits:
    return Limits(
        max_turns=settings.agent_max_turns,
        max_seconds=settings.agent_max_seconds,
        max_total_tokens=settings.agent_max_total_tokens,
        target_suppliers=settings.agent_target_suppliers,
        max_fetches=settings.agent_max_fetches,
    )


async def active_runs(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Run).where(Run.status.in_(["queued", "running"]))) or 0


async def runs_last_day(session: AsyncSession) -> int:
    since = datetime.now(UTC) - timedelta(days=1)
    return await session.scalar(select(func.count()).select_from(Run).where(Run.created_at >= since)) or 0


async def mark_interrupted(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> None:
    """При старте: запуски, прерванные перезапуском приложения, помечаются неудачными (задача фоновая и не переживает процесс)."""
    async with session_factory() as session:
        await session.execute(
            update(Run).where(Run.status.in_(["queued", "running"]))
            .values(status="failed", error="прервано перезапуском приложения", finished_at=func.now())
        )
        await session.commit()


async def start_run(run_id: int) -> None:
    task = asyncio.create_task(execute_run(SessionLocal, run_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def shutdown() -> None:
    for task in list(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)


async def execute_run(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, settings: Settings | None = None
) -> None:
    settings = settings or get_settings()
    emit = EventWriter(session_factory, run_id)
    status, stop_reason, summary, error, stats = "failed", None, None, None, None
    try:
        async with session_factory() as session:
            run = await session.get(Run, run_id)
            category = await session.get(Category, run.category_id) if run.category_id else None
            request = RunRequest(
                category=run.category_name,
                synonyms=tuple(category.synonyms or []) if category else (),
                regions=tuple(RegionRef(r["name"], r["role"]) for r in run.regions),
                target=settings.agent_target_suppliers,
                search_region=yandex_region(run.location_kind, run.location_key),
                city=run.location_key.split("|", 1)[1] if run.location_kind == "city" and run.location_key else None,
            )
            run.status, run.started_at = "running", datetime.now(UTC)
            await session.commit()

        deps = deps_factory(settings)
        limits = limits_from(settings)
        toolbox = ToolBox(request, limits, deps.searcher, deps.fetcher, DbSink(session_factory, run_id), emit,
                          places=deps.places, cache=DbPageCache(session_factory), llm=deps.llm)
        result = await run_agent(request, deps.llm, toolbox, emit, limits)
        # сбой модели в середине запуска не перечёркивает уже найденных поставщиков: запуск завершён с причиной
        status = "failed" if result.stop_reason == "llm_error" and not result.stats.get("suppliers_saved") else "done"
        stop_reason, summary, error, stats = result.stop_reason, result.summary, result.error, result.stats
    except asyncio.CancelledError:
        error = "остановлено"
        raise
    except Exception as exc:  # например, не заданы ключи: запуск завершается с понятной причиной
        log.exception("Запуск %s не удался", run_id)
        error = str(exc)[:500]
        await emit("run_failed", f"Запуск не удался: {error}")
    finally:
        async with session_factory() as session:
            await session.execute(
                update(Run).where(Run.id == run_id).values(
                    status=status, stop_reason=stop_reason, summary=summary, error=error, stats=stats,
                    finished_at=func.now(),
                )
            )
            await session.commit()


async def stream_events(
    session_factory: async_sessionmaker[AsyncSession], run_id: int, after: int = 0, poll: float = 0.7, ping_every: float = 15.0
) -> AsyncIterator[dict | None]:
    """События запуска по мере появления: сначала накопленные после `after`, затем новые, пока запуск идёт.

    Источник — таблица run_events, поэтому поток переживает обновление страницы и перезапуск приложения.
    None — сигнал «соединение живо» (heartbeat). В конце приходит событие stream_end со статусом запуска.
    """
    last, idle = after, 0.0
    while True:
        async with session_factory() as session:
            rows = list(
                (await session.execute(
                    select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > last).order_by(RunEvent.seq)
                )).scalars()
            )
            run = await session.get(Run, run_id)
        for event in rows:
            last = event.seq
            yield {"seq": event.seq, "type": event.type, "created_at": event.created_at.isoformat(), **event.payload}
        if run is None or (run.status not in ("queued", "running") and not rows):
            yield {"type": "stream_end", "status": run.status if run else "missing", "stop_reason": run.stop_reason if run else None}
            return
        if rows:
            idle = 0.0
        else:
            idle += poll
            if idle >= ping_every:
                idle = 0.0
                yield None
        await asyncio.sleep(poll)
