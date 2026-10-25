"""Агент: проверки данных, загрузчик страниц, инструменты с ограничениями и цикл (всё на заглушках, без сети и базы)."""

import asyncio
import copy
import json
import urllib.request

import pytest

from app.agent.config import Limits, is_blocked
from app.agent.loop import compact, parse_text_tool_calls, run_agent
from app.agent.memory import MemorySink
from app.agent.models import RegionRef, RunRequest, SearchHit
from app.agent.pages import (
    UnsafeUrl,
    _SafeRedirect,
    check_public_url,
    extract_page,
    fetch_html_sync,
    useful_links,
    normalize_url,
)
from app.agent.search import parse_dgis, parse_yandex_xml
from app.agent.tools import ToolBox
from app.agent.validation import (
    normalize_email,
    normalize_phone,
    numbers_supported,
    quote_in_text,
    same_site,
    valid_inn,
)
from app.llm import Reply, ToolCall

HOME_URL = "https://syrmaster-kazan.test/"
CONTACTS_URL = "https://syrmaster-kazan.test/contacts"
HOME_HTML = """<html><head><title>СырМастер Казань — моцарелла оптом</title><style>.a{}</style>
<script>var secret = 'не должно попасть в текст';</script></head><body>
<nav><a href="/contacts">Контакты</a> <a href="/about">О компании</a> <a href="/delivery">Доставка</a>
<a href="https://other.test/contacts">Чужие контакты</a> <a href="/blog/1">Блог</a></nav>
<h1>ООО «СырМастер Казань»</h1><p>Производим моцареллу для пиццы.</p>
<p>Минимальный заказ — от 20&nbsp;кг. Доставка по Татарстану.</p>
<footer>Звоните: <a href="tel:+78435550123">+7 (843) 555-01-23</a></footer></body></html>"""
CONTACTS_HTML = """<html><head><title>Контакты</title></head><body>
<p>Телефон: +7 (843) 555-01-23</p><p>Email: <a href="mailto:Sales@Syrmaster-Kazan.test">sales@syrmaster-kazan.test</a></p>
<p>ИНН 7707083893</p><p>Адрес: г. Казань, ул. Промышленная, 12</p></body></html>"""


def page(url: str, html: str):
    return extract_page(html, url)


class FakeFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages, self.calls = pages, []

    async def fetch(self, url: str):
        self.calls.append(url)
        if url not in self.pages:
            raise RuntimeError("HTTP 404")
        return page(url, self.pages[url])


class FakeSearch:
    def __init__(self, hits: list[SearchHit]):
        self.hits, self.queries = hits, []

    async def search(self, query: str, region=None):
        self.queries.append(query)
        self.regions = getattr(self, "regions", []) + [region]
        return self.hits


def hit(domain: str, url: str | None = None) -> SearchHit:
    return SearchHit(title=f"Сайт {domain}", url=url or f"https://{domain}/", snippet="моцарелла оптом", domain=domain)


REQUEST = RunRequest(category="Моцарелла", synonyms=("Сыр моцарелла",),
                     regions=(RegionRef("Республика Татарстан", "primary"), RegionRef("Кировская область", "neighbor")), target=1)


def make_box(hits=None, pages=None, limits: Limits | None = None, request: RunRequest = REQUEST):
    events: list[tuple[str, str]] = []

    async def emit(type_, message="", **data):
        events.append((type_, message))

    fetcher = FakeFetcher(pages if pages is not None else {HOME_URL: HOME_HTML, CONTACTS_URL: CONTACTS_HTML})
    searcher = FakeSearch(hits or [hit("syrmaster-kazan.test")])
    sink = MemorySink()
    box = ToolBox(request, limits or Limits(), searcher, fetcher, sink, emit)
    return box, fetcher, searcher, sink, events


def call(box: ToolBox, name: str, **args) -> dict:
    result = asyncio.run(box.execute(name, json.dumps(args, ensure_ascii=False)))
    return json.loads(result.text)


def field(name: str, value: str, quote: str, source: str = CONTACTS_URL) -> dict:
    return {"field": name, "value": value, "quote": quote, "source_url": source}


GOOD_FIELDS = [
    field("name", "ООО «СырМастер Казань»", "ООО «СырМастер Казань»", HOME_URL),
    field("phone", "8 843 555 01 23", "Телефон: +7 (843) 555-01-23"),
    field("email", "sales@syrmaster-kazan.test", "sales@syrmaster-kazan.test"),
    field("inn", "7707083893", "ИНН 7707083893"),
    field("moq", "от 20 кг", "Минимальный заказ — от 20 кг.", HOME_URL),
]


def opened(box: ToolBox) -> ToolBox:
    call(box, "fetch_page", url=HOME_URL)
    call(box, "fetch_page", url=CONTACTS_URL)
    return box


# --- проверки данных ---


def test_phone_normalization():
    assert normalize_phone("8 (843) 555-01-23") == "+7 (843) 555-01-23"
    assert normalize_phone("+7 495 123 45 67") == "+7 (495) 123-45-67"
    assert normalize_phone("8-800-555-35-35") == "+7 (800) 555-35-35"
    assert normalize_phone("12345") is None and normalize_phone("+7 (111) 555-01-23") is None


def test_email_and_inn_validation():
    assert normalize_email(" Sales@Example.RU ") == "sales@example.ru" and normalize_email("не почта") is None
    assert valid_inn("7707083893") == "7707083893"  # юрлицо
    assert valid_inn("7707083894") is None  # контрольная сумма не сходится
    assert valid_inn("500100732259") == "500100732259"  # ИП
    assert valid_inn("12345") is None


def test_quote_matching_ignores_case_yo_quotes_dashes_and_spaces():
    text = "Минимальный заказ — от 20 кг.\nООО «Ёлка»"
    assert quote_in_text("минимальный заказ - от 20 кг", text)
    assert quote_in_text('ООО "Елка"', text)
    assert not quote_in_text("Минимальный заказ от 50 кг", text) and not quote_in_text("ab", "ab")


def test_numbers_must_come_from_the_quote():
    assert numbers_supported("от 20 кг", "Минимальный заказ — от 20 кг")
    assert numbers_supported("30000 руб", "бесплатно от 30 000 руб.")
    assert not numbers_supported("от 50 кг", "Минимальный заказ — от 20 кг")


def test_same_site_and_blocklist():
    assert same_site("kazan.example.ru", "example.ru") and same_site("www.example.ru", "example.ru")
    assert not same_site("example.ru", "example.com")
    assert is_blocked("www.avito.ru") and is_blocked("kazan.regtorg.ru") and not is_blocked("gurman-llc.ru")


# --- страницы и безопасность загрузки ---


def test_extract_page_text_links_and_hidden_contacts():
    result = page(HOME_URL, HOME_HTML)
    assert result.title.startswith("СырМастер Казань") and result.site == "syrmaster-kazan.test"
    assert "не должно попасть" not in result.text and "ООО «СырМастер Казань»" in result.text
    assert "+78435550123" in result.text  # телефон из tel:-ссылки попал в текст
    assert not any("other.test" in href for href, _ in result.links)  # чужой сайт отброшен
    useful = dict(useful_links(result.links))
    assert f"{HOME_URL}contacts" in useful and f"{HOME_URL}about" in useful and f"{HOME_URL}delivery" in useful
    assert not any("/blog" in href for href in useful)  # ненужная ссылка не попала в полезные


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/admin", "http://10.0.0.5/", "http://192.168.1.1/",
                                 "http://169.254.169.254/latest/meta-data", "http://[::1]/", "file:///etc/passwd", "ftp://example.com/"])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(UnsafeUrl):
        check_public_url(url)


def test_public_address_is_accepted_and_redirects_are_checked():
    assert check_public_url("http://8.8.8.8/") == "8.8.8.8"
    handler = _SafeRedirect()
    request = urllib.request.Request("https://example.com/")
    with pytest.raises(UnsafeUrl):
        handler.redirect_request(request, None, 302, "Found", {}, "http://127.0.0.1:8000/secret")


def test_fetch_html_sync_never_touches_local_server():
    with pytest.raises(UnsafeUrl):
        fetch_html_sync("http://127.0.0.1:9/", "test", None)


def test_normalize_url():
    assert normalize_url("example.ru/a#frag") == "https://example.ru/a" and normalize_url("http://x.ru") == "http://x.ru"


# --- поисковые провайдеры: разбор ответов ---

YANDEX_XML = """<?xml version="1.0"?><yandexsearch><response><results><grouping>
<group><doc><url>https://gfc-russia.ru/catalog/</url><title>Моцарелла <hlword>оптом</hlword></title>
<passages><passage>Поставляем   моцареллу</passage><passage>от производителей</passage></passages></doc></group>
<group><doc><url>not-a-url</url><title>x</title></doc></group></grouping></results></response></yandexsearch>"""


def test_parse_yandex_and_dgis_responses():
    hits = parse_yandex_xml(YANDEX_XML)
    assert len(hits) == 1 and hits[0].domain == "gfc-russia.ru"
    assert hits[0].title == "Моцарелла оптом" and hits[0].snippet == "Поставляем моцареллу от производителей"
    places = parse_dgis({"result": {"items": [{"id": "1", "name": "СырМастер", "address_name": "Казань, ул. Тестовая, 1",
        "rubrics": [{"name": "Сыры"}], "contact_groups": [{"contacts": [
            {"type": "phone", "text": "+7 843 555-01-23"}, {"type": "website", "value": "https://syrmaster-kazan.test"}]}]}]}})
    assert places[0].phones == ("+7 843 555-01-23",) and places[0].website == "https://syrmaster-kazan.test"
    assert parse_dgis({}) == []


# --- инструменты ---


def test_search_filters_blocked_domains_marks_saved_and_limits_queries():
    box, _, searcher, _, events = make_box(
        hits=[hit("avito.ru"), hit("kazan.regtorg.ru"), hit("syrmaster-kazan.test"), hit("syrmaster-kazan.test"), hit("gfc-russia.ru")],
        limits=Limits(max_searches=2),
    )
    result = call(box, "search_web", query="моцарелла оптом Казань")
    assert [r["домен"] for r in result["результаты"]] == ["syrmaster-kazan.test", "gfc-russia.ru"]  # без блок-листа и дублей
    assert result["отфильтровано"] == 2
    assert any("Ищу в Яндексе" in m for _, m in events)
    call(box, "search_web", query="второй")
    assert "лимит" in call(box, "search_web", query="третий")["ошибка"] and len(searcher.queries) == 2
    assert "пустой" in call(box, "search_web", query=" ")["ошибка"]


def test_fetch_page_returns_text_links_uses_cache_and_obeys_limits():
    box, fetcher, *_ = make_box(limits=Limits(max_fetches=2, page_text_limit=40))
    first = call(box, "fetch_page", url="syrmaster-kazan.test/")  # без схемы
    assert first["из_кэша"] is False and "обрезан" in first["текст"] and first["ссылки"]
    again = call(box, "fetch_page", url=HOME_URL)
    assert again["из_кэша"] is True and len(fetcher.calls) == 1  # повторная загрузка из кэша, сеть не трогаем
    assert call(box, "fetch_page", url=CONTACTS_URL)["заголовок"] == "Контакты"
    assert "лимит" in call(box, "fetch_page", url="https://another.test/")["ошибка"]


def test_fetch_page_reports_errors_and_refuses_internal_addresses():
    box, *_ = make_box(pages={})
    assert "не загрузилась" in call(box, "fetch_page", url="https://missing.test/")["ошибка"]
    assert "ошибка" in call(box, "fetch_page", url="")


def test_unknown_tool_and_bad_arguments_return_errors_instead_of_raising():
    box, *_ = make_box()
    assert "неизвестный" in json.loads(asyncio.run(box.execute("hack", "{}")).text)["ошибка"]
    assert "некорректные" in json.loads(asyncio.run(box.execute("fetch_page", "{не json")).text)["ошибка"]
    assert "search_places" not in [t["function"]["name"] for t in box.schemas()]  # нет ключа 2GIS: инструмента нет


def test_save_supplier_accepts_verified_fields_and_normalizes():
    box, _, _, sink, events = make_box()
    opened(box)
    result = call(box, "save_supplier", website=HOME_URL, fields=GOOD_FIELDS)
    assert result["результат"] == "создан" and set(result["сохранено"]) == {"name", "phone", "email", "inn", "moq"}
    saved = sink.suppliers["syrmaster-kazan.test"]
    assert saved.values("phone") == ["+7 (843) 555-01-23"] and saved.values("inn") == ["7707083893"]
    assert result["статус"]["сохранено_поставщиков"] == 1
    assert any(t == "supplier_saved" and "Сохранён поставщик" in m for t, m in events)


@pytest.mark.parametrize(
    "bad, reason",
    [
        (field("moq", "от 50 кг", "Минимальный заказ — от 20 кг.", HOME_URL), "значение не следует"),
        (field("description", "Лучший сыр в мире", "Лучший сыр в мире", HOME_URL), "цитата не найдена"),
        (field("phone", "+7 (843) 555-99-99", "Телефон: +7 (843) 555-99-99", "https://syrmaster-kazan.test/about"), "не открывал"),
        (field("phone", "+7 (495) 111-22-33", "Телефон: +7 (843) 555-01-23"), "значение не следует"),
        (field("inn", "7707083894", "ИНН 7707083893"), "ИНН"),
        (field("email", "не почта", "sales@syrmaster-kazan.test"), "email"),
        (field("favorite_color", "синий", "синий"), "неизвестное поле"),
    ],
)
def test_save_supplier_rejects_each_kind_of_bad_field_but_keeps_good_ones(bad, reason):
    box, _, _, sink, _ = make_box()
    opened(box)
    result = call(box, "save_supplier", website=HOME_URL, fields=[*GOOD_FIELDS[:3], bad])
    assert result["результат"] == "создан" and any(reason in r["причина"] for r in result["отклонено"])
    assert bad["field"] not in {f.field for f in sink.suppliers["syrmaster-kazan.test"].fields if f.value == bad["value"]}


def test_save_supplier_requires_name_and_contact_and_keeps_nothing_otherwise():
    box, _, _, sink, _ = make_box()
    opened(box)
    only_name = call(box, "save_supplier", website=HOME_URL, fields=GOOD_FIELDS[:1])
    assert "phone или email" in only_name["ошибка"] and not sink.suppliers
    no_name = call(box, "save_supplier", website=HOME_URL, fields=GOOD_FIELDS[1:3])
    assert "name" in no_name["ошибка"] and not sink.suppliers


def test_save_supplier_refuses_foreign_site_as_source_blocked_domains_and_empty_input():
    other = "https://other.test/"
    box, *_ = make_box(pages={HOME_URL: HOME_HTML, CONTACTS_URL: CONTACTS_HTML, other: CONTACTS_HTML})
    opened(box)
    call(box, "fetch_page", url=other)
    # страница чужого сайта не может подтверждать данные поставщика с другим доменом
    result = call(box, "save_supplier", website=HOME_URL, fields=[field("phone", "8 843 555 01 23", "Телефон: +7 (843) 555-01-23", other)])
    assert "не принадлежит" in result["отклонено"][0]["причина"] if "отклонено" in result else "name" in result["ошибка"]
    assert "маркетплейс" in call(box, "save_supplier", website="https://www.avito.ru/x", fields=GOOD_FIELDS)["ошибка"]
    assert "website" in call(box, "save_supplier", website="", fields=GOOD_FIELDS)["ошибка"]
    assert "fields" in call(box, "save_supplier", website=HOME_URL, fields=[])["ошибка"]


def test_second_save_extends_supplier_and_duplicate_inn_is_rejected():
    box, _, _, sink, _ = make_box(pages={HOME_URL: HOME_HTML, CONTACTS_URL: CONTACTS_HTML,
                                         "https://twin.test/contacts": CONTACTS_HTML.replace("<p>Телефон", "<p>ООО «Двойник»</p><p>Телефон")})
    opened(box)
    call(box, "save_supplier", website=HOME_URL, fields=GOOD_FIELDS[:3])
    again = call(box, "save_supplier", website=HOME_URL, fields=[GOOD_FIELDS[3], GOOD_FIELDS[4]] + GOOD_FIELDS[:3])
    assert again["результат"] == "дополнен" and again["статус"]["сохранено_поставщиков"] == 1
    assert len(sink.suppliers["syrmaster-kazan.test"].values("phone")) == 1  # одинаковое значение не дублируется

    call(box, "fetch_page", url="https://twin.test/contacts")
    twin = call(box, "save_supplier", website="https://twin.test/", fields=[
        field("name", "Двойник", "ООО «Двойник»", "https://twin.test/contacts"),
        field("phone", "8 843 555 01 23", "Телефон: +7 (843) 555-01-23", "https://twin.test/contacts"),
        field("inn", "7707083893", "ИНН 7707083893", "https://twin.test/contacts")])
    assert "дубликат" in twin["ошибка"] and "twin.test" not in sink.suppliers


# --- цикл ---


QUALIFIED = '{"supplier": "yes", "product": "yes", "reason": "оптовый поставщик сыров"}'


def is_qualify(messages) -> bool:
    """Проверка сайта перед изучением: заглушки отвечают «подходит», не тратя заготовленные ответы."""
    from app.agent.research import QUALIFY_SYSTEM

    return messages[0]["content"] == QUALIFY_SYSTEM


class ScriptedLLM:
    def __init__(self, replies: list[Reply | Exception]):
        self.replies, self.seen = list(replies), []

    async def chat(self, messages, tools=None, **kwargs):
        if is_qualify(messages):
            return Reply(text=QUALIFIED, prompt_tokens=50, completion_tokens=10)
        self.seen.append(copy.deepcopy(messages))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def tool(name: str, call_id: str = "c", **args) -> Reply:
    return Reply(text=None, tool_calls=[ToolCall(call_id, name, json.dumps(args, ensure_ascii=False))], prompt_tokens=100, completion_tokens=20)


def run(llm, box, limits=None, request=REQUEST):
    events: list[tuple[str, str]] = []

    async def emit(type_, message="", **data):
        events.append((type_, message))

    return asyncio.run(run_agent(request, llm, box, emit, limits or Limits())), events


def happy_script():
    return [
        tool("search_web", query="моцарелла оптом Казань"),
        tool("fetch_page", url=HOME_URL),
        tool("fetch_page", url=CONTACTS_URL),
        tool("save_supplier", website=HOME_URL, fields=GOOD_FIELDS),
        tool("finish", summary="Нашёл одного поставщика"),
    ]


def test_loop_finds_saves_and_finishes_with_target_reached():
    box, _, _, sink, _ = make_box()
    llm = ScriptedLLM(happy_script())
    result, events = run(llm, box)
    assert result.stop_reason == "target_reached" and result.stats["suppliers_saved"] == 1  # цель 1 достигнута, finish не нужен
    assert result.stats["turns"] == 4 and result.stats["searches"] == 1 and result.stats["fetches"] == 2
    assert result.stats["prompt_tokens"] == 400 and "syrmaster-kazan.test" in sink.suppliers
    assert [t for t, _ in events][0] == "run_started" and events[-1][0] == "run_finished"


def test_loop_stops_on_finish_when_target_not_reached():
    request = RunRequest(category="Моцарелла", target=3)
    box, *_ = make_box(request=request)
    result, _ = run(ScriptedLLM([tool("search_web", query="x"), tool("finish", summary="Ничего подходящего"), tool("finish", summary="Ничего подходящего")]), box, request=request)
    assert result.stop_reason == "finished" and result.summary == "Ничего подходящего" and result.stats["suppliers_saved"] == 0


def test_loop_stops_on_turn_time_and_token_limits_and_model_errors():
    request = RunRequest(category="Моцарелла", target=5)
    endless = lambda: ScriptedLLM([tool("search_web", query=f"q{i}") for i in range(20)])  # noqa: E731
    box, *_ = make_box(request=request)
    assert run(endless(), box, Limits(max_turns=3), request)[0].stop_reason == "turn_limit"
    box, *_ = make_box(request=request)
    assert run(endless(), box, Limits(max_seconds=-1), request)[0].stop_reason == "time_limit"
    box, *_ = make_box(request=request)
    result, _ = run(endless(), box, Limits(max_total_tokens=250), request)  # каждый ход стоит 120 токенов
    assert result.stop_reason == "token_limit" and result.stats["turns"] == 3
    box, *_ = make_box(request=request)
    result, _ = run(ScriptedLLM([RuntimeError("модель недоступна")]), box, request=request)
    assert result.stop_reason == "llm_error" and "недоступна" in result.error
    box, *_ = make_box(request=request)
    result, _ = run(ScriptedLLM([Reply(text="Вот итог без вызова инструментов")]), box, request=request)
    assert result.stop_reason == "no_action" and result.summary == "Вот итог без вызова инструментов"


def test_loop_gives_model_status_and_survives_tool_errors():
    box, *_ = make_box(request=RunRequest(category="Моцарелла", target=5))
    llm = ScriptedLLM([tool("fetch_page", url="https://missing.test/"), tool("finish", summary="ок"), tool("finish", summary="ок")])
    result, _ = run(llm, box, request=RunRequest(category="Моцарелла", target=5))
    tool_message = [m for m in llm.seen[1] if m["role"] == "tool"][0]
    data = json.loads(tool_message["content"])
    assert "не загрузилась" in data["ошибка"] and data["статус"]["осталось_шагов"] == Limits().max_turns - 1 and result.stop_reason == "finished"


def test_prompt_contains_category_regions_synonyms_and_budget():
    box, *_ = make_box()
    llm = ScriptedLLM([tool("finish", summary="ок"), tool("finish", summary="ок")])
    run(llm, box)
    user = llm.seen[0][1]["content"]
    assert "Моцарелла" in user and "Сыр моцарелла" in user and "1. Республика Татарстан (основной регион)" in user
    assert "2. Кировская область (соседний)" in user and "до 1 поставщиков" in user and str(Limits().max_turns) in user
    everywhere = RunRequest(category="Рис")
    box, *_ = make_box(request=everywhere)
    llm = ScriptedLLM([tool("finish", summary="ок"), tool("finish", summary="ок")])
    run(llm, box, request=everywhere)
    assert "по всей России" in llm.seen[0][1]["content"]


def test_old_fetch_results_are_compacted_but_search_results_stay():
    messages = [{"role": "system", "content": "s"}]
    long_page = json.dumps({"адрес": "https://a/", "заголовок": "A", "текст": "x" * 3000}, ensure_ascii=False)
    for i in range(5):
        messages.append({"role": "tool", "name": "fetch_page", "tool_call_id": str(i), "content": long_page})
    messages.append({"role": "tool", "name": "search_web", "tool_call_id": "s", "content": "y" * 3000})
    compact(messages, keep=2)
    sizes = [len(m["content"]) for m in messages if m["role"] == "tool"]
    assert sizes[:3] == [sizes[0]] * 3 and sizes[0] < 400 and sizes[3] > 3000 and sizes[4] > 3000 and sizes[5] == 3000
    assert "сокращён" in messages[1]["content"]


# --- уроки контрольного прогона с настоящей моделью ---


def test_quotes_of_any_kind_do_not_break_quote_matching():
    page_text = 'Производитель бренда "Фарфаторре"\nООО "Инлут"\nАдрес: г. Казань'
    assert quote_in_text("Производитель бренда 'Фарфаторре' ООО 'Инлут'", page_text)  # одинарные вместо двойных, две строки в одной цитате
    assert quote_in_text("ООО «Инлут»", page_text) and quote_in_text("ООО Инлут", page_text)
    assert not quote_in_text("ООО 'Другой'", page_text)  # кавычки игнорируются, а слова нет


def test_text_tool_call_is_parsed_like_a_real_one():
    text = '[TOOL_CALL_START]save_supplier\n{"fields":[],"website":"https:\\/\\/a.test\\/"}'
    calls = parse_text_tool_calls(text)
    assert [c.name for c in calls] == ["save_supplier"]
    assert json.loads(calls[0].arguments)["website"] == "https://a.test/"  # экранированные слэши разобраны
    two = parse_text_tool_calls('[TOOL_CALL_START]fetch_page {"url": "https://a.test/"}[TOOL_CALL_START]finish {"summary": "ок"}[TOOL_CALL_END]')
    assert [c.name for c in two] == ["fetch_page", "finish"]
    assert parse_text_tool_calls('[TOOL_CALL_START]fetch_page {битый json') == [] and parse_text_tool_calls("просто текст") == []
    assert parse_text_tool_calls(None) == []


def test_loop_executes_tool_call_written_as_text_instead_of_stopping():
    request = RunRequest(category="Моцарелла", target=5)
    box, *_ = make_box(request=request)
    text_call = Reply(text='[TOOL_CALL_START]fetch_page\n{"url": "https://syrmaster-kazan.test/"}', prompt_tokens=50, completion_tokens=10)
    llm = ScriptedLLM([text_call, tool("finish", summary="готово"), tool("finish", summary="готово")])
    result, events = run(llm, box, request=request)
    assert result.stop_reason == "finished" and result.stats["fetches"] == 1  # не «no_action»: вызов выполнен
    assert any(t == "note" and "текстом" in m for t, m in events)
    history = llm.seen[1]
    assert history[-2]["role"] == "assistant" and history[-2]["tool_calls"][0]["function"]["name"] == "fetch_page"
    assert history[-1]["role"] == "tool"


def test_early_finish_is_refused_once_then_accepted():
    request = RunRequest(category="Моцарелла", target=3)
    box, *_ = make_box(request=request)
    first = call(box, "finish", summary="всё")
    assert "Рано завершать" in first["ошибка"] and not box.finished and first["статус"]["сохранено_поставщиков"] == 0
    assert "результат" in call(box, "finish", summary="всё")  # повторный finish принимается
    assert box.finished and box.summary == "всё"


def test_finish_is_accepted_at_once_when_target_reached_or_search_was_thorough_or_budget_spent():
    box, *_ = make_box()  # цель 1
    opened(box)
    call(box, "save_supplier", website=HOME_URL, fields=GOOD_FIELDS)
    assert "результат" in call(box, "finish", summary="цель достигнута")

    request = RunRequest(category="Моцарелла", target=5)
    box, *_ = make_box(request=request)
    for q in ("а", "б", "в"):
        call(box, "search_web", query=q)
    for url in (HOME_URL, CONTACTS_URL, "https://x.test/", "https://y.test/"):
        call(box, "fetch_page", url=url)  # даже неудачные загрузки считаются попытками проверить сайты
    assert "результат" in call(box, "finish", summary="искал добросовестно")

    box, *_ = make_box(request=request, limits=Limits(max_searches=1))
    call(box, "search_web", query="единственный")
    assert "результат" in call(box, "finish", summary="бюджет поиска исчерпан")


def test_not_opened_page_rejection_lists_opened_pages_to_help_the_model():
    box, *_ = make_box()
    call(box, "fetch_page", url=HOME_URL)
    result = call(box, "save_supplier", website=HOME_URL, fields=[field("name", "ООО", "ООО", "https://syrmaster-kazan.test/about")])
    assert HOME_URL in result["отклонено"][0]["причина"] and "Открытые страницы" in result["отклонено"][0]["причина"]


def test_supplier_website_is_saved_as_site_origin_even_if_model_gives_inner_page():
    box, _, _, sink, _ = make_box()
    opened(box)
    call(box, "save_supplier", website="https://syrmaster-kazan.test/contacts?x=1#top", fields=GOOD_FIELDS)
    assert sink.suppliers["syrmaster-kazan.test"].website == "https://syrmaster-kazan.test/"
