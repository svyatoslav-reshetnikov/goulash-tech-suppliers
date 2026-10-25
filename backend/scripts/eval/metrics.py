"""Метрики качества по результатам прогона и ручной разметке. Без сети и базы: проверяется тестами."""

import csv
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).parent
LABELS = HERE / "labels.csv"  # ручная разметка: хранится в репозитории, чтобы её не повторять
OUT_DIR = HERE / "out"  # результаты прогонов (не в репозитории)
LABEL_COLUMNS = ["case_id", "domain", "name", "website", "is_supplier", "has_product", "in_region", "comment"]
# поля, по которым закупщик решает, звонить ли: доля поставщиков, у которых они нашлись
COVERAGE_FIELDS = ["phone", "price_hint", "moq", "delivery_terms", "regions", "certificates"]


def _flag(value: str | None) -> bool | None:
    value = (value or "").strip().lower()
    if value in ("1", "да", "yes", "y", "+"):
        return True
    if value in ("0", "нет", "no", "n", "-"):
        return False
    return None  # не размечено


def read_labels(path: Path) -> dict[tuple[str, str], dict]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {(row["case_id"], row["domain"]): row for row in csv.DictReader(f)}


def merge_labels(path: Path, results: list[dict]) -> int:
    """Добавляет в файл разметки новых поставщиков с пустыми оценками; уже размеченное не трогает. Возвращает число новых."""
    labels = read_labels(path)
    added = 0
    for case in results:
        for supplier in case["suppliers"]:
            key = (case["id"], supplier["domain"])
            if key not in labels:
                labels[key] = {"case_id": key[0], "domain": key[1], "name": supplier["name"], "website": supplier["website"]}
                added += 1
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        for row in sorted(labels.values(), key=lambda r: (r["case_id"], r["domain"])):
            writer.writerow({c: row.get(c, "") for c in LABEL_COLUMNS})
    return added


@dataclass
class CaseScore:
    case_id: str
    found: int
    labeled: int
    relevant: int
    in_region: int
    coverage: dict[str, float]
    cost_rub: float
    seconds: float
    stop_reason: str

    @property
    def precision(self) -> float | None:
        """Доля настоящих поставщиков нужного продукта среди размеченных."""
        return self.relevant / self.labeled if self.labeled else None

    @property
    def rub_per_relevant(self) -> float | None:
        return round(self.cost_rub / self.relevant, 1) if self.relevant else None


def score_case(case: dict, labels: dict[tuple[str, str], dict]) -> CaseScore:
    suppliers = case["suppliers"]
    labeled = relevant = in_region = 0
    for supplier in suppliers:
        row = labels.get((case["id"], supplier["domain"]), {})
        is_supplier, has_product = _flag(row.get("is_supplier")), _flag(row.get("has_product"))
        if is_supplier is None or has_product is None:
            continue
        labeled += 1
        if is_supplier and has_product:
            relevant += 1
            in_region += bool(_flag(row.get("in_region")))
    coverage = {
        name: round(sum(bool(s["fields"].get(name)) for s in suppliers) / len(suppliers), 2) if suppliers else 0.0
        for name in COVERAGE_FIELDS
    }
    stats = case.get("stats") or {}
    return CaseScore(case["id"], len(suppliers), labeled, relevant, in_region, coverage,
                     float(stats.get("cost_rub", 0)), float(stats.get("seconds", 0)), case.get("stop_reason", ""))


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def report(scores: list[CaseScore]) -> str:
    """Таблица в Markdown: по каждому случаю и итог."""
    head = ["случай", "найдено", "размечено", "точность", "в регионе", *COVERAGE_FIELDS, "₽", "₽ за релевантного", "с", "остановка"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for s in scores:
        lines.append("| " + " | ".join([
            s.case_id, str(s.found), str(s.labeled), _pct(s.precision),
            _pct(s.in_region / s.relevant if s.relevant else None), *(_pct(s.coverage[f]) for f in COVERAGE_FIELDS),
            f"{s.cost_rub:.0f}", "—" if s.rub_per_relevant is None else f"{s.rub_per_relevant:.0f}", f"{s.seconds:.0f}", s.stop_reason,
        ]) + " |")
    found = sum(s.found for s in scores)
    labeled, relevant = sum(s.labeled for s in scores), sum(s.relevant for s in scores)
    cost = sum(s.cost_rub for s in scores)
    coverage = {f: (sum(s.coverage[f] * s.found for s in scores) / found if found else 0.0) for f in COVERAGE_FIELDS}
    lines.append("| " + " | ".join([
        "**итого**", str(found), str(labeled), _pct(relevant / labeled if labeled else None),
        _pct(sum(s.in_region for s in scores) / relevant if relevant else None), *(_pct(coverage[f]) for f in COVERAGE_FIELDS),
        f"{cost:.0f}", "—" if not relevant else f"{cost / relevant:.0f}", f"{sum(s.seconds for s in scores):.0f}", "",
    ]) + " |")
    if labeled < found:
        lines.append(f"\nНе размечено поставщиков: {found - labeled}. Точность считается только по размеченным.")
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
                     f"взято из базы {stats.get('reused', 0)}")
        for item in problems:
            if item["type"] == "fields_rejected":
                reasons = "; ".join(f"{r.get('поле')}: {r.get('причина', '')[:80]}" for r in item["rejected"])
                lines.append(f"- {item['domain']}: отклонены поля — {reasons}")
            else:
                lines.append(f"- {item['message']}")
        for site in checked:
            q = site.get("qualification") or {}
            if q.get("error"):
                lines.append(f"- {site['domain']}: сбой проверки — {q['error']}")
    return "\n".join(lines).strip()
