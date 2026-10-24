"""Данные, которыми обмениваются части агента."""

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.pages import Page


@dataclass(frozen=True)
class RegionRef:
    name: str
    role: str  # primary | neighbor


@dataclass(frozen=True)
class RunRequest:
    category: str
    synonyms: tuple[str, ...] = ()
    regions: tuple[RegionRef, ...] = ()  # пусто — вся Россия
    supplier_type: str | None = None
    target: int = 5


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
    async def search(self, query: str) -> list[SearchHit]: ...


class PlacesProvider(Protocol):
    async def search(self, query: str, city: str) -> list[Place]: ...


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> Page: ...


class SupplierSink(Protocol):
    async def save(self, draft: SupplierDraft) -> SaveOutcome: ...

    async def record_research(self, domain: str, pages: list[str], missing: list[str]) -> None:
        """Запоминает, какие страницы сайта изучены и каких обязательных полей на них не нашлось."""
        ...


class PageCache(Protocol):
    async def get(self, url: str) -> Page | None: ...

    async def put(self, page: Page) -> None: ...
