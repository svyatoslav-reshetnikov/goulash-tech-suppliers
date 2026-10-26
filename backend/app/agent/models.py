"""Данные, которыми обмениваются части агента."""

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.pages import Page


@dataclass(frozen=True)
class RegionRef:
    name: str
    role: str  # primary | neighbor
    lr: int | None = None  # код региона Яндекс Поиска: свой поиск на каждый регион


@dataclass(frozen=True)
class RunRequest:
    category: str
    synonyms: tuple[str, ...] = ()
    regions: tuple[RegionRef, ...] = ()  # пусто — вся Россия
    target: int = 5
    search_region: int | None = None  # код региона Яндекс Поиска (lr): выдача ранжируется для этого места
    city: str | None = None  # выбранный город: по нему ищут в первую очередь (иначе в запросах только регион)


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    domain: str


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    address: str
    phones: tuple[str, ...]
    website: str | None
    rubrics: tuple[str, ...]


@dataclass(frozen=True)
class AcceptedField:
    field: str
    value: str
    quote: str
    source_url: str
    snapped: bool = False  # цитата заменена дословным фрагментом страницы: модель исказила свою


@dataclass
class SupplierDraft:
    website: str
    domain: str
    fields: list[AcceptedField] = field(default_factory=list)

    def values(self, name: str) -> list[str]:
        return [f.value for f in self.fields if f.field == name]


@dataclass(frozen=True)
class SaveOutcome:
    supplier_id: int | str
    name: str
    created: bool
    duplicate_of: str | None = None  # домен поставщика, у которого уже такой ИНН


class SearchProvider(Protocol):
    async def search(self, query: str, region: int | None = None, page: int = 0) -> list[SearchHit]: ...


class PlacesProvider(Protocol):
    async def search(self, query: str, city: str) -> list[Place]: ...


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> Page: ...


class SupplierSink(Protocol):
    async def save(self, draft: SupplierDraft) -> SaveOutcome: ...

    async def record_research(self, domain: str, pages: list[str], missing: list[str], qualification: dict | None = None) -> None:
        """Запоминает, какие страницы сайта изучены, каких обязательных полей на них не нашлось и итог проверки сайта."""
        ...

    async def reuse(self, domain: str, max_age_days: int) -> "Reused | None":
        """Поставщик, подробно изученный не раньше max_age_days назад: привязывается к запуску без нового извлечения."""
        ...


@dataclass(frozen=True)
class Reused:
    name: str
    fields: dict[str, tuple[str, ...]]  # поле → значения: по ним проверяется, подтверждён ли товар


class PageCache(Protocol):
    async def get(self, url: str) -> Page | None: ...

    async def put(self, page: Page) -> None: ...
