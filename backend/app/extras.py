"""Optional search requirements ("Дополнительно"): monthly volume, documents, storage, delivery needs.

They are passed to the agent, checked deterministically against the supplier's collected text (no LLM, no score impact in this
version) and reported as "Не найдено" warnings plus questions for the call checklist.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

DOCUMENTS = {
    "haccp": ("ХАССП", re.compile(r"хасс?п|haccp")),
    "iso": ("ISO", re.compile(r"\biso\b|\bисо\b")),
    "mercury": ("ветеринарные документы (Меркурий)", re.compile(r"меркури|ветеринарн|ветсвидет|вет\.?\s*свидет")),
}
STORAGE = {
    "chilled": ("охлаждённое хранение", re.compile(r"охлажд|холодильн|chilled|\+[0-5]\s*°?\s*с\b")),
    "frozen": ("замороженное хранение", re.compile(r"заморож|морозильн|frozen|шоков")),
}


class Extras(BaseModel):
    volume_value: float | None = Field(None, gt=0, le=1_000_000)
    volume_unit: Literal["кг", "т", "л", "шт"] | None = None
    documents: list[Literal["haccp", "iso", "mercury"]] = Field(default_factory=list, max_length=3)
    storage: Literal["chilled", "frozen"] | None = None
    delivery: str | None = Field(None, max_length=200)

    def as_dict(self) -> dict | None:
        """Only what the user filled in; None when nothing is set (nothing is stored then)."""
        data = {
            "volume": {"value": self.volume_value, "unit": self.volume_unit or "кг"} if self.volume_value else None,
            "documents": list(dict.fromkeys(self.documents)) or None,
            "storage": self.storage,
            "delivery": re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f]", " ", self.delivery or "")).strip() or None,
        }
        data = {k: v for k, v in data.items() if v}
        return data or None


def describe(extras: dict | None) -> list[str]:
    """Human-readable requirement lines (Russian), used in the agent prompt and the export."""
    if not extras:
        return []
    lines = []
    if volume := extras.get("volume"):
        lines.append(f"объём закупки около {volume['value']:g} {volume['unit']} в месяц")
    if docs := extras.get("documents"):
        lines.append("нужны документы: " + ", ".join(DOCUMENTS[d][0] for d in docs))
    if storage := extras.get("storage"):
        lines.append("нужно " + STORAGE[storage][0])
    if delivery := extras.get("delivery"):
        lines.append(f"доставка: {delivery}")
    return lines


def check(extras: dict | None, texts: list[str]) -> tuple[list[str], list[str]]:
    """(labels not found in the supplier's texts, questions for the call). Volume and delivery wishes cannot be checked: question only."""
    if not extras:
        return [], []
    blob = " ".join(texts).lower().replace("ё", "е")
    missing: list[str] = []
    questions: list[str] = []
    for key in extras.get("documents", []):
        label, pattern = DOCUMENTS[key]
        if not pattern.search(blob):
            missing.append(label)
            questions.append(f"Уточните, есть ли {label}.")
    if storage := extras.get("storage"):
        label, pattern = STORAGE[storage]
        if not pattern.search(blob):
            missing.append(label)
            questions.append(f"Уточните, обеспечивают ли {label}.")
    if volume := extras.get("volume"):
        questions.append(f"Уточните, смогут ли поставлять около {volume['value']:g} {volume['unit']} в месяц.")
    if delivery := extras.get("delivery"):
        questions.append(f"Уточните, подходит ли доставка: {delivery}.")
    return missing, questions
