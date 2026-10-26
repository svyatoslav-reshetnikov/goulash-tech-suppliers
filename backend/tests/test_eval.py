"""Отчёт eval: покрытие ключевых полей и разбор отказов (без запуска агента)."""

from scripts.eval.metrics import report, score_case


def result(case_id: str, *suppliers: tuple[str, dict]) -> dict:
    return {"id": case_id, "stop_reason": "target_reached", "stats": {"seconds": 120},
            "suppliers": [{"domain": d, "website": f"https://{d}/", "name": d, "fields": f} for d, f in suppliers]}


PHONE = {"phone": [{"value": "+7 (843) 555-01-23"}]}
PRICED = {**PHONE, "price_hint": [{"value": "520 руб/кг"}]}


def test_report_shows_found_suppliers_and_field_coverage_without_money():
    score = score_case(result("mozzarella", ("a.test", PHONE), ("b.test", PRICED), ("c.test", {})))
    assert score.found == 3 and score.coverage["phone"] == 0.67 and score.coverage["price_hint"] == 0.33
    table = report([score, score_case(result("empty"))])
    assert "| mozzarella | 3 | 67% | 33% |" in table and "| empty | 0 |" in table and "**итого**" in table and "₽" not in table


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


def test_problems_report_shows_the_rejected_value():
    from scripts.eval.metrics import problems_report

    case = result("x", ("a.test", PHONE))
    case["problems"] = [{"type": "fields_rejected", "domain": "a.test",
                         "rejected": [{"поле": "price_hint", "значение": "от 1 шт. 15,54 ₽", "причина": "значение не следует из цитаты"}]}]
    assert "price_hint «от 1 шт. 15,54 ₽»: значение не следует" in problems_report([case])


def test_problems_report_lists_search_failures_with_request_id():
    from scripts.eval.metrics import problems_report

    case = result("shrimp")
    case["stats"]["search_errors"] = 1
    case["problems"] = [{"type": "search_error", "message": "Сбой: поиск «креветка оптом» — HTTP 429, код 8, не ответил",
                         "request_id": "bcf8209f", "status": 429, "code": "8"}]
    text = problems_report([case])
    assert "сбоев поиска 1" in text and "HTTP 429, код 8, не ответил (request-id bcf8209f)" in text
