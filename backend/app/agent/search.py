"""Поисковые провайдеры: Yandex Search API (веб-поиск) и 2GIS (организации по рубрикам и городам)."""

import asyncio
import base64
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from app.agent.models import Place, SearchHit
from app.agent.validation import host_of
from app.config import Settings
from app.net import build_ssl_context

YANDEX_URL = "https://searchapi.api.cloud.yandex.net/v2/web/search"
DGIS_URL = "https://catalog.api.2gis.com/3.0/items"


class SearchError(RuntimeError):
    """Отказ поиска с подробностями для разбора: HTTP-статус, код Яндекса и request-id (по нему ищет поддержка)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None, request_id: str | None = None):
        super().__init__(message)
        self.message, self.status, self.code, self.request_id = message, status, code, request_id

    def details(self) -> dict:
        return {"status": self.status, "code": self.code, "request_id": self.request_id, "text": self.message[:300]}


class SearchLimited(SearchError):
    """Поиск временно отказал (лимит запросов, 429, сбой на стороне Яндекса): запрос стоит повторить."""


# коды ошибок в XML-ответе: 15 — ничего не найдено (это не ошибка), 55 — превышен лимит запросов в секунду
_NO_RESULTS, _RPS_LIMIT = "15", "55"


def parse_yandex_xml(xml: str, request_id: str | None = None) -> list[SearchHit]:
    root = ET.fromstring(xml)
    error = root.find(".//error")
    if error is not None:
        code, text = error.get("code", ""), " ".join("".join(error.itertext()).split())
        if code == _NO_RESULTS:
            return []
        kind = SearchLimited if code == _RPS_LIMIT else SearchError
        raise kind(f"Yandex Search: ошибка {code}: {text[:200]}", status=200, code=code, request_id=request_id)
    hits: list[SearchHit] = []
    for doc in root.iter("doc"):
        url = (doc.findtext("url") or "").strip()
        domain = host_of(url)
        if not url or not domain:
            continue
        title_node = doc.find("title")
        title = " ".join("".join(title_node.itertext()).split()) if title_node is not None else ""
        passages = doc.find("passages")
        snippet = " ".join(
            " ".join("".join(p.itertext()).split()) for p in (passages.findall("passage") if passages is not None else [])
        )
        hits.append(SearchHit(title=title, url=url, snippet=snippet[:400], domain=domain))
    return hits


def http_error(status: int, body: str, request_id: str | None) -> SearchError:
    """Ответ вида {"code":8, "message":"search API backends are overloaded: web search rate limit exceeded…",
    "details":[{"requestId":"…"}]}: код gRPC 8 — исчерпан ресурс (лимит). 429 и 5xx стоит повторить."""
    code, text = None, body[:200]
    try:
        data = json.loads(body)
        code, text = str(data.get("code")) if data.get("code") is not None else None, str(data.get("message") or text)
        request_id = request_id or next((d.get("requestId") for d in data.get("details") or [] if d.get("requestId")), None)
    except (json.JSONDecodeError, AttributeError):
        pass
    kind = SearchLimited if status == 429 or status >= 500 else SearchError
    return kind(f"Yandex Search: HTTP {status}{f', код {code}' if code else ''}: {text[:200]}", status=status, code=code,
                request_id=request_id)


# запросы к поиску идут строго по одному на весь процесс: агент изучает сайты параллельно, а одновременные
# запросы Яндекс отбивает ответом 429. Замок берётся в потоке запроса, поэтому общий для всех запусков
_ONE_AT_A_TIME = threading.Lock()


class YandexSearch:
    """Веб-поиск Yandex Search API v2. Ключ: YANDEX_SEARCH_API_KEY или общий с моделью LLM_API_KEY."""

    retry_delays: tuple[float, ...] = (2, 5, 10)  # пауза перед повтором, с: лимит обычно снимается за секунды

    def __init__(self, settings: Settings, per_page: int = 10):
        key = settings.search_api_key
        if key is None or not settings.yandex_cloud_folder_id:
            raise RuntimeError("для поиска нужны YANDEX_CLOUD_FOLDER_ID и ключ (YANDEX_SEARCH_API_KEY или LLM_API_KEY)")
        self._key = key.get_secret_value()
        self._folder = settings.yandex_cloud_folder_id
        self._per_page = per_page
        self._ssl = build_ssl_context(settings.extra_ca_bundle)
        self.errors: list[dict] = []  # неудачные попытки с подробностями; инструменты забирают их через drain_errors

    def _request(self, query: str, region: int | None = None) -> tuple[str, str | None]:
        """Возвращает (XML выдачи, request-id). Бросает SearchError с подробностями."""
        body = {
            "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query, "familyMode": "FAMILY_MODE_NONE", "page": "0"},
            "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": str(self._per_page), "docsInGroup": "1"},
            "maxPassages": "2",
            **({"region": str(region)} if region else {}),  # код региона (lr): выдача ранжируется для этого места
            "l10N": "LOCALIZATION_RU",
            "folderId": self._folder,
            "responseFormat": "FORMAT_XML",
        }
        request = urllib.request.Request(
            YANDEX_URL,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Api-Key {self._key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _ONE_AT_A_TIME:
            try:
                with urllib.request.urlopen(request, timeout=40, context=self._ssl) as response:
                    data = json.loads(response.read())
                    request_id = response.headers.get("x-request-id")
            except urllib.error.HTTPError as exc:
                raise http_error(exc.code, exc.read().decode("utf-8", "replace"), exc.headers.get("x-request-id")) from None
        return base64.b64decode(data["rawData"]).decode("utf-8", "replace"), request_id

    async def search(self, query: str, region: int | None = None) -> list[SearchHit]:
        """Запрос с повтором при временном отказе: без него один всплеск лимита срывал весь запуск.
        Каждая неудачная попытка записывается в errors с HTTP-статусом, кодом и request-id."""
        for attempt, delay in enumerate((*self.retry_delays, None), start=1):
            try:
                xml, request_id = await asyncio.to_thread(self._request, query, region)
                return parse_yandex_xml(xml, request_id)
            except SearchError as exc:
                retry = isinstance(exc, SearchLimited) and delay is not None
                self.errors.append({"query": query, "attempt": attempt, "retried": retry, **exc.details()})
                if not retry:
                    raise
                await asyncio.sleep(delay)
        return []

    def drain_errors(self, query: str | None = None) -> list[dict]:
        """Забирает записанные сбои (только этого запроса, если он задан: поиски по сайтам идут параллельно)."""
        taken = [e for e in self.errors if query is None or e["query"] == query]
        self.errors = [e for e in self.errors if e not in taken]
        return taken


def parse_dgis(data: dict) -> list[Place]:
    places: list[Place] = []
    for item in (data.get("result") or {}).get("items") or []:
        phones, website = [], None
        for group in item.get("contact_groups") or []:
            for contact in group.get("contacts") or []:
                if contact.get("type") == "phone" and contact.get("text"):
                    phones.append(contact["text"])
                elif contact.get("type") == "website" and contact.get("value"):
                    website = website or contact["value"]
        places.append(
            Place(
                id=str(item.get("id", "")),
                name=item.get("name", ""),
                address=item.get("address_name", ""),
                phones=tuple(phones),
                website=website,
                rubrics=tuple(r.get("name", "") for r in item.get("rubrics") or []),
            )
        )
    return places


class DGisPlaces:
    """Организации по запросу и городу из 2GIS Catalog API. ВНИМАНИЕ: не проверялся на реальном API (нет ключа)."""

    def __init__(self, settings: Settings):
        if settings.dgis_api_key is None or not settings.dgis_api_key.get_secret_value():
            raise RuntimeError("для 2GIS нужен DGIS_API_KEY")
        self._key = settings.dgis_api_key.get_secret_value()
        self._ssl = build_ssl_context(settings.extra_ca_bundle)

    def _request(self, query: str, city: str) -> dict:
        params = {
            "q": f"{query}, {city}",
            "type": "branch",
            "page_size": "10",
            "fields": "items.contact_groups,items.address,items.rubrics",
            "locale": "ru_RU",
            "key": self._key,
        }
        try:
            with urllib.request.urlopen(f"{DGIS_URL}?{urllib.parse.urlencode(params)}", timeout=30, context=self._ssl) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"2GIS: HTTP {exc.code}") from None

    async def search(self, query: str, city: str) -> list[Place]:
        return parse_dgis(await asyncio.to_thread(self._request, query, city))
