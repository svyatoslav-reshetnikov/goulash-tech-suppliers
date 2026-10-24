from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from app.locations import get_locations

router = APIRouter(prefix="/locations", tags=["локации"])


@router.get("")
def search(q: str = Query("", max_length=100), limit: int = Query(10, ge=1, le=30)) -> dict:
    """Автодополнение: города и регионы России по началу названия, синониму или сокращению."""
    return {"results": [asdict(m) for m in get_locations().search(q, limit)]}


@router.get("/resolve")
def resolve(
    kind: str = Query(..., pattern="^(city|region)$"),
    key: str = Query(..., max_length=200),
    neighbors: bool = False,
) -> dict:
    """Что именно выбрано и в каких регионах искать поставщиков (выбранный и, при желании, соседние)."""
    locations = get_locations()
    selected = locations.resolve(kind, key)
    if selected is None:
        raise HTTPException(status_code=404, detail="Локация не найдена")
    return {
        "selected": asdict(selected),
        "regions": [
            {"id": region.id, "name": region.name, "role": role}
            for region, role in locations.expand(selected.region_id, neighbors)
        ],
    }
