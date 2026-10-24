"""Каталог: разбор данных Nuxt, обход городов, сравнение с базой, поток событий (без сети и базы)."""

import asyncio
import json

import pytest

from app.catalog.diff import Existing, check_sane, plan_changes
from app.catalog.eda1 import CrawlError, Eda1Source, parse_city_page
from app.catalog.nuxt import NuxtDataError, extract_nodes, unflatten
from app.catalog.progress import SyncProgress
from app.config import Settings


def encode(value) -> list:
    """Упаковывает обычные данные в плоский массив узлов, как это делает Nuxt (devalue)."""
    nodes: list = []

    def add(item) -> int:
        index = len(nodes)
        nodes.append(None)
        if isinstance(item, dict):
            nodes[index] = {key: add(sub) for key, sub in item.items()}
        elif isinstance(item, list):
            nodes[index] = [add(sub) for sub in item]
        else:
            nodes[index] = item
        return index

    add(value)
    return nodes


def page(products: list[dict], slugs: list[str], categories: list[dict] | None = None) -> str:
    categories = categories if categories is not None else [
        {"id": 1, "title": "Роллы", "url": "/rolly"},
        {"id": 2, "title": "Новинки", "url": "/novinki"},
    ]
    data = {"pinia": {"product": {"products": products, "categories": categories}, "delivery": {"citiesUrls": slugs}}}
    return f'<html><script type="application/json" id="__NUXT_DATA__">{json.dumps(encode(data), ensure_ascii=False)}</script></html>'


def product(pid: int, name: str, description: str = "", weight: str | None = "100 г", cats=(1,)) -> dict:
    return {"id": pid, "name": name, "description": description, "info": {"weight": weight} if weight else {},
            "categories_ids": list(cats), "url": f"/product/{pid}"}


# --- Nuxt ---

def test_unflatten_resolves_references_and_wrappers():
    nodes = [["ShallowReactive", 1], {"a": 2, "b": 3}, "значение", [2, 4], 42]
    assert unflatten(nodes) == {"a": "значение", "b": ["значение", 42]}


def test_extract_nodes_errors():
    with pytest.raises(NuxtDataError):
        extract_nodes("<html>без данных</html>")
    with pytest.raises(NuxtDataError):
        extract_nodes('<script id="__NUXT_DATA__">не json</script>')


# --- разбор страницы города ---

def test_parse_city_page_items_sections_and_ignored_sections():
    html = page(
        [product(1, " Филадельфия ", "Лосось, рис", "230 г", cats=(1, 2)), product(2, "Имбирь", "", None, cats=(1,))],
        ["ekb", "tagil"],
    )
    items, slugs = parse_city_page(html)
    assert slugs == ["ekb", "tagil"]
    first, second = items
    assert first.name == "Филадельфия" and first.sections == ("Роллы",)  # «Новинки» — раздел-дубль
    assert first.weight == "230 г" and second.weight is None and second.description == ""


def test_parse_city_page_detects_changed_structure():
    broken = f'<script id="__NUXT_DATA__">{json.dumps(encode({"pinia": {}}))}</script>'
    with pytest.raises(NuxtDataError):
        parse_city_page(broken)


def test_content_hash_changes_with_description():
    a, _ = parse_city_page(page([product(1, "Ролл", "рис")], ["ekb"]))
    b, _ = parse_city_page(page([product(1, "Ролл", "рис и нори")], ["ekb"]))
    assert a[0].content_hash != b[0].content_hash


# --- обход городов ---

def make_source(pages: dict[str, str | Exception | list]):
    calls: list[str] = []

    async def fetch(url: str) -> str:
        calls.append(url)
        slug = url.rsplit("/", 1)[1]
        value = pages[slug]
        if isinstance(value, list):  # последовательность ответов: сначала ошибки, потом страница
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    source = Eda1Source(Settings(_env_file=None, crawl_delay_seconds=0), fetch=fetch)
    return source, calls


async def collect(source: Eda1Source):
    events: list[dict] = []

    async def emit(**event):
        events.append(event)

    return await source.crawl(emit), events


def test_crawl_merges_cities_primary_first_and_reports_failed_city():
    slugs = ["beta", "alpha", "ekb"]
    source, calls = make_source(
        {
            "ekb": page([product(1, "Ролл"), product(2, "Пицца")], slugs),
            "alpha": page([product(2, "Пицца"), product(3, "Суп")], slugs),
            "beta": RuntimeError("HTTP 500"),
        }
    )
    result, events = asyncio.run(collect(source))
    assert [c.rsplit("/", 1)[1] for c in calls[:2]] == ["ekb", "alpha"]  # главный город первым, остальные по алфавиту
    assert set(result.items) == {1, 2, 3}
    assert result.cities_ok == ["ekb", "alpha"]
    assert list(result.cities_failed) == ["beta"] and "HTTP 500" in result.cities_failed["beta"]
    assert result.conflicts == 0
    assert [e["type"] for e in events] == ["city", "city", "city_failed"]


def test_crawl_counts_conflicts_and_keeps_primary_version():
    source, _ = make_source(
        {
            "ekb": page([product(1, "Ролл", "рис")], ["ekb", "alpha"]),
            "alpha": page([product(1, "Ролл", "другое описание")], ["ekb", "alpha"]),
        }
    )
    result, _ = asyncio.run(collect(source))
    assert result.conflicts == 1 and result.items[1].description == "рис"


def test_crawl_retries_failed_request():
    source, calls = make_source({"ekb": [RuntimeError("сбой"), RuntimeError("сбой"), page([product(1, "Ролл")], ["ekb"])]})
    result, _ = asyncio.run(collect(source))
    assert set(result.items) == {1} and len(calls) == 3


def test_crawl_fails_when_primary_page_unavailable():
    source, _ = make_source({"ekb": RuntimeError("недоступно")})
    with pytest.raises(CrawlError):
        asyncio.run(collect(source))


# --- сравнение с базой ---

def fetched(*specs: tuple[int, str]):
    items, _ = parse_city_page(page([product(pid, "Ролл", desc) for pid, desc in specs], ["ekb"]))
    return {i.source_id: i for i in items}


def test_plan_add_update_restore_unchanged_archive():
    new = fetched((1, "same"), (2, "changed"), (3, "back"), (4, "brand new"))
    old = fetched((1, "same"), (2, "was"), (3, "back"), (5, "gone"))
    existing = {
        1: Existing(old[1].content_hash, "active"),
        2: Existing(old[2].content_hash, "active"),
        3: Existing(old[3].content_hash, "archived"),
        5: Existing(old[5].content_hash, "active"),
    }
    plan = plan_changes(existing, new, complete=True)
    assert [i.source_id for i in plan.add] == [4]
    assert [i.source_id for i in plan.update] == [2]
    assert [i.source_id for i in plan.restore] == [3]
    assert plan.unchanged == [1]
    assert plan.archive == [5]


def test_plan_does_not_archive_when_crawl_is_incomplete():
    existing = {5: Existing("h", "active")}
    plan = plan_changes(existing, fetched((1, "x")), complete=False)
    assert plan.archive == []


def test_plan_does_not_archive_already_archived():
    plan = plan_changes({5: Existing("h", "archived")}, fetched((1, "x")), complete=True)
    assert plan.archive == []


def test_check_sane_guards():
    assert check_sane(0, 0) is not None  # пустой результат
    assert check_sane(0, 5) is None  # первая загрузка
    assert check_sane(100, 49) is not None  # меньше половины
    assert check_sane(100, 50) is None
    assert check_sane(10, 1) is None  # база мала, защита не применяется


# --- поток событий ---

def test_progress_stream_replays_history_and_ends_on_close():
    async def scenario():
        progress = SyncProgress()
        await progress.emit(type="started")
        await progress.emit(type="city", slug="ekb")
        await progress.close()
        return [e async for e in progress.stream()]  # подписчик пришёл после завершения

    assert [e["type"] for e in asyncio.run(scenario())] == ["started", "city"]


def test_progress_stream_sends_heartbeat_while_waiting():
    async def scenario():
        progress = SyncProgress()
        stream = progress.stream(heartbeat=0.05)
        first = await anext(stream)  # событий нет: приходит heartbeat
        await progress.emit(type="done")
        second = await anext(stream)
        await progress.close()
        return first, second

    assert asyncio.run(scenario()) == (None, {"type": "done"})


# --- загрузка страниц ---

def test_decode_body_handles_gzip_and_plain():
    import gzip

    from app.net import decode_body

    text = "Меню: лосось, рис"
    assert decode_body(gzip.compress(text.encode("utf-8")), "gzip") == text
    assert decode_body(text.encode("utf-8"), None) == text
    with pytest.raises(RuntimeError):
        decode_body(b"not gzip at all", "gzip")
