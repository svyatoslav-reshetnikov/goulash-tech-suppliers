"""Builds the frontend test fixture (the same JSON shape as GET /api/runs/{id}/suppliers) from the stored search fixture.

    python -m scripts.make_frontend_fixture   (run from backend/)

No database and no paid run: ranking and commerce are computed by the same code the API uses.
"""

import json
from dataclasses import asdict
from pathlib import Path

from app.locations import get_locations
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.ranking.commerce import commerce_payload

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/tests/fixtures/search_rice_moscow.json"
TARGET = ROOT / "frontend/src/test/fixtures/search-rice-moscow.json"


def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    regions = get_locations().regions
    primary = next(r["id"] for r in data["run"]["regions"] if r["role"] == "primary")
    ctx = RankContext(category="Рис для суши", synonyms=("Рис",), primary_region_id=primary, neighbor_region_ids=tuple(regions[primary].neighbors))
    infos = [
        SupplierInfo(
            id=s["id"], domain=s["domain"], name=s["name"], inn=s["inn"],
            fields={k: [FieldInfo(v["value"], v["source_url"]) for v in vs] for k, vs in s["fields"].items()},
            pages_studied=len(s["research"]["pages"]) if s["research"] else None,
        )
        for s in data["suppliers"]
    ]
    ranking = {r.supplier_id: asdict(r) for r in rank_suppliers(infos, ctx)}
    phrases = (ctx.category, *ctx.synonyms)
    items = []
    for s in data["suppliers"]:
        fields = s["fields"]
        items.append({
            **{k: s[k] for k in ("id", "domain", "website", "name", "inn", "note", "research", "fields")},
            "ranking": ranking[s["id"]],
            "status": {"value": "new", "updated_at": None},
            "collected_at": (s["research"] or {}).get("researched_at") or data["run"]["finished_at"],
            "commerce": commerce_payload(
                [(v["value"], v["source_url"]) for v in fields.get("price_hint", [])],
                [(v["value"], v["source_url"]) for v in fields.get("moq", [])], phrases,
            ),
        })
    items.sort(key=lambda item: item["ranking"]["rank"])
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps({"suppliers": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(items)} suppliers -> {TARGET.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
