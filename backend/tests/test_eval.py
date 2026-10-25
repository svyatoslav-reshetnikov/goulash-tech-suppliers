"""Измерение качества: разметка и метрики (без запуска агента)."""

import pytest

from scripts.eval.metrics import merge_labels, read_labels, report, score_case


def result(case_id: str, *suppliers: tuple[str, dict], cost: float = 90.0) -> dict:
    return {"id": case_id, "stop_reason": "target_reached", "stats": {"cost_rub": cost, "seconds": 120},
            "suppliers": [{"domain": d, "website": f"https://{d}/", "name": d, "fields": f} for d, f in suppliers]}


PHONE = {"phone": [{"value": "+7 (843) 555-01-23"}]}
PRICED = {**PHONE, "price_hint": [{"value": "520 руб/кг"}]}


def test_labels_file_gets_new_suppliers_and_keeps_existing_marks(tmp_path):
    path = tmp_path / "labels.csv"
    first = [result("mozzarella", ("a.test", PHONE), ("b.test", PRICED))]
    assert merge_labels(path, first) == 2
    text = path.read_text(encoding="utf-8").replace("a.test,a.test,https://a.test/,,,,", "a.test,a.test,https://a.test/,1,1,0,")
    path.write_text(text, encoding="utf-8")
    assert merge_labels(path, first + [result("salmon", ("c.test", {}))]) == 1  # уже известные не дублируются
    labels = read_labels(path)
    assert labels[("mozzarella", "a.test")]["is_supplier"] == "1" and ("salmon", "c.test") in labels


def test_precision_counts_only_labeled_and_cost_per_relevant_supplier():
    case = result("mozzarella", ("a.test", PHONE), ("b.test", PRICED), ("c.test", {}), cost=90)
    labels = {("mozzarella", "a.test"): {"is_supplier": "1", "has_product": "1", "in_region": "1"},
              ("mozzarella", "b.test"): {"is_supplier": "1", "has_product": "0", "in_region": "1"}}  # c.test не размечен
    score = score_case(case, labels)
    assert (score.found, score.labeled, score.relevant, score.in_region) == (3, 2, 1, 1)
    assert score.precision == 0.5 and score.rub_per_relevant == 90.0
    assert score.coverage["phone"] == pytest.approx(0.67) and score.coverage["price_hint"] == pytest.approx(0.33)
    table = report([score])
    assert "| mozzarella | 3 | 2 | 50% | 100% |" in table and "Не размечено поставщиков: 1" in table and "**итого**" in table


def test_unlabeled_run_has_no_precision_instead_of_zero():
    score = score_case(result("x", ("a.test", PHONE)), {})
    assert score.precision is None and score.rub_per_relevant is None and "| x | 1 | 0 | — |" in report([score])


def test_eval_cases_resolve_to_known_places_and_region_codes():
    import json

    from app.locations import get_locations, yandex_region
    from scripts.eval.metrics import HERE

    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert len(cases) >= 10 and len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert get_locations().resolve(case["location_kind"], case["location_key"]) is not None, case["id"]
        assert yandex_region(case["location_kind"], case["location_key"]) != 225, case["id"]  # у всех мест есть код Яндекса


def test_problems_report_lists_dropped_sites_rejected_fields_and_check_failures():
    from scripts.eval.metrics import problems_report

    case = result("boxes", ("nn.russcarton.ru", PHONE))
    case["stats"].update(rejected=1, reused=0)
    case["checked_sites"] = [{"domain": "a.test", "qualification": {"supplier": "no"}, "saved": False},
                             {"domain": "b.test", "qualification": {"error": "ответ проверки не разобрался"}, "saved": False}]
    case["problems"] = [{"type": "research_skipped", "domain": "a.test", "message": "a.test: не подходит — ресторан"},
                        {"type": "fields_rejected", "domain": "nn.russcarton.ru",
                         "rejected": [{"поле": "name", "причина": "значение не следует из цитаты"}]}]
    text = problems_report([case, result("empty")])
    assert "boxes: проверено сайтов 2, отсеяно 1" in text and "не подходит — ресторан" in text
    assert "nn.russcarton.ru: отклонены поля — name: значение не следует" in text and "b.test: сбой проверки" in text
    assert "empty" not in text
