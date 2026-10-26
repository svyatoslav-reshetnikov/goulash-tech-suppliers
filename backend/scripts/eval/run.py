"""Прогон агента на наборе случаев для измерения качества. ПЛАТНО: каждый случай — полноценный запуск поиска.

    python -m scripts.eval.run --yes                  # все случаи из cases.json
    python -m scripts.eval.run --yes --only nori-ekb  # один случай

Агент работает без базы приложения (хранилище в памяти), поэтому прогон не смешивается с историей запусков.
Результаты: scripts/eval/out/<время>/results.json. Отчёт (что найдено, каких данных не хватает, что отсеяно и почему):

    python -m scripts.eval.score
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from app.agent.loop import run_agent
from app.agent.memory import MemoryPageCache, MemorySink
from app.agent.models import RegionRef, RunRequest
from app.agent.tools import ToolBox
from app.config import get_settings
from app.locations import get_locations, yandex_region
from app.runs.service import default_deps, limits_from

from .metrics import HERE, OUT_DIR

CASES = HERE / "cases.json"
SHOWN = {"search", "search_error", "research_skipped", "research_failed", "supplier_saved", "supplier_rejected", "run_finished"}


def build_request(case: dict, target: int) -> RunRequest:
    locations = get_locations()
    regions: tuple[RegionRef, ...] = ()
    if case.get("location_key"):
        selected = locations.resolve(case["location_kind"], case["location_key"])
        if selected is None:
            raise ValueError(f"{case['id']}: неизвестное место {case['location_key']}")
        regions = tuple(RegionRef(r.name, role, yandex_region("region", r.id)) for r, role in locations.expand(selected.region_id, case.get("include_neighbors", False)))
    return RunRequest(category=case["category"], synonyms=tuple(case.get("synonyms", [])), regions=regions,
                      target=target,
                      search_region=yandex_region(case.get("location_kind"), case.get("location_key")),
                      city=case["location_key"].split("|", 1)[1] if case.get("location_kind") == "city" else None)


async def run_case(case: dict, settings, cache: MemoryPageCache) -> dict:
    request, limits, deps = build_request(case, settings.agent_target_suppliers), limits_from(settings), default_deps(settings)

    problems: list[dict] = []  # почему сайты отсеяны или не сохранены: без этого не разобрать, что пошло не так

    async def emit(type_: str, message: str = "", **data) -> None:
        if type_ in SHOWN and message:
            print(f"  [{case['id']}] {message}", file=sys.stderr)
        if type_ in ("supplier_rejected", "research_skipped", "research_failed"):
            problems.append({"type": type_, "domain": data.get("domain"), "message": message,
                             **({"rejected": data["rejected"]} if data.get("rejected") else {})})
        elif type_ == "search_error":  # код и request-id: по ним видно, лимит это или другое, и есть что передать в поддержку
            problems.append({"type": type_, "message": message,
                             **{k: data.get(k) for k in ("kind", "query", "attempt", "retried", "final", "status", "code", "request_id", "text")}})
        elif type_ == "supplier_saved" and data.get("rejected"):  # сохранён, но часть полей отклонена
            problems.append({"type": "fields_rejected", "domain": data.get("domain"), "rejected": data["rejected"]})

    sink = MemorySink()
    toolbox = ToolBox(request, limits, deps.searcher, deps.fetcher, sink, emit, places=deps.places, cache=cache, llm=deps.llm)
    started = time.monotonic()
    result = await run_agent(request, deps.llm, toolbox, emit, limits)
    stats = {**result.stats, "seconds": round(time.monotonic() - started, 1)}
    suppliers = []
    for domain, draft in sink.suppliers.items():
        fields: dict[str, list[dict]] = {}
        for f in draft.fields:
            fields.setdefault(f.field, []).append({"value": f.value, "quote": f.quote, "source_url": f.source_url})
        research = sink.research.get(domain, {})
        suppliers.append({"domain": domain, "website": draft.website, "name": (draft.values("name") or [domain])[0],
                          "fields": fields, "missing": research.get("missing"), "qualification": research.get("qualification")})
    checked = [{"domain": d, "qualification": q, "saved": d in sink.suppliers} for d, q in toolbox.qualifications.items()]
    return {"id": case["id"], "request": case, "stop_reason": result.stop_reason, "error": result.error,
            "summary": result.summary, "stats": stats, "suppliers": suppliers, "checked_sites": checked, "problems": problems}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--yes", action="store_true", help="подтверждаю, что прогон платный")
    parser.add_argument("--only", action="append", help="id случая (можно несколько раз)")
    parser.add_argument("--cases", type=Path, default=CASES)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    if args.only:
        cases = [c for c in cases if c["id"] in args.only]
    settings = get_settings()
    print(f"Случаев: {len(cases)}. Каждый — полноценный платный запуск поиска.", file=sys.stderr)
    if not args.yes:
        print("Прогон платный. Повторите с --yes.", file=sys.stderr)
        return 1

    out = OUT_DIR / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    cache, results = MemoryPageCache(), []
    for case in cases:
        print(f"{case['id']}: {case['category']}, {case.get('location_key') or 'вся Россия'}", file=sys.stderr)
        try:
            results.append(await run_case(case, settings, cache))
        except Exception as exc:  # сбой одного случая не останавливает остальные
            print(f"  [{case['id']}] сбой: {exc}", file=sys.stderr)
            results.append({"id": case["id"], "request": case, "stop_reason": "error", "error": str(exc)[:300],
                            "summary": None, "stats": {}, "suppliers": []})
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Результаты: {out / 'results.json'}. Отчёт: python -m scripts.eval.score", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
