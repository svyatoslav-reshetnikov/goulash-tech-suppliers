"""Отчёт по прогону eval: сколько поставщиков найдено, у скольких есть ключевые данные, что отсеяно и почему. Без сети и базы."""

from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
OUT_DIR = HERE / "out"  # результаты прогонов (не в репозитории)
# поля, по которым закупщик решает, звонить ли: доля поставщиков, у которых они нашлись
COVERAGE_FIELDS = ["phone", "price_hint", "moq", "delivery_terms", "regions", "certificates"]


@dataclass
class CaseScore:
    case_id: str
    found: int
    coverage: dict[str, float]
    seconds: float
    stop_reason: str


def score_case(case: dict) -> CaseScore:
    suppliers = case["suppliers"]
    coverage = {
        name: round(sum(bool(s["fields"].get(name)) for s in suppliers) / len(suppliers), 2) if suppliers else 0.0
        for name in COVERAGE_FIELDS
    }
    stats = case.get("stats") or {}
    return CaseScore(case["id"], len(suppliers), coverage, float(stats.get("seconds", 0)), case.get("stop_reason", ""))


def _pct(value: float) -> str:
    return f"{round(value * 100)}%"


def report(scores: list[CaseScore]) -> str:
    """Таблица в Markdown: по каждому случаю и итог."""
    head = ["случай", "найдено", *COVERAGE_FIELDS, "с", "остановка"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in scores:
        lines.append("| " + " | ".join([s.case_id, str(s.found), *(_pct(s.coverage[f]) for f in COVERAGE_FIELDS),
                                         f"{s.seconds:.0f}", s.stop_reason]) + " |")
    found = sum(s.found for s in scores)
    coverage = {f: (sum(s.coverage[f] * s.found for s in scores) / found if found else 0.0) for f in COVERAGE_FIELDS}
    lines.append("| " + " | ".join(["**итого**", str(found), *(_pct(coverage[f]) for f in COVERAGE_FIELDS),
                                     f"{sum(s.seconds for s in scores):.0f}", ""]) + " |")
    return "\n".join(lines)


def problems_report(results: list[dict]) -> str:
    """Что отсеяно и отклонено: сайты, не попавшие в результат, и отклонённые поля сохранённых поставщиков."""
    lines: list[str] = []
    for case in results:
        problems = case.get("problems") or []
        stats = case.get("stats") or {}
        checked = case.get("checked_sites") or []
        if not problems and not checked:
            continue
        lines.append(f"\n### {case['id']}: проверено сайтов {len(checked)}, отсеяно {stats.get('rejected', 0)}, "
                     f"взято из базы {stats.get('reused', 0)}, сбоев поиска {stats.get('search_errors', 0)}")
        for item in problems:
            if item["type"] == "search_error":
                rid = f" (request-id {item['request_id']})" if item.get("request_id") else ""
                lines.append(f"- {item['message']}{rid}")
            elif item["type"] == "fields_rejected":
                reasons = "; ".join(
                    f"{r.get('поле')}" + (f" «{r['значение'][:60]}»" if r.get("значение") else "") + f": {r.get('причина', '')[:80]}"
                    for r in item["rejected"]
                )
                lines.append(f"- {item['domain']}: отклонены поля — {reasons}")
            else:
                lines.append(f"- {item['message']}")
        for site in checked:
            q = site.get("qualification") or {}
            if q.get("error"):
                lines.append(f"- {site['domain']}: сбой проверки — {q['error']}")
    return "\n".join(lines).strip()
