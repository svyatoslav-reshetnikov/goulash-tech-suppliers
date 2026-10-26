"""Изучение сайтов поставщиков: выбор страниц, извлечение полей моделью, дополнительный поиск по сайту, учёт токенов."""

import asyncio
import json

import pytest

from app.agent.config import Limits
from app.agent.loop import run_agent
from app.agent.memory import MemorySink
from app.agent.models import RegionRef, RunRequest, SearchHit
from app.agent.research import (
    build_extraction_messages,
    classify_link,
    focus_text,
    missing_fields,
    parse_extraction,
    select_pages,
)
from app.agent.tools import ToolBox, tool_schemas
from app.llm import Reply, ToolCall
from tests.test_agent import QUALIFIED, FakeFetcher, FakeSearch, ScriptedLLM, is_qualify, page

BASE = "https://syrmaster-kazan.test"
HOME_HTML = f"""<html><head><title>СырМастер</title></head><body><h1>ООО «СырМастер Казань»</h1>
<p>Производим моцареллу для пиццы и сыр для роллов.</p>
<nav><a href="{BASE}/contacts">Контакты</a><a href="{BASE}/about">О компании</a><a href="{BASE}/delivery">Доставка и оплата</a>
<a href="{BASE}/price">Прайс-лист</a><a href="{BASE}/docs/certs">Сертификаты</a><a href="{BASE}/blog/1">Блог</a></nav></body></html>"""
PAGES = {
    f"{BASE}/": HOME_HTML,
    f"{BASE}/contacts": "<html><body><p>Телефон: +7 (843) 555-01-23</p><p>Email: sales@syrmaster-kazan.test</p>"
                        "<p>Адрес: г. Казань, ул. Промышленная, 12</p><p>ИНН 7707083893</p></body></html>",
    f"{BASE}/about": "<html><body><p>Компания основана в 2012 году, работаем с пиццериями Татарстана.</p></body></html>",
    f"{BASE}/delivery": "<html><body><p>Доставка по Татарстану бесплатно при заказе от 30 000 руб.</p>"
                        "<p>Минимальный заказ — от 20 кг.</p></body></html>",
    f"{BASE}/price": "<html><body><p>Моцарелла для пиццы 45% — 520 руб/кг</p></body></html>",
    f"{BASE}/docs/certs": "<html><body><p>Сертификат соответствия ХАССП № 123</p></body></html>",
    f"{BASE}/blog/1": "<html><body><p>Новости</p></body></html>",
    f"{BASE}/hidden/opt": "<html><body><p>Моцарелла для пиццы оптом.</p><p>Минимальный заказ — от 20 кг.</p><p>Цена: 520 руб/кг</p></body></html>",
    f"{BASE}/hidden/cert": "<html><body><p>Сертификат соответствия ХАССП № 123</p></body></html>",
}
REQUEST = RunRequest(category="Моцарелла", synonyms=("Сыр моцарелла",),
                     regions=(RegionRef("Республика Татарстан", "primary"),), target=1)
WIDE = RunRequest(category="Моцарелла", synonyms=("Сыр моцарелла",), regions=REQUEST.regions, target=5)  # несколько сайтов за вызов


def entry(field, value, quote, path="/"):
    return {"field": field, "value": value, "quote": quote, "source_url": f"{BASE}{path}"}


FIRST_PASS_FULL = [
    entry("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»"),
    entry("description", "Производим моцареллу для пиццы и сыр для роллов", "Производим моцареллу для пиццы и сыр для роллов."),
    entry("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "/contacts"),
    entry("email", "sales@syrmaster-kazan.test", "sales@syrmaster-kazan.test", "/contacts"),
    entry("address", "г. Казань, ул. Промышленная, 12", "г. Казань, ул. Промышленная, 12", "/contacts"),
    entry("inn", "7707083893", "ИНН 7707083893", "/contacts"),
    entry("regions", "Татарстан", "Доставка по Татарстану бесплатно при заказе от 30 000 руб.", "/delivery"),
    entry("delivery_terms", "бесплатно при заказе от 30 000 руб.", "Доставка по Татарстану бесплатно при заказе от 30 000 руб.", "/delivery"),
    entry("moq", "от 20 кг", "Минимальный заказ — от 20 кг.", "/delivery"),
    entry("price_hint", "520 руб/кг", "Моцарелла для пиццы 45% — 520 руб/кг", "/price"),
    entry("certificates", "Сертификат соответствия ХАССП № 123", "Сертификат соответствия ХАССП № 123", "/docs/certs"),
]


class ExtractLLM:
    """Отвечает на вызовы извлечения заранее заготовленными JSON по очереди; хранит запросы."""

    def __init__(self, answers: list[list[dict] | str]):
        self.answers, self.seen = list(answers), []

    async def chat(self, messages, tools=None, **kwargs):
        if is_qualify(messages):
            return Reply(text=QUALIFIED, prompt_tokens=50, completion_tokens=10)
        self.seen.append(messages)
        answer = self.answers.pop(0)
        text = answer if isinstance(answer, str) else json.dumps({"fields": answer}, ensure_ascii=False)
        return Reply(text=text, prompt_tokens=1000, completion_tokens=200)


class SiteSearch:
    """Поиск по сайту: отдаёт заготовленные страницы на запросы с «site:»; запоминает запросы."""

    def __init__(self, hits_by_word: dict[str, list[str]] | None = None):
        self.queries, self.by_word = [], hits_by_word or {}

    async def search(self, query: str, region=None):
        self.queries.append(query)
        for word, urls in self.by_word.items():
            if word in query:
                return [SearchHit("Страница", u, "", "syrmaster-kazan.test") for u in urls]
        return []


def make_box(llm, searcher=None, pages=None, limits=None, request=REQUEST):
    events = []

    async def emit(type_, message="", **data):
        events.append((type_, message))

    fetcher = FakeFetcher(pages if pages is not None else PAGES)
    sink = MemorySink()
    box = ToolBox(request, limits or Limits(), searcher or SiteSearch(), fetcher, sink, emit, llm=llm)
    return box, fetcher, sink, events


def research(box, *sites):
    result = asyncio.run(box.execute("research_suppliers", json.dumps({"websites": list(sites)})))
    return json.loads(result.text)


# --- выбор страниц и текст ---


def test_classify_and_select_pages_one_per_topic_in_priority_order():
    links = [(f"{BASE}/blog/1", "Блог"), (f"{BASE}/docs/certs", "Сертификаты"), (f"{BASE}/price", "Прайс-лист"),
             (f"{BASE}/delivery", "Доставка и оплата"), (f"{BASE}/about", "О компании"), (f"{BASE}/contacts", "Контакты"),
             (f"{BASE}/contacts/moscow", "Контакты Москвы")]
    assert classify_link(f"{BASE}/x", "Реквизиты") == "contacts" and classify_link(f"{BASE}/blog/1", "Блог") is None
    assert select_pages(links, limit=10) == [f"{BASE}/contacts", f"{BASE}/about", f"{BASE}/delivery", f"{BASE}/price", f"{BASE}/docs/certs"]
    assert select_pages(links, limit=2) == [f"{BASE}/contacts", f"{BASE}/about"]
    assert select_pages(links, limit=5, exclude={f"{BASE}/contacts"})[0] == f"{BASE}/contacts/moscow"
    assert select_pages(links, limit=5, topics=["certs"]) == [f"{BASE}/docs/certs"]


def test_focus_text_keeps_window_around_prices_and_orders_when_page_is_long():
    filler = "Лирика о компании без фактов. " * 120
    text = "Начало страницы. " + filler + "Минимальный заказ — от 20 кг, цена 520 руб/кг. " + filler
    short = focus_text(text, 1500)
    assert len(short) <= 1700 and short.startswith("Начало страницы.") and "от 20 кг" in short and "520 руб/кг" in short
    assert focus_text("коротко", 1500) == "коротко"


def test_parse_extraction_keeps_only_well_formed_known_fields():
    ok = parse_extraction('```json\n{"fields": [{"field": "phone", "value": "1", "quote": "q", "source_url": "u"}, '
                          '{"field": "unknown", "value": "1", "quote": "q", "source_url": "u"}, {"field": "moq", "value": 5}]}\n```')
    assert [e["field"] for e in ok.entries] == ["phone"] and ok.error is None
    for bad in (None, "", "нет json", '{"fields": "строка"}', '{"x": 1}'):
        assert parse_extraction(bad).error
    assert missing_fields({"phone"}) == ["description", "address", "regions", "delivery_terms", "moq", "price_hint", "certificates"]
    assert missing_fields(set())[0] == "phone"


def test_extraction_prompt_names_product_and_focus_fields():
    messages = build_extraction_messages(REQUEST, "=== u ===\nтекст", ["moq", "certificates"])
    user = messages[1]["content"]
    assert "Моцарелла" in user and "Сыр моцарелла" in user and "moq (минимальный заказ)" in user and "certificates (сертификаты)" in user


# --- изучение сайта ---


def test_schema_exposes_research_instead_of_manual_fetch_and_save_when_model_is_available():
    names = lambda tools: [t["function"]["name"] for t in tools]  # noqa: E731
    assert names(tool_schemas(False, research_enabled=True)) == ["search_web", "research_suppliers", "finish"]
    assert "fetch_page" in names(tool_schemas(False)) and "research_suppliers" not in names(tool_schemas(False))


def test_first_pass_opens_all_sections_and_saves_every_required_field():
    llm = ExtractLLM([FIRST_PASS_FULL])
    box, fetcher, sink, events = make_box(llm)
    result = research(box, BASE)["сайты"][0]
    assert result["результат"] == "сохранён" and result["не_найдено_на_сайте"] == [] and result["изучено_страниц"] == 6
    fetched = {u.removeprefix(BASE) for u in fetcher.calls}
    assert {"/", "/contacts", "/about", "/delivery", "/price", "/docs/certs"} <= fetched and "/blog/1" not in fetched
    saved = sink.suppliers["syrmaster-kazan.test"]
    assert {f.field for f in saved.fields} >= {"name", "phone", "email", "address", "description", "regions", "delivery_terms", "moq", "price_hint", "certificates", "inn"}
    assert sink.research["syrmaster-kazan.test"]["missing"] == [] and len(sink.research["syrmaster-kazan.test"]["pages"]) == 6
    assert (box.extra_prompt_tokens, box.extra_completion_tokens) == (1050, 210) and box.counters.researched == 1  # проверка сайта и извлечение
    assert len(llm.seen) == 1 and "site:" not in str(box.searcher.queries) and any(t == "research_done" for t, _ in events)


def test_missing_fields_are_searched_on_the_site_and_extracted_in_a_second_pass():
    first = [e for e in FIRST_PASS_FULL if e["field"] not in {"moq", "price_hint", "certificates"}]
    second_a = [entry("moq", "от 20 кг", "Минимальный заказ — от 20 кг.", "/hidden/opt"), entry("price_hint", "520 руб/кг", "Цена: 520 руб/кг", "/hidden/opt")]
    second_b = [entry("certificates", "ХАССП № 123", "Сертификат соответствия ХАССП № 123", "/hidden/cert")]
    pages = {k: v for k, v in PAGES.items() if k not in (f"{BASE}/price", f"{BASE}/docs/certs")}
    pages[f"{BASE}/delivery"] = "<html><body><p>Доставка по Татарстану бесплатно при заказе от 30 000 руб.</p></body></html>"  # без минимального заказа
    pages[f"{BASE}/"] = HOME_HTML.replace(f'<a href="{BASE}/price">Прайс-лист</a><a href="{BASE}/docs/certs">Сертификаты</a>', "")
    llm = ExtractLLM([first, second_a, second_b])
    search = SiteSearch({"минимальный заказ": [f"{BASE}/hidden/opt"], "сертификаты": [f"{BASE}/hidden/cert"]})
    box, fetcher, sink, events = make_box(llm, search, pages)
    result = research(box, BASE)["сайты"][0]
    assert result["не_найдено_на_сайте"] == [] and box.counters.site_searches == 2
    assert all(q.startswith("site:syrmaster-kazan.test ") for q in search.queries)
    assert "moq (минимальный заказ)" in llm.seen[1][1]["content"] and "certificates (сертификаты)" in llm.seen[2][1]["content"]
    assert {f.field for f in sink.suppliers["syrmaster-kazan.test"].fields} >= {"moq", "price_hint", "certificates"}
    assert any(t == "research_more" and "Ищу по сайту" in m for t, m in events)


def test_fields_absent_on_the_site_are_reported_not_invented_and_searches_are_bounded():
    first = [e for e in FIRST_PASS_FULL if e["field"] in {"name", "phone", "description"}]
    llm = ExtractLLM([first] + [[] for _ in range(6)])
    search = SiteSearch({"": [f"{BASE}/hidden/opt"]})  # любой запрос возвращает одну и ту же страницу
    box, _, sink, _ = make_box(llm, search, limits=Limits(max_site_searches_per_supplier=2))
    result = research(box, BASE)["сайты"][0]
    assert result["результат"] == "сохранён"
    assert set(result["не_найдено_на_сайте"]) >= {"минимальный заказ", "цены", "сертификаты"}  # так и написано, ничего не выдумано
    assert len(search.queries) <= 2 and sink.research["syrmaster-kazan.test"]["missing"]
    assert "moq" not in {f.field for f in sink.suppliers["syrmaster-kazan.test"].fields}


def test_fabricated_quote_from_the_extraction_is_rejected_by_the_same_checks():
    bad = [entry("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»"), entry("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "/contacts"),
           entry("description", "Производим моцареллу для пиццы", "Производим моцареллу для пиццы и сыр для роллов."),
           entry("moq", "от 5 кг", "Минимальный заказ — от 5 кг")]  # такой цитаты на страницах нет
    box, _, sink, _ = make_box(ExtractLLM([bad, [], [], []]))
    result = research(box, BASE)["сайты"][0]
    assert "минимальный заказ" in result["не_найдено_на_сайте"]
    assert "moq" not in {f.field for f in sink.suppliers["syrmaster-kazan.test"].fields}


def test_site_without_confirmed_name_and_contact_is_not_saved():
    box, _, sink, _ = make_box(ExtractLLM([[entry("description", "Производим моцареллу для пиццы и сыр для роллов", "Производим моцареллу для пиццы и сыр для роллов.")], [], [], []]))
    result = research(box, BASE)["сайты"][0]
    assert result["результат"].startswith("не сохранён") and not sink.suppliers and box.counters.saved == 0


def test_bad_sites_unreachable_home_duplicates_and_limits():
    # число изучаемых сайтов не ограничено: неоткрывшийся и повторный сайт стоят не больше одной загрузки
    box, fetcher, sink, _ = make_box(ExtractLLM([FIRST_PASS_FULL]), request=WIDE)
    out = research(box, "https://www.avito.ru/x", "https://missing.test/", BASE)["сайты"]
    assert "не поставщик" in out[0]["результат"] and "не открылась" in out[1]["результат"] and out[2]["результат"] == "сохранён"
    calls = len(fetcher.calls)
    again = research(box, BASE)["сайты"][0]
    assert again["примечание"].startswith("этот сайт уже изучен") and len(fetcher.calls) == calls  # повторно не изучаем
    assert "не открылась" in research(box, "https://one.test/")["сайты"][0]["результат"]  # лимита на число сайтов нет
    assert "websites" in json.loads(asyncio.run(box.execute("research_suppliers", "{}")).text)["ошибка"]


def test_several_sites_are_researched_at_once_but_no_more_than_three():
    pages = {}
    for host in ("a", "b", "c", "d"):
        pages[f"https://{host}.test/"] = f"<html><body><h1>ООО {host}</h1><p>Телефон: +7 (843) 555-01-2{ord(host) - 96}</p></body></html>"
    answers = []
    for host in ("a", "b", "c"):
        answers.append([{"field": "name", "value": f"ООО {host}", "quote": f"ООО {host}", "source_url": f"https://{host}.test/"},
                        {"field": "phone", "value": f"+7 (843) 555-01-2{ord(host) - 96}", "quote": f"Телефон: +7 (843) 555-01-2{ord(host) - 96}", "source_url": f"https://{host}.test/"}])
    llm = ExtractLLM(answers + [[]] * 12)
    box, *_ = make_box(llm, pages=pages, limits=Limits(max_site_searches_per_supplier=0), request=WIDE)
    out = research(box, "https://a.test/", "https://b.test/", "https://c.test/", "https://d.test/")["сайты"]
    assert len(out) == 3 and box.counters.researched == 3  # четвёртый адрес отброшен


# --- цикл и бюджет ---


def tool(name, **args):
    return Reply(text=None, tool_calls=[ToolCall("c", name, json.dumps(args, ensure_ascii=False))], prompt_tokens=100, completion_tokens=20)


def run(llm, box, limits=None, request=REQUEST):
    async def emit(type_, message="", **data):
        pass

    return asyncio.run(run_agent(request, llm, box, emit, limits or Limits()))


def test_loop_with_research_counts_extraction_tokens_in_stats():
    extract = ExtractLLM([FIRST_PASS_FULL])
    box, *_ = make_box(extract)
    main = ScriptedLLM([tool("search_web", query="моцарелла оптом Казань"), tool("research_suppliers", websites=[BASE]), tool("finish", summary="готово")])
    result = run(main, box)
    assert result.stop_reason == "target_reached" and result.stats["suppliers_saved"] == 1 and result.stats["researched"] == 1
    assert result.stats["prompt_tokens"] == 200 + 50 + 1000 and result.stats["completion_tokens"] == 40 + 10 + 200  # две реплики агента, проверка сайта, извлечение


def test_token_budget_includes_tokens_spent_inside_tools():
    request = RunRequest(category="Моцарелла", target=5)
    box, *_ = make_box(ExtractLLM([FIRST_PASS_FULL]), request=request)
    main = ScriptedLLM([tool("research_suppliers", websites=[BASE]), tool("search_web", query="ещё"), tool("search_web", query="ещё")])
    result = run(main, box, Limits(max_total_tokens=1200), request)  # одно извлечение стоит 1200 токенов
    assert result.stop_reason == "token_limit" and result.stats["turns"] == 1


# --- уроки реального запуска ---


def test_value_must_rest_on_the_words_of_the_quote_not_only_on_numbers():
    from app.agent.validation import value_supported_by_quote, words_supported

    value = "Бесплатная доставка курьером при заказе от 11 000 ₽, условия самовывоза"
    assert not value_supported_by_quote("delivery_terms", value, "Сумма для оформления заказа от 11 000 ₽")
    assert value_supported_by_quote("delivery_terms", "бесплатно при заказе от 30 000 руб.", "Доставка по Татарстану бесплатно при заказе от 30 000 руб.")
    assert value_supported_by_quote("moq", "от 20 кг", "Минимальный заказ — от 20 кг.")  # короткое значение: достаточно чисел
    assert words_supported("Поставляем продукты от Калининграда до Владивостока", "Поставляем продукты от Калининграда до Владивостока.")
    assert value_supported_by_quote("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23")  # телефон проверяется по цифрам


def test_same_quote_is_saved_only_once_per_field():
    twice = FIRST_PASS_FULL + [entry("moq", "минимум 20 кг", "минимальный заказ — от 20 кг.", "/delivery")]
    box, _, sink, _ = make_box(ExtractLLM([twice]))
    research(box, BASE)
    moq = [f for f in sink.suppliers["syrmaster-kazan.test"].fields if f.field == "moq"]
    assert len(moq) == 1


class BrokenFetcher(FakeFetcher):
    attempts = 0

    async def fetch(self, url):
        if "broken" in url:
            self.attempts += 1
            raise ConnectionResetError("обрыв соединения")  # не RuntimeError: раньше такое роняло изучение
        return await super().fetch(url)


def test_network_failures_are_reported_in_feed_and_do_not_break_other_sites():
    events = []

    async def emit(type_, message="", **data):
        events.append((type_, message))

    pages = dict(PAGES)
    fetcher = BrokenFetcher(pages)
    box = ToolBox(WIDE, Limits(), SiteSearch(), fetcher, MemorySink(), emit, llm=ExtractLLM([FIRST_PASS_FULL]))
    out = research(box, "https://broken.test/", BASE)["сайты"]
    assert "не открылась" in out[0]["результат"] and "обрыв соединения" in out[0]["результат"] and out[1]["результат"] == "сохранён"
    assert any(t == "research_failed" and "broken.test" in m for t, m in events)
    again = research(box, "https://broken.test/")["сайты"][0]  # повторно недоступный сайт не открываем: шаг не тратится
    assert "примечание" in again and fetcher.attempts == 1


def test_unexpected_exception_inside_one_research_is_isolated():
    class Exploding(ExtractLLM):
        async def chat(self, messages, tools=None, **kwargs):
            raise RuntimeError("модель недоступна")

    box, *_ = make_box(Exploding([]))
    out = research(box, BASE)["сайты"]
    assert "ошибка при изучении" in out[0]["результат"] and "модель недоступна" in out[0]["результат"]


def test_usage_events_report_growing_spend_during_research():
    events = []

    async def emit(type_, message="", **data):
        events.append((type_, data))

    box = ToolBox(REQUEST, Limits(), SiteSearch(), FakeFetcher(dict(PAGES)), MemorySink(), emit, llm=ExtractLLM([FIRST_PASS_FULL]))
    research(box, BASE)
    usage = [d for t, d in events if t == "usage"]
    assert usage and usage[-1]["prompt_tokens"] + usage[-1]["completion_tokens"] > 0
    totals = [d["prompt_tokens"] + d["completion_tokens"] for d in usage]
    assert totals == sorted(totals)


# --- адреса: настоящие данные запуска «Рис для суши», Москва ---

REAL_ADDRESSES = [
    "Московская область, Мытищи, Осташковское шоссе, вл.5, с.1",
    "Москва",
    "127224, Россия, Московская обл., Мытищи, Осташковское шоссе, вл.5, с.1",
]


def test_address_must_have_a_house_number_not_just_a_city():
    from app.agent.validation import normalize_value

    assert normalize_value("address", "Москва")[0] is None
    assert normalize_value("address", "г. Москва")[0] is None
    assert "не адрес" in normalize_value("address", "Москва")[1]
    for good in ("г. Мытищи, ул. Угольная, 6", "Россия, Москва, Коровинское шоссе, 35с4", "123022, г. Москва, Звенигородское шоссе, д. 9/27с1", "ул. Ленина, 5"):
        assert normalize_value("address", good)[0] == good


def test_same_address_in_different_writing_is_collapsed_to_the_fullest():
    from app.agent.validation import address_problem, collapse_duplicates

    real = [a for a in REAL_ADDRESSES if address_problem(a) is None]
    assert collapse_duplicates("address", real) == [REAL_ADDRESSES[2]]
    other = ["г. Казань, ул. Бухарская, 4В", "г. Казань, ул. Бухарская, 6"]
    assert collapse_duplicates("address", other) == other  # разные дома не склеиваются
    assert collapse_duplicates("phone", ["+7 (843) 555-01-23", "+7 (843) 555-01-23 доб. 5"]) == ["+7 (843) 555-01-23", "+7 (843) 555-01-23 доб. 5"]  # только описательные поля


def test_free_delivery_threshold_is_not_a_minimum_order_and_prices_must_be_for_the_product():
    from app.agent.validation import fact_problem, product_stems

    product = product_stems(("Моцарелла", "Сыр моцарелла"))
    gfc = "Бесплатная доставка курьером при заказе от 11 000 ₽, условия самовывоза"  # реальная цитата запуска №5
    assert "бесплатной доставки" in fact_problem("moq", "от 11 000 ₽", gfc, product)
    assert fact_problem("moq", "от 5000 руб.", "При заказе от 5000 руб. доставка бесплатно", product)
    assert fact_problem("moq", "от 10 коробок или от 50.000 рублей", "Минимальный заказ продукции от 10 коробок или от 50.000 рублей!", product) is None
    assert fact_problem("moq", "Самовывоз — от 3 000 ₽", "Самовывоз — от 3 000 ₽. Доставка — от 5 000 ₽, бесплатно", product) is None
    butter = "от 665 рублей за упаковку сливочного масла"  # реальная «цена» в поиске моцареллы
    assert "не на нужный продукт" in fact_problem("price_hint", butter, butter, product)
    assert fact_problem("price_hint", "520 руб/кг", "Моцарелла для пиццы 45% — 520 руб/кг", product) is None
    assert fact_problem("price_hint", "520 руб/кг", "Цена: 520 руб/кг", product, context="моцарелла для пиццы оптом") is None
    assert fact_problem("price_hint", "520 руб/кг", "Цена: 520 руб/кг", []) is None  # продукт неизвестен: не проверяем


def test_threshold_and_foreign_price_are_rejected_when_saving():
    pages = dict(PAGES)
    pages[f"{BASE}/delivery"] = "<html><body><p>Бесплатная доставка курьером при заказе от 11 000 ₽.</p><p>Сливочное масло от 665 рублей за упаковку.</p></body></html>"
    answer = [e for e in FIRST_PASS_FULL if e["field"] not in {"moq", "price_hint"}] + [
        entry("moq", "от 11 000 ₽", "Бесплатная доставка курьером при заказе от 11 000 ₽.", "/delivery"),
        entry("price_hint", "от 665 рублей за упаковку", "Сливочное масло от 665 рублей за упаковку.", "/delivery"),
    ]
    box, _, sink, events = make_box(ExtractLLM([answer] + [[] for _ in range(4)]), SiteSearch({}), pages)
    research(box, BASE)
    saved = {f.field for f in sink.suppliers["syrmaster-kazan.test"].fields}
    assert "moq" not in saved and "price_hint" not in saved and {"phone", "address"} <= saved


def test_phone_is_required_even_when_email_is_found():
    assert missing_fields({"email"})[0] == "phone"
    assert "phone" not in missing_fields({"phone"})


# выдуманные значения рядом с настоящей цитатой: раньше проверялись только числа, и всё это принималось
FABRICATED = [
    ("name", "ООО «Сырная Империя»", "Контакты"),
    ("supplier_type", "производитель", "Контакты"),
    ("certificates", "ХАССП", "Доставка по городу"),
    ("regions", "вся Россия", "Каталог продукции"),
    ("regions", "Татарстан", "Москва и область"),
    ("moq", "от 10 кг", "Скидка 10% новым клиентам"),
    ("price_hint", "моцарелла 450 руб/кг", "Сыр пармезан 450 руб/кг"),
    ("delivery_terms", "бесплатная доставка", "Самовывоз со склада"),
    ("description", "производитель моцареллы", "Мы продаём кофе"),
    ("assortment", "моцарелла", "Мы продаём кофе"),
]
SUPPORTED = [
    ("name", "ООО «Инлут»", "Производитель бренда Фарфаторре ООО Инлут"),
    ("name", "Торговый дом Ромашка", "ТД «Ромашка», Казань"),
    ("supplier_type", "производитель", "Собственное производство сыров с 2012 года"),
    ("supplier_type", "оптовик", "Продаём оптом и в розницу"),
    ("supplier_type", "дистрибьютор", "Официальный дистрибьютор Unagrande"),
    ("regions", "Татарстан", "Доставка по Татарстану"),
    ("regions", "вся Россия", "Отгружаем по всей России транспортными компаниями"),
    ("assortment", "сыры", "Сыр моцарелла, сулугуни"),
    ("moq", "от 20 кг", "Минимальный заказ — от 20 кг."),
    ("description", "Производство мясных полуфабрикатов", "Мы занимаемся производством мясных полуфабрикатов"),
]


@pytest.mark.parametrize("field,value,quote", FABRICATED)
def test_fabricated_value_next_to_a_real_quote_is_rejected(field, value, quote):
    from app.agent.validation import normalize_value, value_supported_by_quote

    normalized, _ = normalize_value(field, value)
    assert normalized is None or not value_supported_by_quote(field, normalized, quote)


@pytest.mark.parametrize("field,value,quote", SUPPORTED)
def test_value_written_in_other_words_or_cases_is_accepted(field, value, quote):
    from app.agent.validation import normalize_value, value_supported_by_quote

    normalized, _ = normalize_value(field, value)
    assert normalized is not None and value_supported_by_quote(field, normalized, quote)


def test_too_short_quote_confirms_nothing():
    from app.agent.validation import quote_in_text

    assert not quote_in_text("ООО", "ООО Ромашка") and quote_in_text("ООО Ромашка", "ООО «Ромашка», Казань")


# --- проверка сайта до изучения, повторное использование, обрезанные ответы, таймауты ---


class JudgingLLM(ExtractLLM):
    """Проверка сайта отвечает заданным вердиктом; извлечение — заготовленными ответами."""

    def __init__(self, verdict: str, answers=()):
        super().__init__(list(answers))
        self.verdict, self.qualify_calls = verdict, 0

    async def chat(self, messages, tools=None, **kwargs):
        from tests.test_agent import is_qualify

        if is_qualify(messages):
            self.qualify_calls += 1
            return Reply(text=self.verdict, prompt_tokens=40, completion_tokens=8)
        return await super().chat(messages, tools, **kwargs)


def test_site_that_does_not_sell_the_product_to_business_is_skipped_before_research():
    llm = JudgingLLM('{"supplier": "no", "product": "yes", "reason": "ресторан, продаёт готовую пиццу"}')
    box, fetcher, sink, events = make_box(llm)
    out = research(box, BASE)["сайты"][0]
    assert "не подходит" in out["результат"] and "ресторан" in out["результат"]
    assert fetcher.calls == [f"{BASE}/"] and llm.seen == [] and not sink.suppliers  # только главная, без извлечения
    assert box.counters.rejected == 1 and box.counters.researched == 0
    assert any(t == "research_skipped" for t, _ in events)
    assert "уже изучен" in research(box, BASE)["сайты"][0]["примечание"]  # второй раз не проверяем


def test_half_clear_or_broken_verdict_keeps_the_site_but_fully_unclear_drops_it():
    for verdict in ('{"supplier": "yes", "product": "unclear", "reason": "посуда, коробок не видно"}',
                    '{"supplier": "unclear", "product": "yes", "reason": "не сказано, продают ли бизнесу"}', "не JSON"):
        box, _, sink, _ = make_box(JudgingLLM(verdict, [FIRST_PASS_FULL]))
        assert research(box, BASE)["сайты"][0]["результат"] == "сохранён", verdict
    assert "не разобрался" in sink.research["syrmaster-kazan.test"]["qualification"]["error"]  # сбой проверки записан
    # колбасная база в поиске моцареллы: ни про бизнес, ни про продукт по главной не понять — не изучаем
    box, fetcher, sink, _ = make_box(JudgingLLM('{"supplier": "unclear", "product": "unclear", "reason": ""}'))
    out = research(box, BASE)["сайты"][0]
    assert "не подходит" in out["результат"] and "не понять" in out["результат"] and not sink.suppliers
    assert box.qualifications["syrmaster-kazan.test"]["product"] == "unclear"


def test_recently_researched_supplier_is_reused_without_paying_for_extraction_again():
    sink = MemorySink()
    first, *_ = make_box(ExtractLLM([FIRST_PASS_FULL]))
    first.sink = sink
    research(first, BASE)
    llm = ExtractLLM([])  # заготовленных ответов нет: извлечение упало бы
    second = ToolBox(REQUEST, Limits(), SiteSearch(), FakeFetcher(dict(PAGES)), sink, first.emit, llm=llm)
    out = research(second, BASE)["сайты"][0]
    assert "данные изучения" in out["результат"] and "phone" in out["сохранено_полей"]
    assert second.counters.saved == 1 and second.counters.reused == 1 and llm.seen == []
    stale = ToolBox(REQUEST, Limits(reuse_days=0), SiteSearch(), FakeFetcher(dict(PAGES)), sink, first.emit, llm=ExtractLLM([FIRST_PASS_FULL]))
    sink.research["syrmaster-kazan.test"]["at"] -= 10
    assert research(stale, BASE)["сайты"][0]["результат"] == "сохранён" and stale.counters.reused == 0  # устарело: изучаем заново


def test_truncated_extraction_is_split_in_halves_and_its_tokens_are_counted():
    from app.llm import LLMTruncated

    class Truncating(ExtractLLM):
        truncated = False

        async def chat(self, messages, tools=None, **kwargs):
            from tests.test_agent import is_qualify

            if not is_qualify(messages) and not self.truncated:
                self.truncated = True
                raise LLMTruncated(prompt_tokens=3000, completion_tokens=4096)
            return await super().chat(messages, tools, **kwargs)

    llm = Truncating([FIRST_PASS_FULL[:6], FIRST_PASS_FULL[6:]])
    box, _, sink, _ = make_box(llm, limits=Limits(max_site_searches_per_supplier=0))
    out = research(box, BASE)["сайты"][0]
    assert out["результат"] == "сохранён" and len(llm.seen) == 2  # две половины страниц
    assert box.extra_prompt_tokens == 50 + 3000 + 2000 and box.extra_completion_tokens == 10 + 4096 + 400


def test_slow_site_times_out_alone_and_is_not_retried():
    class SlowFetcher(FakeFetcher):
        async def fetch(self, url):
            if "slow" in url:
                await asyncio.sleep(5)
            return await super().fetch(url)

    box = ToolBox(WIDE, Limits(research_site_timeout=0.3, max_site_searches_per_supplier=0), SiteSearch(), SlowFetcher(dict(PAGES)),
                  MemorySink(), make_box(None)[0].emit, llm=ExtractLLM([FIRST_PASS_FULL]))
    out = research(box, "https://slow.test/", BASE)["сайты"]
    assert "слишком медленно" in out[0]["результат"] and out[1]["результат"] == "сохранён"
    assert "уже изучен" in research(box, "https://slow.test/")["сайты"][0]["примечание"]


def test_no_more_sites_are_researched_than_are_missing_to_the_target():
    request = RunRequest(category="Моцарелла", target=2)
    llm = ExtractLLM([[]] * 6)
    box, *_ = make_box(llm, request=request, limits=Limits(max_site_searches_per_supplier=0))
    out = research(box, "https://a.test/", "https://b.test/", "https://c.test/")
    assert len(out["сайты"]) == 2 and "не хватало 2" in out["примечание"]


def test_early_finish_in_research_mode_points_to_research_tool_not_fetch_page():
    box, *_ = make_box(ExtractLLM([]), request=WIDE)
    refusal = json.loads(asyncio.run(box.execute("finish", '{"summary": "x"}')).text)["ошибка"]
    assert "research_suppliers" in refusal and "fetch_page" not in refusal


def test_search_uses_region_code_but_site_search_does_not():
    request = RunRequest(category="Моцарелла", target=1, search_region=43)
    searcher = FakeSearch([SearchHit("СырМастер", f"{BASE}/", "моцарелла оптом", "syrmaster-kazan.test")])
    box = ToolBox(request, Limits(), searcher, FakeFetcher(dict(PAGES)), MemorySink(), make_box(None)[0].emit,
                  llm=ExtractLLM([FIRST_PASS_FULL[:4], [], [], []]))
    asyncio.run(box.execute("search_web", '{"query": "моцарелла оптом Казань"}'))
    research(box, BASE)
    assert searcher.regions[0] == 43 and all(r is None for r in searcher.regions[1:]) and len(searcher.regions) > 1
    assert box.snippets["syrmaster-kazan.test"].startswith("СырМастер")


def test_yandex_region_codes_for_city_region_and_whole_country():
    from app.locations import yandex_region

    assert yandex_region("city", "RU-KDA|Сочи") == 239  # у города свой код
    assert yandex_region("city", "RU-TA|Набережные Челны") == 43  # нет своего: код региона (столицы)
    assert yandex_region("region", "RU-TA") == 43 and yandex_region("region", "RU-MOS") == 1
    assert yandex_region("region", "RU-SA") == 225 and yandex_region(None, None) == 225  # нет кода: вся Россия


def test_menu_is_left_out_of_page_text_but_its_links_keep_labels():
    result = page(f"{BASE}/", HOME_HTML)
    assert "Прайс-лист" not in result.text and "Производим моцареллу" in result.text
    assert (f"{BASE}/price", "Прайс-лист") in result.links


def test_truncated_reply_in_main_loop_stops_with_tokens_counted():
    from app.llm import LLMTruncated
    from tests.test_agent import tool

    box, *_ = make_box(ExtractLLM([]))
    main = ScriptedLLM([tool("search_web", query="моцарелла"), LLMTruncated(prompt_tokens=700, completion_tokens=2048)])
    result = run(main, box)
    assert result.stop_reason == "llm_error" and "обрезан" in result.error
    assert result.stats["prompt_tokens"] == 100 + 700 and result.stats["completion_tokens"] == 20 + 2048


# --- по итогам второго прогона eval: адрес страницы, телефоны, лимит поиска ---


def test_source_url_without_slash_or_www_finds_the_opened_page():
    box, *_ = make_box(ExtractLLM([]))
    asyncio.run(box._get_page(f"{BASE}/"))
    asyncio.run(box._get_page(f"{BASE}/contacts"))
    fields = [entry("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»"),
              entry("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "/contacts")]
    fields[0]["source_url"] = BASE  # модель не поставила слеш у главной
    fields[1]["source_url"] = "http://www.syrmaster-kazan.test/contacts/"  # и перепутала http, www и слеш
    saved = json.loads(asyncio.run(box.execute("save_supplier", json.dumps({"website": BASE, "fields": fields}))).text)
    assert saved["сохранено"] == ["name", "phone"] and "отклонено" not in saved


def test_phone_with_extension_and_several_numbers_in_one_value():
    from app.agent.validation import normalize_phone, split_phones, value_supported_by_quote

    assert normalize_phone("+7 (495) 133-00-60 доб. 105") == "+7 (495) 133-00-60 доб. 105"
    assert normalize_phone("8 (843) 555-01-23 (доп. 2)") == "+7 (843) 555-01-23 доб. 2"
    assert normalize_phone("+7 (495) 133-00-60, +7 (495) 640-24-94") is None  # два номера сразу — не один телефон
    assert split_phones("+7 (495) 133-00-60, +7 (495) 640-24-94 или 8 800 550 41 83") == [
        "+7 (495) 133-00-60", "+7 (495) 640-24-94", "8 800 550 41 83"]
    assert split_phones("+7 (495) 133-00-60, доб. 105") == ["+7 (495) 133-00-60, доб. 105"]  # добавочный не отдельный номер
    assert value_supported_by_quote("phone", "+7 (495) 133-00-60 доб. 105", "Тел.: +7 (495) 133-00-60, доб. 105")
    assert not value_supported_by_quote("phone", "+7 (495) 133-00-60 доб. 105", "Тел.: +7 (495) 133-00-60")  # добавочный выдуман


def test_several_phones_in_one_value_are_saved_separately():
    pages = dict(PAGES)
    pages[f"{BASE}/contacts"] = "<html><body><p>Телефоны: +7 (843) 555-01-23, +7 (843) 555-01-24 доб. 7</p></body></html>"
    box, _, sink, _ = make_box(ExtractLLM([]), pages=pages)
    asyncio.run(box._get_page(f"{BASE}/contacts"))
    quote = "Телефоны: +7 (843) 555-01-23, +7 (843) 555-01-24 доб. 7"
    fields = [entry("phone", "+7 (843) 555-01-23, +7 (843) 555-01-24 доб. 7", quote, "/contacts")]
    asyncio.run(box.execute("save_supplier", json.dumps({"website": BASE, "fields": fields})))
    assert sink.suppliers["syrmaster-kazan.test"].values("phone") == ["+7 (843) 555-01-23", "+7 (843) 555-01-24 доб. 7"]


def test_rejected_field_keeps_its_value_for_review():
    box, *_ = make_box(ExtractLLM([]))
    asyncio.run(box._get_page(f"{BASE}/contacts"))
    fields = [entry("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "/contacts"),
              entry("regions", "Татарстан", "Телефон: +7 (843) 555-01-23", "/contacts")]
    saved = json.loads(asyncio.run(box.execute("save_supplier", json.dumps({"website": BASE, "fields": fields}))).text)
    assert saved["отклонено"] == [{"поле": "regions", "значение": "Татарстан", "цитата": "Телефон: +7 (843) 555-01-23",
                                  "причина": "значение не следует из цитаты"}]


# настоящий ответ Yandex Search API на превышение лимита (26.10.2026, 8 одновременных запросов)
REAL_429 = ('{"code":8, "message":"search API backends are overloaded: web search rate limit exceeded, retry later", '
            '"details":[{"@type":"type.googleapis.com/google.rpc.RequestInfo", "requestId":"bcf8209f-6d21-4e59-8c24-9d0166ceb744"}]}')
OK_XML = "<yandexsearch><response><results><grouping><group><doc><url>https://a.test/</url><title>A</title></doc></group></grouping></results></response></yandexsearch>"
RPS_XML = '<yandexsearch><response><error code="55">Превышено ограничение на число запросов в секунду</error></response></yandexsearch>'


def test_yandex_errors_carry_status_code_and_request_id():
    from app.agent.search import SearchError, SearchLimited, http_error, parse_yandex_xml

    limited = http_error(429, REAL_429, None)
    assert isinstance(limited, SearchLimited) and limited.status == 429 and limited.code == "8"
    assert limited.request_id == "bcf8209f-6d21-4e59-8c24-9d0166ceb744" and "rate limit exceeded" in str(limited)
    assert isinstance(http_error(503, "<html>", "rid-1"), SearchLimited) and http_error(503, "<html>", "rid-1").request_id == "rid-1"
    forbidden = http_error(403, '{"code":7, "message":"Permission denied"}', None)
    assert type(forbidden) is SearchError and forbidden.code == "7"  # не повторяется
    assert parse_yandex_xml('<yandexsearch><response><error code="15">Ничего не нашлось</error></response></yandexsearch>') == []
    with pytest.raises(SearchLimited):
        parse_yandex_xml(RPS_XML)
    with pytest.raises(SearchError, match="ошибка 32") as daily:
        parse_yandex_xml('<yandexsearch><response><error code="32">Лимит запросов на сутки</error></response></yandexsearch>', "rid-2")
    assert daily.value.request_id == "rid-2" and not isinstance(daily.value, SearchLimited)


def bare_search(answers):
    """YandexSearch без ключей и сети: _request отдаёт заготовленные ответы или бросает ошибки."""
    from app.agent.search import YandexSearch

    search = object.__new__(YandexSearch)
    search.retry_delays, search.errors = (0, 0, 0), []

    def request(query, region, page=0):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, "rid-ok"

    search._request = request
    return search


def test_yandex_rate_limit_is_retried_and_every_failed_attempt_is_logged():
    from app.agent.search import SearchLimited, http_error

    search = bare_search([http_error(429, REAL_429, None), http_error(429, REAL_429, None), (OK_XML)])
    assert [h.domain for h in asyncio.run(search.search("моцарелла"))] == ["a.test"]
    log = search.drain_errors("моцарелла")
    assert [(e["attempt"], e["retried"], e["status"], e["code"]) for e in log] == [(1, True, 429, "8"), (2, True, 429, "8")]
    assert log[0]["request_id"] == "bcf8209f-6d21-4e59-8c24-9d0166ceb744" and search.errors == []

    search = bare_search([http_error(429, REAL_429, None)] * 4)
    with pytest.raises(SearchLimited):
        asyncio.run(search.search("моцарелла"))  # после трёх повторов ошибка доходит до инструмента
    assert [e["retried"] for e in search.errors] == [True, True, True, False]


def test_search_requests_never_overlap_across_threads():
    import threading
    import time

    from app.agent import search as S

    active, peak = [0], [0]

    def request():
        with S._ONE_AT_A_TIME:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            time.sleep(0.02)
            active[0] -= 1

    threads = [threading.Thread(target=request) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert peak[0] == 1


def test_search_errors_reach_the_feed_with_code_and_request_id_even_for_site_search():
    from app.agent.search import http_error

    events = []

    async def emit(type_, message="", **data):
        events.append((type_, message, data))

    # основной поиск: два отказа и успех; поиск по сайту: отказ без повтора (403) — раньше он пропадал молча
    searcher = bare_search([http_error(429, REAL_429, None), OK_XML.replace("a.test", "syrmaster-kazan.test"),
                            http_error(403, '{"code":7, "message":"Permission denied"}', "rid-403")])
    searcher.retry_delays = (0,)
    box = ToolBox(REQUEST, Limits(max_site_searches_per_supplier=1), searcher, FakeFetcher(dict(PAGES)), MemorySink(), emit,
                  llm=ExtractLLM([FIRST_PASS_FULL[:4]]))
    asyncio.run(box.execute("search_web", '{"query": "моцарелла оптом Казань"}'))
    research(box, BASE)
    failures = [(m, d) for t, m, d in events if t == "search_error"]
    assert len(failures) == 2 and box.counters.search_errors == 2
    assert "HTTP 429, код 8, повторяю" in failures[0][0] and failures[0][1]["request_id"] == "bcf8209f-6d21-4e59-8c24-9d0166ceb744"
    assert failures[1][1]["kind"] == "site" and failures[1][1]["final"] and failures[1][1]["request_id"] == "rid-403"
    assert "поиск по сайту" in failures[1][0] and "не ответил" in failures[1][0]


def test_failed_search_tells_the_model_it_is_an_outage_and_does_not_use_the_budget():
    from app.agent.search import SearchLimited

    class Down(SiteSearch):
        async def search(self, query, region=None):
            raise SearchLimited("Yandex Search: HTTP 429")

    box, *_ = make_box(ExtractLLM([]), searcher=Down())
    out = json.loads(asyncio.run(box.execute("search_web", '{"query": "креветка оптом Владивосток"}')).text)
    assert "сбой поиска" in out["ошибка"] and "research_suppliers" in out["ошибка"] and box.counters.searches == 0


def test_supplier_whose_site_does_not_name_the_product_does_not_count_toward_the_target():
    no_product = [entry("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»"),
                  entry("phone", "+7 (843) 555-01-23", "Телефон: +7 (843) 555-01-23", "/contacts")]
    events = []
    box, _, sink, _ = make_box(ExtractLLM([no_product, [], [], []]))
    box.emit = lambda t, m="", **d: _record(events, t, m)
    result = research(box, BASE)["сайты"][0]
    assert "товар «Моцарелла» на сайте не назван" in result["результат"] and box.counters.saved == 0
    assert "syrmaster-kazan.test" in box.unconfirmed and any(t == "research_skipped" for t, _ in events)


async def _record(events, type_, message):
    events.append((type_, message))
