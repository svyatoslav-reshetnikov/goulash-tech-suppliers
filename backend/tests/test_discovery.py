"""Проверка полей без ложных отказов, дословные цитаты, поиск кандидатов по плану и изучение всех найденных сайтов."""

import asyncio
import json

from app.agent.config import Limits
from app.agent.discovery import build_plan
from app.agent.loop import run_agent
from app.agent.memory import MemorySink
from app.agent.models import RegionRef, RunRequest, SearchHit
from app.agent.research import focus_text, select_pages
from app.agent.tools import ToolBox
from app.agent.validation import snap_quote, value_supported_by_quote
from app.llm import Reply
from tests.test_agent import FakeFetcher, ScriptedLLM, is_qualify, tool
from tests.test_research import ExtractLLM, SiteSearch, make_box

GRIB = "https://grib.test"
GRIB_HTML = """<html><body><h1>ООО Гриб</h1><p>Телефон: +7 (495) 555-01-23</p><h2>Частые вопросы</h2>
<p>Есть ли минимальный объём заказа?</p><p>Да. Минимальный объём заказа возможен от 3кг продукции самовывозом.</p>
<p>Осуществляем доставку от 100 кг собственным транспортом. Также возможна доставка через транспортные службы.</p>
<p>Шампиньоны свежие оптом.</p></body></html>"""
MUSHROOMS = RunRequest(category="Шампиньоны", regions=(RegionRef("Москва", "primary", 213),), target=5, search_region=213)


def save(box, fields):
    asyncio.run(box._get_page(f"{GRIB}/"))
    out = asyncio.run(box.execute("save_supplier", json.dumps({"website": GRIB, "fields": fields}, ensure_ascii=False)))
    return json.loads(out.text)


def entry(field, value, quote):
    return {"field": field, "value": value, "quote": quote, "source_url": f"{GRIB}/"}


# --- слитные число и единица ---


def test_a_number_glued_to_its_unit_still_supports_the_value():
    assert value_supported_by_quote("moq", "от 3 кг", "Минимальный объём заказа возможен от 3кг продукции")
    assert value_supported_by_quote("moq", "от 500 кг", "гибкие объёмы от 500кг")
    assert value_supported_by_quote("moq", "от 5 000 ₽", "Минимальная сумма заказа 5000₽")
    assert value_supported_by_quote("moq", "от 3 кг", "минимум 3 кг")  # предлог «от» ничего не подтверждает и не мешает
    # число и единица по-прежнему обязательны
    assert not value_supported_by_quote("moq", "от 3 кг", "скидка 3% на заказ")
    assert not value_supported_by_quote("moq", "от 3 кг", "минимальный заказ от 30кг")
    assert not value_supported_by_quote("moq", "от 3 кг", "минимальный заказ от 3 шт")


# --- дословная цитата вместо искажённой ---


def test_garbled_quote_is_replaced_by_the_real_text_of_the_page():
    box, *_ = make_box(ExtractLLM([]), pages={f"{GRIB}/": GRIB_HTML}, request=MUSHROOMS)
    saved = save(box, [
        entry("phone", "+7 (495) 555-01-23", "Телефон: +7 (495) 555-01-23"),
        entry("moq", "от 3 кг", "Минимальный объём заказа возможен от 3 кг продукции самовывозом"),  # модель вставила пробел
        # склейка двух мест страницы
        entry("delivery_terms", "доставка от 100 кг собственным транспортом",
              "Осуществляем доставку от 100 кг собственным транспортом. Самовывоз по адресу склада"),
    ])
    assert "moq" in saved["сохранено"] and "delivery_terms" in saved["сохранено"] and "отклонено" not in saved
    moq = next(f for f in box.sink.suppliers["grib.test"].fields if f.field == "moq")
    assert moq.snapped and moq.quote == "Минимальный объём заказа возможен от 3кг продукции самовывозом."  # цитата страницы
    assert next(f for f in box.sink.suppliers["grib.test"].fields if f.field == "delivery_terms").snapped


def test_a_fact_that_is_not_on_the_page_is_not_rescued_by_a_quote_replacement():
    box, *_ = make_box(ExtractLLM([]), pages={f"{GRIB}/": GRIB_HTML}, request=MUSHROOMS)
    saved = save(box, [
        entry("phone", "+7 (495) 555-01-23", "Телефон: +7 (495) 555-01-23"),
        entry("regions", "Казань", "Доставка по Казани бесплатно"),
        entry("moq", "от 50 кг", "Минимальный объём заказа возможен от 50кг"),
    ])
    assert [r["поле"] for r in saved["отклонено"]] == ["regions", "moq"]
    assert saved["отклонено"][0]["цитата"] == "Доставка по Казани бесплатно"  # отклонённая цитата видна в разборе


def test_snap_quote_picks_the_line_most_like_the_models_quote_and_pairs_a_label_with_its_value():
    text = "Контакты\nТелефон:\n+7 (495) 555-01-23\nПрайс\nШампиньоны 250 руб/кг"
    assert snap_quote(text, "Телефон +7 (495) 555-01-23", lambda piece: "555-01-23" in piece) == "Телефон: +7 (495) 555-01-23"
    assert snap_quote(text, "", lambda piece: "нет такого" in piece) is None


# --- текст страницы для модели ---


def test_small_page_goes_to_the_model_whole_and_a_large_one_keeps_its_footer():
    small = "Начало\n" + "строка про заказ и доставку\n" * 100 + "Контакты: +7 (495) 555-01-23"
    assert focus_text(small, 9000) == small
    big = "Начало страницы\n" + "Новости про области и регионы. " * 400 + "\nКонтакты: info@site.test, офис в Москве"
    trimmed = focus_text(big, 3200)
    assert len(trimmed) <= 3300 and "Начало страницы" in trimmed and trimmed.endswith("офис в Москве")


def test_menu_label_beats_a_news_headline_and_product_pages_are_added():
    links = [
        (f"{GRIB}/news/1", "Мастер Гриб получил Сертификат на удобрение органическое, премию и награду"),
        (f"{GRIB}/sertifikaty", "Сертификаты"),
        (f"{GRIB}/shampinony", "Шампиньоны свежие"),
        (f"{GRIB}/contacts", "Контакты"),
    ]
    assert select_pages(links, 8) == [f"{GRIB}/contacts", f"{GRIB}/sertifikaty"]
    assert select_pages(links, 8, product=["шампиньо"]) == [f"{GRIB}/contacts", f"{GRIB}/sertifikaty", f"{GRIB}/shampinony"]


# --- план поиска ---

THREE_REGIONS = RunRequest(
    category="Шампиньоны", synonyms=("Грибы шампиньоны",), search_region=213, city=None,
    regions=(RegionRef("Москва", "primary", 213), RegionRef("Московская область", "neighbor", 1), RegionRef("Калужская область", "neighbor", 225)),
)


def test_plan_searches_every_region_with_its_own_code_and_looks_beyond_the_first_page():
    plan = build_plan(THREE_REGIONS)
    assert len({(s.query.lower(), s.region, s.page) for s in plan}) == len(plan)  # без повторов
    assert {s.region for s in plan} == {213, 1, 225}
    assert any(s.page == 1 for s in plan) and any("Калужская область" in s.query for s in plan)
    assert plan[0].region == 213 and "Москва" in plan[0].query  # основной регион первым
    assert any(s.query.startswith("грибы шампиньоны оптом") for s in plan)  # название-синоним
    assert build_plan(RunRequest(category="Рис"))[0].query == "рис оптом производитель"  # без региона: по всей России


# --- изучение всех найденных кандидатов ---

HOSTS = [f"s{i}.test" for i in range(12)]


class PagedSearch:
    """Выдача: на первой странице — первые десять сайтов, на второй — остальные; поиск по сайтам ничего не находит."""

    def __init__(self):
        self.calls = []

    async def search(self, query: str, region=None, page: int = 0):
        self.calls.append((query, region, page))
        if query.startswith("site:"):
            return []
        chunk = HOSTS[:10] if page == 0 else HOSTS[10:]
        return [SearchHit(f"Шампиньоны оптом {h}", f"https://{h}/", "шампиньоны", h) for h in chunk]


class PerSiteLLM:
    """Отвечает за тот сайт, текст которого пришёл в запросе извлечения."""

    def __init__(self, sells_product=True):
        self.sells_product, self.calls = sells_product, 0

    async def chat(self, messages, tools=None, **kwargs):
        if is_qualify(messages):
            return Reply(text='{"supplier": "yes", "product": "yes", "reason": "оптовик"}', prompt_tokens=50, completion_tokens=10)
        self.calls += 1
        host = next(h for h in HOSTS if f"https://{h}/" in messages[1]["content"])
        n = HOSTS.index(host)
        fields = [
            {"field": "name", "value": f"ООО Гриб {n}", "quote": f"ООО Гриб {n}", "source_url": f"https://{host}/"},
            {"field": "phone", "value": f"+7 (495) 555-01-{n:02d}", "quote": f"Телефон: +7 (495) 555-01-{n:02d}", "source_url": f"https://{host}/"},
            {"field": "assortment", "value": "шампиньоны свежие" if self.sells_product else "мебель",
             "quote": "Шампиньоны свежие оптом" if self.sells_product else "Мебель оптом", "source_url": f"https://{host}/"},
        ]
        return Reply(text=json.dumps({"fields": fields}, ensure_ascii=False), prompt_tokens=800, completion_tokens=100)


def site_pages(product="Шампиньоны свежие оптом"):
    return {f"https://{h}/": f"<html><body><h1>ООО Гриб {i}</h1><p>Телефон: +7 (495) 555-01-{i:02d}</p><p>{product}</p></body></html>"
            for i, h in enumerate(HOSTS)}


def run_auto(llm, pages, request, limits):
    events = []

    async def emit(type_, message="", **data):
        events.append((type_, message, data))

    search = PagedSearch()
    box = ToolBox(request, limits, search, FakeFetcher(pages), MemorySink(), emit, llm=llm)
    main = ScriptedLLM([tool("finish", summary="готово")])
    result = asyncio.run(run_agent(request, main, box, emit, limits))
    return result, box, search, events


def test_every_found_candidate_is_researched_without_a_cap_on_the_number_of_sites():
    request = RunRequest(category="Шампиньоны", search_region=213, target=20, regions=THREE_REGIONS.regions)
    result, box, search, _ = run_auto(PerSiteLLM(), site_pages(), request, Limits(auto_discovery=True, max_site_searches_per_supplier=0))
    assert box.counters.researched == 12 and box.counters.saved == 12  # раньше стоял предел в 8 сайтов
    assert {region for _, region, _ in search.calls} == {213, 1, 225} and any(page == 1 for *_, page in search.calls)
    assert len({call for call in search.calls}) == len(search.calls)  # запросы не повторяются
    assert result.stop_reason == "finished" and result.stats["researched"] == 12


def test_research_stops_at_the_target_and_by_the_budget():
    request = RunRequest(category="Шампиньоны", search_region=213, target=4, regions=THREE_REGIONS.regions)
    result, box, *_ = run_auto(PerSiteLLM(), site_pages(), request, Limits(auto_discovery=True, max_site_searches_per_supplier=0))
    assert result.stop_reason == "target_reached" and 4 <= box.counters.saved <= 6  # пачками по три сайта
    request = RunRequest(category="Шампиньоны", search_region=213, target=20, regions=THREE_REGIONS.regions)
    result, box, *_ = run_auto(PerSiteLLM(), site_pages(), request, Limits(auto_discovery=True, max_total_tokens=3000))
    assert result.stop_reason == "token_limit" and box.counters.researched < 12


def test_a_site_without_the_product_costs_one_search_not_four():
    request = RunRequest(category="Шампиньоны", search_region=213, target=20, regions=THREE_REGIONS.regions)
    result, box, search, _ = run_auto(PerSiteLLM(sells_product=False), site_pages("Мебель оптом"), request,
                                      Limits(auto_discovery=True, max_candidates=3))
    site_searches = [q for q, *_ in search.calls if q.startswith("site:")]
    assert box.counters.saved == 0 and len(site_searches) == 3 and all(q.endswith("Шампиньоны") for q in site_searches)


# --- очередь: упоминания продукта на главной, итог по каждому сайту ---


def test_sites_that_mention_the_product_are_studied_first_and_every_candidate_gets_a_status():
    pages = site_pages()
    pages["https://s5.test/"] = "<html><body><h1>ООО Гриб 5</h1><p>Телефон: +7 (495) 555-01-05</p><p>Мебель оптом</p></body></html>"
    request = RunRequest(category="Шампиньоны", search_region=213, target=20, regions=THREE_REGIONS.regions)
    result, box, *_ = run_auto(PerSiteLLM(), pages, request, Limits(auto_discovery=True, max_candidates=11, max_site_searches_per_supplier=0))
    report = {row["domain"]: row for row in result.stats["candidates"]}
    assert report["s5.test"]["status"] == "pending" and report["s5.test"]["mentions"] == 0  # без продукта на главной: последним
    assert report["s0.test"]["status"] == "saved" and report["s0.test"]["mentions"] >= 1
    assert [row["status"] for row in result.stats["candidates"]].count("saved") == 11 and len(report) == 12
    assert result.stats["candidates"][-1]["domain"] == "s5.test"  # очередь отсортирована: нерелевантный в конце


def test_rejected_unconfirmed_and_unopened_sites_keep_their_reason():
    pages = site_pages()
    del pages["https://s1.test/"]
    request = RunRequest(category="Шампиньоны", search_region=213, target=20, regions=THREE_REGIONS.regions)
    result, box, *_ = run_auto(PerSiteLLM(sells_product=False), pages, request, Limits(auto_discovery=True, max_candidates=4))
    rows = {row["domain"]: row for row in result.stats["candidates"]}
    assert rows["s1.test"]["mentions"] is None and rows["s1.test"]["status"] in ("failed", "pending")  # не открылся: ранжируется ниже
    assert {row["status"] for row in rows.values()} <= {"pending", "failed", "unconfirmed", "rejected", "saved", "studying", "no_contacts"}
    assert any(row["status"] == "unconfirmed" and "не назван" in row["reason"] for row in rows.values())


# --- загрузка страниц: повтор при временном сбое ---


def test_fetcher_retries_a_timeout_once_but_not_a_refusal(monkeypatch):
    from app.agent import pages as pages_module

    monkeypatch.setattr(pages_module, "RETRY_PAUSE", 0)
    calls = []

    def flaky(url, agent, context, timeout=45):
        calls.append(url)
        if len(calls) == 1:
            raise RuntimeError("не удалось загрузить: timed out")
        return url, "<html><body>ok</body></html>"

    monkeypatch.setattr(pages_module, "fetch_html_sync", flaky)
    fetcher = pages_module.HttpFetcher("test", None)
    assert asyncio.run(fetcher.fetch("https://a.test/")).text == "ok" and len(calls) == 2

    calls.clear()

    def refused(url, agent, context, timeout=45):
        calls.append(url)
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(pages_module, "fetch_html_sync", refused)
    try:
        asyncio.run(fetcher.fetch("https://a.test/"))
    except RuntimeError as exc:
        assert str(exc) == "HTTP 403" and len(calls) == 1
    calls.clear()
    monkeypatch.setattr(pages_module, "fetch_html_sync", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 503")))
    try:
        asyncio.run(fetcher.fetch("https://a.test/"))
    except RuntimeError:
        pass


def test_fetcher_retries_a_host_that_did_not_resolve_but_not_a_forbidden_address(monkeypatch):
    from app.agent import pages as pages_module

    monkeypatch.setattr(pages_module, "RETRY_PAUSE", 0)
    calls = []

    def dns_blip(url, agent, context, timeout=45):
        calls.append(url)
        if len(calls) < 3:
            raise pages_module.UnresolvedHost("адрес не разрешается")
        return url, "<html><body>ok</body></html>"

    monkeypatch.setattr(pages_module, "fetch_html_sync", dns_blip)
    assert asyncio.run(pages_module.HttpFetcher("test", None).fetch("https://a.test/")).text == "ok" and len(calls) == 3

    calls.clear()

    def internal(url, agent, context, timeout=45):
        calls.append(url)
        raise pages_module.UnsafeUrl("внутренние и локальные адреса запрещены")

    monkeypatch.setattr(pages_module, "fetch_html_sync", internal)
    try:
        asyncio.run(pages_module.HttpFetcher("test", None).fetch("http://10.0.0.1/"))
    except pages_module.UnsafeUrl:
        assert len(calls) == 1
