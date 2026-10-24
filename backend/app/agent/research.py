"""Исследование сайта поставщика: какие страницы открыть, как собрать для модели текст и как разобрать её ответ.

Код сам гарантирует, что нужные разделы сайта (контакты, о компании, доставка, прайс, сертификаты) открыты,
а модель только извлекает из них поля. Каждое поле всё равно проходит все проверки save_supplier (цитата дословно
на странице, значение следует из цитаты и т. д.).
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.agent.models import RunRequest
from app.agent.pages import Page
from app.agent.validation import FIELDS

# разделы сайта, которые нужно изучить, в порядке важности
TOPIC_WORDS: dict[str, tuple[str, ...]] = {
    "contacts": ("контакт", "contact", "реквизит", "связаться", "как нас найти"),
    "about": ("о компании", "о нас", "about", "компания", "кто мы", "о предприятии"),
    "delivery": ("доставк", "оплат", "delivery", "условия", "как заказать", "оптовик", "для партнер"),
    "price": ("прайс", "price", "цены", "каталог", "catalog", "продукц", "ассортимент", "товары"),
    "certs": ("сертификат", "документ", "качество", "декларац", "certificate", "лицензи", "гост"),
}
TOPIC_ORDER = ["contacts", "about", "delivery", "price", "certs"]

# какие обязательные поля (по ТЗ) ищем и каким запросом по сайту их искать, если на открытых страницах их нет
REQUIRED_FIELDS = ["description", "address", "regions", "delivery_terms", "moq", "price_hint", "certificates"]
SITE_QUERIES: list[tuple[tuple[str, ...], str]] = [
    (("moq", "price_hint"), "минимальный заказ опт цена прайс"),
    (("certificates",), "сертификаты декларация соответствия документы качество"),
    (("delivery_terms", "regions"), "доставка условия регионы оплата"),
    (("address",), "контакты адрес как нас найти"),
]
FIELD_LABELS = {
    "description": "чем занимается", "address": "адрес", "regions": "регионы работы", "delivery_terms": "условия доставки",
    "moq": "минимальный заказ", "price_hint": "цены", "certificates": "сертификаты", "phone": "телефон", "email": "email",
    "name": "название", "inn": "ИНН", "assortment": "ассортимент", "supplier_type": "тип поставщика",
}
_FOCUS_WORDS = re.compile(
    r"(руб|₽|цена|стоимост|прайс|от\s*\d+\s*(?:кг|шт|т\b|тонн|коробк|уп)|минимальн|мин\.|заказ|партия|доставк|самовывоз|"
    r"сертификат|декларац|гост|хассп|haccp|исо|iso|лиценз|инн|огрн|регион|област|край|республик|по\s+росси|адрес)",
    re.I,
)


def classify_link(url: str, text: str) -> str | None:
    """К какому разделу относится ссылка (по подписи и пути), либо None."""
    haystack = f"{text} {urlsplit(url).path}".lower()
    for topic in TOPIC_ORDER:
        if any(word in haystack for word in TOPIC_WORDS[topic]):
            return topic
    return None


def select_pages(
    links: list[tuple[str, str]], limit: int, exclude: set[str] | frozenset[str] = frozenset(), topics: list[str] | None = None
) -> list[str]:
    """По одной лучшей ссылке на раздел, в порядке важности разделов; уже открытые адреса пропускаются."""
    wanted = topics or TOPIC_ORDER
    best: dict[str, tuple[int, str]] = {}
    for url, text in links:
        if url in exclude:
            continue
        topic = classify_link(url, text)
        if topic in wanted:
            rank = len(urlsplit(url).path)  # короче путь — обычно главная страница раздела
            if topic not in best or rank < best[topic][0]:
                best[topic] = (rank, url)
    return [best[t][1] for t in TOPIC_ORDER if t in best][:limit]


def focus_text(text: str, chars: int) -> str:
    """Начало страницы и окна вокруг слов про цены, заказ, доставку, сертификаты: так в запрос попадает главное."""
    if len(text) <= chars:
        return text
    head = min(900, chars // 3)
    ranges: list[list[int]] = []
    for match in _FOCUS_WORDS.finditer(text, head):
        start, end = max(head, match.start() - 160), min(len(text), match.end() + 240)
        if ranges and start <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    parts, used = [text[:head]], head
    for start, end in ranges:
        piece = text[start:end]
        if used + len(piece) > chars:
            piece = piece[: max(0, chars - used)]
        if piece:
            parts.append(piece)
            used += len(piece)
        if used >= chars:
            break
    return "\n…\n".join(parts)


def build_evidence(pages: list[Page], chars_per_page: int) -> str:
    return "\n\n".join(f"=== {page.url} ===\n{focus_text(page.text, chars_per_page)}" for page in pages)


EXTRACTION_SYSTEM = """Ты извлекаешь данные о поставщике из текстов страниц его сайта для закупщика сети суши- и пицца-доставки. \
Ответ — только JSON без пояснений:
{"fields": [{"field": "phone", "value": "+7 (843) 555-01-23", "quote": "Телефон: +7 (843) 555-01-23", "source_url": "https://…"}]}

Допустимые поля: name (название компании), phone, email, inn, address (адрес офиса или производства), \
description (чем занимается, одно предложение из текста), assortment (основные товары), supplier_type \
(производитель, дистрибьютор или оптовик), regions (регионы и города, куда поставляют или где работают), \
delivery_terms (условия и стоимость доставки, самовывоз), moq (минимальный заказ или партия), price_hint \
(конкретные цены с единицами измерения, лучше на нужный продукт), certificates (сертификаты, декларации, ГОСТ, ХАССП).

Правила:
- quote — ДОСЛОВНЫЙ фрагмент страницы (до 200 символов), source_url — адрес страницы из заголовка «=== адрес ===», с которой взят фрагмент.
- Значение бери только из текста. Не выдумывай и не додумывай: если на страницах поля нет, не включай его.
- Телефонов не больше трёх, email не больше двух; остальные поля по одному значению (для price_hint можно до трёх).
- Числа (цены, минимальный заказ) переписывай как в тексте."""


@dataclass
class Extraction:
    entries: list[dict] = field(default_factory=list)
    error: str | None = None


def build_extraction_messages(request: RunRequest, evidence: str, focus: list[str] | None) -> list[dict]:
    product = request.category + (f" (также: {', '.join(request.synonyms)})" if request.synonyms else "")
    ask = ""
    if focus:
        ask = "\nОсобенно тщательно найди поля: " + ", ".join(f"{f} ({FIELD_LABELS.get(f, f)})" for f in focus) + "."
    user = f"Нужный продукт: {product}.{ask}\n\nСтраницы сайта:\n\n{evidence}"
    return [{"role": "system", "content": EXTRACTION_SYSTEM}, {"role": "user", "content": user}]


def parse_extraction(text: str | None) -> Extraction:
    """Разбирает ответ модели. Неизвестные поля и записи не по формату отбрасываются, проверка цитат — позже."""
    if not text:
        return Extraction(error="пустой ответ модели")
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return Extraction(error="в ответе модели нет JSON")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        return Extraction(error=f"ответ модели не разобрался как JSON: {exc}")
    raw = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return Extraction(error="в ответе нет списка fields")
    entries = []
    for item in raw:
        if isinstance(item, dict) and item.get("field") in FIELDS and all(isinstance(item.get(k), str) for k in ("value", "quote", "source_url")):
            entries.append({k: item[k] for k in ("field", "value", "quote", "source_url")})
    return Extraction(entries=entries)


def missing_fields(have: set[str]) -> list[str]:
    """Обязательные по ТЗ поля, которых ещё нет у поставщика (контакты считаются отдельно)."""
    missing = [f for f in REQUIRED_FIELDS if f not in have]
    if "phone" not in have and "email" not in have:
        missing.insert(0, "phone")
    return missing
