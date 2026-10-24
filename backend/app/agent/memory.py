"""Реализации интерфейсов в памяти: для тестов и запуска без базы."""

from app.agent.models import SaveOutcome, SupplierDraft
from app.agent.pages import Page


class MemoryPageCache:
    def __init__(self) -> None:
        self.pages: dict[str, Page] = {}

    async def get(self, url: str) -> Page | None:
        return self.pages.get(url)

    async def put(self, page: Page) -> None:
        self.pages[page.url] = page


class MemorySink:
    """Хранит поставщиков по домену; ИНН уникален между поставщиками."""

    def __init__(self) -> None:
        self.suppliers: dict[str, SupplierDraft] = {}
        self.research: dict[str, dict] = {}

    async def save(self, draft: SupplierDraft) -> SaveOutcome:
        for inn in draft.values("inn"):
            for domain, other in self.suppliers.items():
                if domain != draft.domain and inn in other.values("inn"):
                    return SaveOutcome(domain, "", False, duplicate_of=domain)
        known = self.suppliers.get(draft.domain)
        if known is None:
            self.suppliers[draft.domain] = draft
        else:
            have = {(f.field, f.value) for f in known.fields}
            known.fields += [f for f in draft.fields if (f.field, f.value) not in have]
        name = (self.suppliers[draft.domain].values("name") or [draft.domain])[0]
        return SaveOutcome(draft.domain, name, known is None)

    async def record_research(self, domain: str, pages: list[str], missing: list[str]) -> None:
        self.research[domain] = {"pages": pages, "missing": missing}
