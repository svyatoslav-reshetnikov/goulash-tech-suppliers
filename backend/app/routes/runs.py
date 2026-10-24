import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.validation import SUPPLIER_TYPES, address_problem, collapse_duplicates
from app.config import get_settings
from app.db.models import Category, Run, RunEvent, RunSupplier, Supplier, SupplierField
from app.db.session import SessionLocal, get_session
from app.locations import get_locations
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.runs import service

router = APIRouter(prefix="/runs", tags=["запуски"])


class RunCreate(BaseModel):
    category_id: int
    location_kind: str | None = Field(None, pattern="^(city|region)$")
    location_key: str | None = Field(None, max_length=200)
    include_neighbors: bool = False
    supplier_type: str | None = None


def _run_dict(run: Run) -> dict:
    return {
        "id": run.id, "status": run.status, "stop_reason": run.stop_reason, "category": run.category_name,
        "location": run.location_label, "include_neighbors": run.include_neighbors, "regions": run.regions,
        "supplier_type": run.supplier_type, "summary": run.summary, "error": run.error, "stats": run.stats,
        "created_at": run.created_at, "started_at": run.started_at, "finished_at": run.finished_at,
    }


@router.post("", status_code=202)
async def create_run(body: RunCreate, session: AsyncSession = Depends(get_session)) -> dict:
    category = await session.get(Category, body.category_id)
    if category is None or category.is_group or category.status != "active":
        raise HTTPException(status_code=404, detail="Категория не найдена")
    if body.supplier_type and body.supplier_type not in SUPPLIER_TYPES:
        raise HTTPException(status_code=422, detail=f"Тип поставщика: {', '.join(sorted(SUPPLIER_TYPES))}")

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

    limit = get_settings().agent_max_runs_per_day
    if await service.runs_last_day(session) >= limit:
        raise HTTPException(status_code=429, detail=f"Достигнут суточный лимит запусков ({limit}): каждый запуск платный. Попробуйте завтра")
    if await service.active_runs(session) >= service.MAX_ACTIVE_RUNS:
        raise HTTPException(status_code=429, detail="Уже идут другие запуски: дождитесь их завершения")
    run = Run(category_id=category.id, category_name=category.name, location_kind=body.location_kind,
              location_key=body.location_key, location_label=label, include_neighbors=body.include_neighbors,
              regions=regions, supplier_type=body.supplier_type, status="queued")
    session.add(run)
    await session.commit()
    await service.start_run(run.id)
    return {"run_id": run.id}


@router.get("")
async def list_runs(limit: int = Query(20, ge=1, le=200), session: AsyncSession = Depends(get_session)) -> dict:
    rows = (await session.execute(select(Run).order_by(Run.id.desc()).limit(limit))).scalars()
    return {"runs": [_run_dict(r) for r in rows]}


@router.get("/previous")
async def previous_run(
    category_id: int,
    location_kind: str | None = Query(None, pattern="^(city|region)$"),
    location_key: str | None = Query(None, max_length=200),
    include_neighbors: bool = False,
    supplier_type: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Последний такой же поиск (те же категория, место, соседние регионы и тип поставщика), если он уже был: идёт или завершён.
    Неудавшиеся запуски не считаются: их стоит повторить."""
    query = select(Run).where(
        Run.category_id == category_id,
        Run.include_neighbors == include_neighbors,
        Run.status.in_(("queued", "running", "done")),
        Run.location_kind.is_(None) if location_kind is None else Run.location_kind == location_kind,
        Run.location_key.is_(None) if location_key is None else Run.location_key == location_key,
        Run.supplier_type.is_(None) if not supplier_type else Run.supplier_type == supplier_type,
    )
    run = (await session.execute(query.order_by(Run.id.desc()).limit(1))).scalar_one_or_none()
    return {"run": _run_dict(run) if run else None}


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
            select(SupplierField).where(SupplierField.supplier_id.in_([s.id for s in suppliers])).order_by(SupplierField.id)
        )
    ).scalars().all() if suppliers else []
    by_supplier: dict[int, dict[str, list[SupplierField]]] = {}
    for f in fields:
        by_supplier.setdefault(f.supplier_id, {}).setdefault(f.field, []).append(f)
    for per_field in by_supplier.values():  # данные прошлых запусков: прячем не-адреса и дубли одного и того же
        if "address" in per_field:
            per_field["address"] = [f for f in per_field["address"] if address_problem(f.value) is None]
        for name, items in per_field.items():
            keep = set(collapse_duplicates(name, [f.value for f in items]))
            per_field[name] = [f for f in items if f.value in keep]

    category = await session.get(Category, run.category_id) if run.category_id else None
    primary = next((r["id"] for r in run.regions if r["role"] == "primary"), None)
    ctx = RankContext(
        category=run.category_name,
        synonyms=tuple(category.synonyms or []) if category else (),
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
    items = [
        {
            "id": s.id, "domain": s.domain, "website": s.website, "name": s.name, "inn": s.inn, "note": s.note,
            "research": s.research,
            "fields": {
                name: [{"value": f.value, "quote": f.quote, "source_url": f.source_url, "verified_at": f.verified_at} for f in values]
                for name, values in by_supplier.get(s.id, {}).items()
            },
            "ranking": asdict(ranking[s.id]),
        }
        for s in suppliers
    ]
    items.sort(key=lambda item: item["ranking"]["rank"])
    return {"suppliers": items, "recommended_ids": [i["id"] for i in items if i["ranking"]["recommended"]]}
