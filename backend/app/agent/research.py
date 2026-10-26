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
    (("phone", "address"), "контакты телефон адрес как нас найти"),
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
    head = min(500, chars // 6)  # начало страницы (название, первый экран); меню навигации вырезает загрузчик
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
delivery_terms (условия и стоимость доставки, самовывоз), moq (минимальный заказ или партия; сумма заказа для \
бесплатной доставки — это delivery_terms, а не moq), price_hint (конкретные цены с единицами измерения именно на нужный \
продукт; цены на другие товары не включай), certificates (сертификаты, декларации, ГОСТ, ХАССП).

Правила:
- quote — ДОСЛОВНЫЙ фрагмент страницы (до 200 символов), source_url — адрес страницы из заголовка «=== адрес ===», с которой взят фрагмент.
- Значение бери только из текста. Не выдумывай и не додумывай: если на страницах поля нет, не включай его.
- Телефонов не больше трёх, email не больше двух; остальные поля по одному значению (для price_hint можно до трёх).
- Числа (цены, минимальный заказ) переписывай как в тексте.
- price_hint пиши одной строкой «товар, фасовка — цена с единицей»: «Рис для суши, 25 кг — 2100 руб.». Название товара обязательно: цена без названия товара бесполезна. Цены других товаров в эту строку не добавляй.
- moq пиши с единицей измерения: «от 11 000 ₽», «от 20 кг», «от 1 шт.». Число без единицы не записывай."""


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
    """Обязательные по ТЗ поля, которых ещё нет у поставщика. Телефон нужен и при найденном email: закупщик звонит."""
    missing = [f for f in REQUIRED_FIELDS if f not in have]
    if "phone" not in have:
        missing.insert(0, "phone")
    return missing


# --- проверка кандидата до подробного изучения ---

QUALIFY_SYSTEM = """Ты помогаешь закупщику сети суши- и пицца-доставки отобрать поставщиков. По главной странице сайта \
и отрывку из поиска определи:
1. supplier — это компания, которая продаёт бизнесу (производитель, дистрибьютор, оптовая база, поставщик для HoReCa)? \
«no», если это розничный магазин только для частных покупателей, ресторан, кафе, доставка готовой еды, СМИ, блог, \
справочник, маркетплейс или сайт не о продаже продуктов.
2. product — продаёт ли компания нужный продукт или продукты этой категории?
Ответ — только JSON: {"supplier": "yes|no|unclear", "product": "yes|no|unclear", "reason": "одно короткое предложение"}
«no» ставь, только если это явно следует из текста; если по главной странице не понять, ставь «unclear»."""

QUALIFY_CHARS = 2500


@dataclass
class Qualification:
    supplier: str = "unclear"  # yes | no | unclear
    product: str = "unclear"
    reason: str = ""
    error: str | None = None

    @property
    def rejected(self) -> bool:
        """Отсеивается явное «нет» и сайт, по которому неясно и то и другое (так проходили колбасная база и посуда).

        Сбой самой проверки сайт не отсеивает: лучше изучить лишний, чем потерять поставщика.
        """
        if self.error:
            return False
        return self.supplier == "no" or self.product == "no" or (self.supplier == "unclear" and self.product == "unclear")


def build_qualify_messages(request: RunRequest, home: Page, snippet: str | None) -> list[dict]:
    product = request.category + (f" (также: {', '.join(request.synonyms)})" if request.synonyms else "")
    found = f"\nОтрывок из поиска: {snippet}" if snippet else ""
    user = (f"Нужный продукт: {product}.{found}\n\n=== {home.url} ===\nЗаголовок: {home.title}\n"
            f"{home.text[:QUALIFY_CHARS]}")
    return [{"role": "system", "content": QUALIFY_SYSTEM}, {"role": "user", "content": user}]


def parse_qualification(text: str | None) -> Qualification:
    """Ответ не по формату не отсеивает сайт: лучше изучить лишний, чем потерять поставщика."""
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    try:
        data = json.loads(cleaned[start : end + 1]) if start != -1 and end > start else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        return Qualification(error=f"ответ проверки не разобрался: {(text or '')[:120]!r}")

    def verdict(key: str) -> str:
        value = str(data.get(key, "")).strip().lower()
        return value if value in ("yes", "no", "unclear") else "unclear"

    return Qualification(supplier=verdict("supplier"), product=verdict("product"), reason=str(data.get("reason", "")).strip()[:200])
