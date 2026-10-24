"""Проверки данных поставщика. Выполняются кодом, а не моделью: модели доверять факты нельзя."""

import ipaddress
import re
from urllib.parse import urlsplit

FIELDS = {
    "name", "phone", "email", "inn", "address", "description", "assortment", "supplier_type",
    "regions", "delivery_terms", "moq", "price_hint", "certificates",
}
SUPPLIER_TYPES = {"производитель", "дистрибьютор", "оптовик"}
CONTACT_FIELDS = {"phone", "email"}

_EMAIL = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def norm_text(text: str) -> str:
    """Для сравнения цитаты со страницей: регистр, ё→е, без кавычек, одинаковые тире, схлопнутые пробелы."""
    text = text.lower().replace("ё", "е")
    text = re.sub("[\"'«»“”„‘’`]", "", text)  # кавычки любого вида: модель часто меняет их местами
    text = text.replace("—", "-").replace("–", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def quote_in_text(quote: str, text: str) -> bool:
    quote = norm_text(quote)
    return len(quote) >= 3 and quote in norm_text(text)


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _numbers(text: str) -> set[str]:
    glued = re.sub(r"(?<=\d)[\s ](?=\d)", "", text)  # «30 000» → «30000»
    return {n.replace(",", ".") for n in _NUMBER.findall(glued)}


def numbers_supported(value: str, quote: str) -> bool:
    """Каждое число из значения (минимальный заказ, цена, срок) должно встречаться в цитате."""
    return _numbers(value) <= _numbers(quote)


def normalize_phone(value: str) -> str | None:
    d = digits(value)
    if len(d) == 11 and d[0] in "78":
        d = d[1:]
    if len(d) != 10 or d[0] not in "3489":  # коды городов 3xx-4xx-8xx, мобильные 9xx, бесплатные 800
        return None
    return f"+7 ({d[:3]}) {d[3:6]}-{d[6:8]}-{d[8:]}"


def normalize_email(value: str) -> str | None:
    value = value.strip().lower()
    return value if _EMAIL.match(value) else None


def valid_inn(value: str) -> str | None:
    """ИНН юрлица (10 цифр) или ИП (12 цифр) с проверкой контрольной суммы."""
    d = digits(value)
    if len(d) not in (10, 12):
        return None
    nums = [int(c) for c in d]

    def check(coefficients: list[int]) -> int:
        return sum(n * c for n, c in zip(nums, coefficients)) % 11 % 10

    if len(d) == 10:
        return d if check([2, 4, 10, 3, 5, 9, 4, 6, 8]) == nums[9] else None
    ok = check([7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == nums[10] and check([3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8]) == nums[11]
    return d if ok else None


def host_of(url: str) -> str | None:
    try:
        host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # сайт поставщика не может быть IP-адресом
    except ValueError:
        return host


def same_site(a: str, b: str) -> bool:
    """Одинаковый домен или поддомен: kazan.example.ru относится к example.ru."""
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def normalize_value(field: str, value: str) -> tuple[str | None, str | None]:
    """Возвращает (нормализованное значение, причина отказа)."""
    value = value.strip()
    if not value:
        return None, "пустое значение"
    if field == "phone":
        phone = normalize_phone(value)
        return (phone, None) if phone else (None, "это не российский номер телефона")
    if field == "email":
        email = normalize_email(value)
        return (email, None) if email else (None, "некорректный email")
    if field == "inn":
        inn = valid_inn(value)
        return (inn, None) if inn else (None, "некорректный ИНН (длина или контрольная сумма)")
    if field == "supplier_type":
        kind = value.lower()
        return (kind, None) if kind in SUPPLIER_TYPES else (None, "допустимо: производитель, дистрибьютор, оптовик")
    if len(value) > 600:
        return None, "значение слишком длинное (до 600 символов)"
    return value, None


_DESCRIPTIVE = {"delivery_terms", "moq", "price_hint", "certificates", "regions", "address", "description", "assortment"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-zа-я0-9]+", norm_text(text)) if len(w) >= 5}


def words_supported(value: str, quote: str, threshold: float = 0.5) -> bool:
    """Не меньше половины значимых слов значения встречаются в цитате (по началу слова, чтобы не мешали падежи).

    Защита от значения, которое лишь по числам совпадает с цитатой («бесплатная доставка…» под цитатой про сумму заказа).
    Короткие значения (меньше трёх значимых слов) проверяются только по числам.
    """
    value_stems = _stems(value)
    if len(value_stems) < 3:
        return True
    return len(value_stems & _stems(quote)) / len(value_stems) >= threshold


def value_supported_by_quote(field: str, value: str, quote: str) -> bool:
    """Значение должно следовать из цитаты, а не быть выдумано рядом с настоящей цитатой."""
    if field == "phone":
        return digits(value)[-10:] in digits(quote)
    if field == "email":
        return value.lower() in quote.lower()
    if field == "inn":
        return digits(value) in digits(quote)
    if field in _DESCRIPTIVE and not words_supported(value, quote):
        return False
    return numbers_supported(value, quote)
