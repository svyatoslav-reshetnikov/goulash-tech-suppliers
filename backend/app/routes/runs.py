import json
from collections.abc import AsyncIterator
from dataclasses import asdict
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.research import missing_fields
from app.agent.validation import (
    address_problem,
    collapse_duplicates,
    fact_problem,
    product_stems,
    value_supported_by_quote,
)
from app.config import get_settings
from app.db.models import Category, Run, RunEvent, RunSupplier, Supplier, SupplierField
from app.db.session import SessionLocal, get_session
from app.export import HEADERS, build_csv, build_xlsx, export_rows, file_name
from app.locations import get_locations
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.ranking.commerce import commerce_payload
from app.runs import service

router = APIRouter(prefix="/runs", tags=["запуски"])


class RunCreate(BaseModel):
    """Один товар строго из справочника и место поиска."""

    category_id: int
    location_kind: str | None = Field(None, pattern="^(city|region)$")
    location_key: str | None = Field(None, max_length=200)
    include_neighbors: bool = False


def _run_dict(run: Run, with_candidates: bool = True) -> dict:
    stats = run.stats if with_candidates or not run.stats else {k: v for k, v in run.stats.items() if k != "candidates"}
    return {
        "id": run.id, "status": run.status, "stop_reason": run.stop_reason, "category": run.category_name,
        "location": run.location_label, "include_neighbors": run.include_neighbors, "regions": run.regions,
        "summary": run.summary, "error": run.error, "stats": stats,
        "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
    }


@router.post("", status_code=202)
async def create_run(body: RunCreate, session: AsyncSession = Depends(get_session)) -> dict:
    category = await session.get(Category, body.category_id)
    if category is None or category.is_group or category.status != "active":
        raise HTTPException(status_code=404, detail="Категория не найдена")

    regions: list[dict] = []
    label = None
    if body.location_kind and body.location_key:
        locations = get_locations()
        selected = locations.resolve(body.location_kind, body.location_key)
        if selected is None:
            raise HTTPException(status_code=404, detail="Локация не найдена")
        label = selected.label if selected.kind == "region" else f"{selected.label}, {selected.region_name}"
        regions = [{"id": r.id, "name": r.name, "role": role}
                   for r, role in locations.expand(selected.region_id, body.include_neighbors)]

    # проверка лимитов и создание запуска — под одной блокировкой: два одновременных запроса не пройдут оба
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext("runs:create"))))
    limit = get_settings().agent_max_runs_per_day
    if await service.runs_last_day(session) >= limit:
        raise HTTPException(status_code=429, detail=f"Достигнут суточный лимит запусков ({limit}): каждый запуск платный. Попробуйте завтра")
    if await service.active_runs(session) >= service.MAX_ACTIVE_RUNS:
        raise HTTPException(status_code=429, detail="Уже идут другие запуски: дождитесь их завершения")
    run = Run(category_id=category.id, category_name=category.name, location_kind=body.location_kind,
              location_key=body.location_key, location_label=label, include_neighbors=body.include_neighbors,
              regions=regions, status="queued")
    session.add(run)
    await session.commit()
    await service.start_run(run.id)
    return {"run_id": run.id}


@router.get("")
async def list_runs(limit: int = Query(20, ge=1, le=200), session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(Run).order_by(Run.id.desc()).limit(limit))).scalars()
    return {"runs": [_run_dict(r, with_candidates=False) for r in rows]}


@router.get("/previous")
async def previous_run(
    category_id: int,
    location_kind: str | None = Query(None, pattern="^(city|region)$"),
    location_key: str | None = Query(None, max_length=200),
    include_neighbors: bool = False,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Последний такой же поиск (тот же товар, место и соседние регионы), если он уже был: идёт или завершён.
    Неудавшиеся запуски не считаются: их стоит повторить."""
    query = select(Run).where(
        Run.category_id == category_id,
        Run.include_neighbors == include_neighbors,
        Run.status.in_(("queued", "running", "done")),
        Run.location_kind.is_(None) if location_kind is None else Run.location_kind == location_kind,
        Run.location_key.is_(None) if location_key is None else Run.location_key == location_key,
    )
    run = (await session.execute(query.order_by(Run.id.desc()).limit(1))).scalar_one_or_none()
    return {"run": _run_dict(run, with_candidates=False) if run else None}


@router.get("/{run_id}")
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    return _run_dict(run)


@router.get("/{run_id}/events")
async def run_events(run_id: int, after: int = Query(0, ge=0), session: AsyncSession = Depends(get_session)) -> dict:
    """Шаги агента после номера `after` (для ленты; живой поток SSE появится вместе с интерфейсом поиска)."""
    if await session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    rows = (
        await session.execute(select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.seq > after).order_by(RunEvent.seq))
    ).scalars()
    return {"events": [{"seq": e.seq, "type": e.type, "created_at": e.created_at, **e.payload} for e in rows]}


@router.get("/{run_id}/stream")
async def run_stream(
    run_id: int,
    after: int = Query(0, ge=0),
    last_event_id: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Живая лента шагов агента (SSE). При переподключении браузер сам присылает Last-Event-ID, и поток продолжается."""
    if await session.get(Run, run_id) is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    resume = int(last_event_id) if last_event_id and last_event_id.isdigit() else after

    async def sse() -> AsyncIterator[str]:
        async for event in service.stream_events(SessionLocal, run_id, resume):
            if event is None:
                yield ": ping\n\n"
                continue
            identifier = f"id: {event['seq']}\n" if "seq" in event else ""
            yield f"{identifier}data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{run_id}/suppliers")
async def run_suppliers(run_id: int, session: AsyncSession = Depends(get_session)) -> dict:
    """Поставщики запуска, отсортированные по оценке, с объяснением и рекомендацией «с кем связаться»."""
    payload, _ = await build_run_payload(session, run_id)
    return payload


async def build_run_payload(session: AsyncSession, run_id: int) -> tuple[dict, Run]:
    """Everything the result page and the export show for one search; everything derived is computed here, on read."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Запуск не найден")
    suppliers = (
        await session.execute(
            select(Supplier).join(RunSupplier, RunSupplier.supplier_id == Supplier.id)
            .where(RunSupplier.run_id == run_id).order_by(RunSupplier.found_at)
        )
    ).scalars().all()
    fields = (
        await session.execute(
            # поля, найденные этим запуском и раньше: найденное позже не меняет результаты прошлого запуска
            select(SupplierField).where(
                SupplierField.supplier_id.in_([s.id for s in suppliers]),
                or_(SupplierField.first_run_id.is_(None), SupplierField.first_run_id <= run_id),
            ).order_by(SupplierField.id)
        )
    ).scalars().all() if suppliers else []
    category = await session.get(Category, run.category_id) if run.category_id else None
    synonyms = tuple(category.synonyms or []) if category else ()
    product = product_stems((run.category_name, *synonyms))
    by_supplier: dict[int, dict[str, list[SupplierField]]] = {}
    for f in fields:
        by_supplier.setdefault(f.supplier_id, {}).setdefault(f.field, []).append(f)
    for sid, per_field in by_supplier.items():
        # данные прошлых запусков проверяем по нынешним правилам: прячем не-адреса, порог доставки вместо
        # минимального заказа, цены на другие товары и дубли одного и того же
        if "address" in per_field:
            per_field["address"] = [f for f in per_field["address"] if address_problem(f.value) is None]
        for name, items in per_field.items():
            items = [f for f in items if value_supported_by_quote(name, f.value, f.quote)
                     and fact_problem(name, f.value, f.quote, product) is None]
            keep = set(collapse_duplicates(name, [f.value for f in items]))
            per_field[name] = [f for f in items if f.value in keep]
        by_supplier[sid] = {name: items for name, items in per_field.items() if items}

    primary = next((r["id"] for r in run.regions if r["role"] == "primary"), None)
    ctx = RankContext(
        category=run.category_name,
        synonyms=synonyms,
        primary_region_id=primary,
        neighbor_region_ids=tuple(get_locations().regions[primary].neighbors) if primary else (),
    )
    infos = [
        SupplierInfo(
            id=s.id, domain=s.domain, name=s.name, inn=s.inn,
            fields={name: [FieldInfo(f.value, f.source_url) for f in items] for name, items in by_supplier.get(s.id, {}).items()},
        )
        for s in suppliers
    ]
    ranking = {r.supplier_id: r for r in rank_suppliers(infos, ctx)}
    suppliers = [s for s in suppliers if s.id in ranking]  # без подтверждённого товара поставщика не показываем
    items = [
        {
            "id": s.id, "domain": s.domain, "website": s.website, "name": s.name, "inn": s.inn, "note": s.note,
            # «не найдено на сайте» считаем по нынешним полям: поле могло найтись позже изучения сайта или в другом запуске
            "research": {**s.research, "missing": missing_fields(set(by_supplier.get(s.id, {})))} if s.research else None,
            "fields": {
                name: [{"value": f.value, "quote": f.quote, "source_url": f.source_url, "verified_at": f.verified_at} for f in values]
                for name, values in by_supplier.get(s.id, {}).items()
            },
            "ranking": asdict(ranking[s.id]),
            "commerce": commerce_payload(
                [(f.value, f.source_url) for f in by_supplier.get(s.id, {}).get("price_hint", [])],
                [(f.value, f.source_url) for f in by_supplier.get(s.id, {}).get("moq", [])],
                (run.category_name, *ctx.synonyms),
            ),
        }
        for s in suppliers
    ]
    finished = run.finished_at.isoformat() if run.finished_at else None
    for item in items:
        # crawl time of the supplier's site; old records without it fall back to the search finish date
        item["collected_at"] = (item["research"] or {}).get("researched_at") or finished
    items.sort(key=lambda item: item["ranking"]["rank"])
    return {"suppliers": items, "recommended_ids": [i["id"] for i in items if i["ranking"]["recommended"]]}, run


@router.get("/{run_id}/export")
async def export_run(
    run_id: int,
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    ids: str | None = Query(None, max_length=200, description="supplier ids in the wanted order, comma separated; all suppliers when absent"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The result of a search as a file: the whole list or the chosen suppliers, in the given (table) order."""
    payload, run = await build_run_payload(session, run_id)
    items = payload["suppliers"]
    if ids:
        wanted = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        by_id = {item["id"]: item for item in items}
        items = [by_id[i] for i in dict.fromkeys(wanted) if i in by_id]
    run_info = {"category": run.category_name, "location": run.location_label, "include_neighbors": run.include_neighbors,
                "created_at": run.created_at.isoformat()}
    rows = export_rows(run_info, items)
    if format == "csv":
        body, media = build_csv(HEADERS, rows), "text/csv; charset=utf-8"
    else:
        body, media = build_xlsx(HEADERS, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    name = file_name(run_info, format)
    return Response(content=body, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"suppliers.{format}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    })
