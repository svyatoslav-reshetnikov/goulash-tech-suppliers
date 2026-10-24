"""Поисковые провайдеры: Yandex Search API (веб-поиск) и 2GIS (организации по рубрикам и городам)."""

import asyncio
import base64
import json
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


def parse_yandex_xml(xml: str) -> list[SearchHit]:
    root = ET.fromstring(xml)
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


class YandexSearch:
    """Веб-поиск Yandex Search API v2. Ключ: YANDEX_SEARCH_API_KEY или общий с моделью LLM_API_KEY."""

    def __init__(self, settings: Settings, per_page: int = 10):
        key = settings.search_api_key
        if key is None or not settings.yandex_cloud_folder_id:
            raise RuntimeError("для поиска нужны YANDEX_CLOUD_FOLDER_ID и ключ (YANDEX_SEARCH_API_KEY или LLM_API_KEY)")
        self._key = key.get_secret_value()
        self._folder = settings.yandex_cloud_folder_id
        self._per_page = per_page
        self._ssl = build_ssl_context(settings.extra_ca_bundle)

    def _request(self, query: str) -> str:
        body = {
            "query": {"searchType": "SEARCH_TYPE_RU", "queryText": query, "familyMode": "FAMILY_MODE_NONE", "page": "0"},
            "groupSpec": {"groupMode": "GROUP_MODE_DEEP", "groupsOnPage": str(self._per_page), "docsInGroup": "1"},
            "maxPassages": "2",
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
        try:
            with urllib.request.urlopen(request, timeout=40, context=self._ssl) as response:
                data = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Yandex Search: HTTP {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}") from None
        return base64.b64decode(data["rawData"]).decode("utf-8", "replace")

    async def search(self, query: str) -> list[SearchHit]:
        return parse_yandex_xml(await asyncio.to_thread(self._request, query))


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
