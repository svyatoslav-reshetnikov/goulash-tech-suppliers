"""Источник каталога: eda1.ru («Сушкоф и пицца»).

Сайт на Nuxt: страница любого города уже содержит всё меню этого города и список городов,
поэтому достаточно по одному запросу на город. Правила обхода: пауза между запросами,
честный User-Agent, только публичные страницы.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.catalog.nuxt import NuxtDataError, extract_nodes, unflatten
from app.config import Settings
from app.net import http_get, settings_ssl_context

log = logging.getLogger(__name__)

BASE_URL = "https://eda1.ru"
PRIMARY_CITY = "ekb"  # его версия позиции берётся первой при расхождении между городами
IGNORED_SECTIONS = {"/samovyvoz", "/novinki"}  # разделы-дубли: позиции в них уже есть в основных
FETCH_ATTEMPTS = 3

Emit = Callable[..., Awaitable[None]]


class CrawlError(RuntimeError):
    """Обход не удался целиком (например, не загрузилась первая страница)."""


@dataclass(frozen=True)
class MenuItem:
    source_id: int
    name: str
    description: str
    weight: str | None
    url: str
    sections: tuple[str, ...]
    content_hash: str


@dataclass
class CrawlResult:
    items: dict[int, MenuItem] = field(default_factory=dict)
    cities_ok: list[str] = field(default_factory=list)
    cities_failed: dict[str, str] = field(default_factory=dict)
    conflicts: int = 0  # одна позиция с разным содержимым в разных городах


def content_hash(name: str, description: str, weight: str | None, sections: tuple[str, ...] | list[str]) -> str:
    payload = json.dumps([name, description, weight, sorted(sections)], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def parse_city_page(html: str) -> tuple[list[MenuItem], list[str]]:
    """Возвращает позиции меню города и список slug всех городов сайта."""
    try:
        pinia = unflatten(extract_nodes(html))["pinia"]
        store = pinia["product"]
        slugs = [str(s) for s in pinia["delivery"]["citiesUrls"]]
        raw_products = list(store["products"]) + list(store.get("adultProducts") or [])
        raw_categories = list(store["categories"]) + list(store.get("adultCategories") or [])
    except (KeyError, TypeError) as exc:
        raise NuxtDataError(f"структура данных сайта изменилась: нет {exc}") from None

    titles = {c["id"]: c["title"] for c in raw_categories if c.get("url") not in IGNORED_SECTIONS}
    items: list[MenuItem] = []
    for product in raw_products:
        try:
            sections = tuple(titles[cid] for cid in product.get("categories_ids") or [] if cid in titles)
            name = str(product["name"]).strip()
            description = str(product.get("description") or "").strip()
            weight = (product.get("info") or {}).get("weight") or None
            items.append(
                MenuItem(
                    source_id=int(product["id"]),
                    name=name,
                    description=description,
                    weight=str(weight) if weight else None,
                    url=str(product.get("url") or ""),
                    sections=sections,
                    content_hash=content_hash(name, description, weight, sections),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise NuxtDataError(f"структура позиции изменилась: нет {exc}") from None
    return items, slugs


class Eda1Source:
    def __init__(self, settings: Settings, fetch: Callable[[str], Awaitable[str]] | None = None):
        self._delay = settings.crawl_delay_seconds
        self._user_agent = settings.crawl_user_agent
        self._ssl = settings_ssl_context(settings)
        self._fetch = fetch or self._http_fetch

    async def _http_fetch(self, url: str) -> str:
        return await asyncio.to_thread(http_get, url, self._user_agent, self._ssl)

    async def _get(self, url: str) -> str:
        last: Exception | None = None
        for attempt in range(FETCH_ATTEMPTS):
            try:
                return await self._fetch(url)
            except Exception as exc:
                last = exc
                if attempt + 1 < FETCH_ATTEMPTS:
                    await asyncio.sleep(self._delay * (attempt + 1))
        raise RuntimeError(f"{url}: {last}")

    @staticmethod
    def _merge(result: CrawlResult, items: list[MenuItem]) -> None:
        for item in items:
            known = result.items.get(item.source_id)
            if known is None:
                result.items[item.source_id] = item
            elif known.content_hash != item.content_hash:
                result.conflicts += 1

    async def crawl(self, emit: Emit) -> CrawlResult:
        try:
            first_items, slugs = parse_city_page(await self._get(f"{BASE_URL}/{PRIMARY_CITY}"))
        except Exception as exc:
            raise CrawlError(f"не удалось загрузить первую страницу: {exc}") from None

        order = [PRIMARY_CITY] + sorted(s for s in slugs if s != PRIMARY_CITY)
        result = CrawlResult(cities_ok=[PRIMARY_CITY])
        self._merge(result, first_items)
        await emit(type="city", slug=PRIMARY_CITY, index=1, total=len(order), items=len(first_items))

        for index, slug in enumerate(order[1:], start=2):
            await asyncio.sleep(self._delay)
            try:
                items, _ = parse_city_page(await self._get(f"{BASE_URL}/{slug}"))
            except Exception as exc:
                result.cities_failed[slug] = str(exc)[:200]
                log.warning("Город %s не загрузился: %s", slug, exc)
                await emit(type="city_failed", slug=slug, index=index, total=len(order), error=str(exc)[:200])
                continue
            result.cities_ok.append(slug)
            self._merge(result, items)
            await emit(type="city", slug=slug, index=index, total=len(order), items=len(items))
        return result
