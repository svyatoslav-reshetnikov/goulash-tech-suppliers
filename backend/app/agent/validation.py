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


MIN_QUOTE = 5  # цитата «ООО» или «Да» есть на любой странице и ничего не подтверждает


def quote_in_text(quote: str, text: str) -> bool:
    quote = norm_text(quote)
    return len(quote) >= MIN_QUOTE and quote in norm_text(text)


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


_ADDRESS_NOISE = {"россия", "рф", "г", "город", "ул", "улица", "д", "дом", "вл", "владение", "с", "стр", "строение", "к", "корп", "корпус"}
_ADDRESS_ALIASES = {"обл": "область", "пр": "проспект", "пр-т": "проспект", "ш": "шоссе", "наб": "набережная", "пер": "переулок"}
# поля, у которых одно и то же сведение с разных страниц приходит в разной записи; остаётся самая полная
DEDUP_FIELDS = {"address", "assortment", "description"}


def _tokens(field: str, value: str) -> frozenset[str]:
    words = re.findall(r"[a-zа-я0-9]+", value.lower().replace("ё", "е"))
    if field == "address":
        words = [_ADDRESS_ALIASES.get(w, w) for w in words if w not in _ADDRESS_NOISE and not re.fullmatch(r"\d{6}", w)]
    return frozenset(words)


def near_duplicate(field: str, a: str, b: str) -> bool:
    """Одно и то же значение в разной записи: слова одного целиком входят в другое («Москва, ул. Ленина, 5» и «127000, Россия, г. Москва, ул. Ленина, 5»)."""
    ta, tb = _tokens(field, a), _tokens(field, b)
    return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def collapse_duplicates(field: str, values: list[str]) -> list[str]:
    """Убирает значения, которые целиком входят в другое, более полное; порядок остальных сохраняется."""
    if field not in DEDUP_FIELDS:
        return values
    kept: list[str] = []
    for value in sorted(values, key=len, reverse=True):
        if not any(near_duplicate(field, value, other) for other in kept):
            kept.append(value)
    return [v for v in values if v in kept]


def address_problem(value: str) -> str | None:
    """Адрес без номера дома («Москва» из статьи о технологии) адресом не считается."""
    if not re.search(r"\d", value) or len(_tokens("address", value)) < 2:
        return "это не адрес: нужен адрес с улицей и номером дома, например «г. Казань, ул. Бухарская, 4В». Название города или региона адресом не является"
    return None


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
    if field == "address" and (problem := address_problem(value)):
        return None, problem
    return value, None


_DESCRIPTIVE = {"delivery_terms", "moq", "price_hint", "certificates", "regions", "address", "description", "assortment"}


# служебные слова не подтверждают значение: «вся Россия» должно опираться на «Россия», а не на «вся»
_FILLER = {
    "для", "без", "или", "под", "при", "над", "про", "как", "так", "также", "это", "что", "все", "всё", "вся", "весь",
    "всей", "всех", "наш", "наша", "наши", "ваш", "ваша", "ваши", "мы", "вы", "the", "and", "for",
}
# организационно-правовые формы и общие слова в названиях: «ООО ТД Ромашка» подтверждается цитатой «Ромашка»
_NAME_FILLER = _FILLER | {
    "ооо", "оао", "зао", "пао", "ао", "ип", "нао", "тд", "гк", "пк", "кфх", "llc", "ltd", "компания", "группа",
    "компаний", "торговый", "дом", "общество", "ограниченной", "ответственностью",
}
# единицы у чисел: «от 10 кг» не подтверждается цитатой «скидка 10%»
_UNITS = [
    ("кг", r"кг|килограмм\w*"), ("т", r"т|тн|тонн\w*"), ("г", r"г|гр|грамм\w*"), ("шт", r"шт\w*|штук\w*"),
    ("уп", r"уп\w*"), ("кор", r"кор\w*"), ("ящ", r"ящ\w*"), ("пал", r"паллет\w*|палет\w*"), ("меш", r"меш\w*"),
    ("л", r"л|литр\w*"), ("руб", r"руб\w*|р|₽"), ("%", r"%"),
]
_UNIT_PATTERNS = [(name, re.compile(rf"\d\s*(?:{body})(?![a-zа-я])")) for name, body in _UNITS]
# тип поставщика должен быть назван в цитате своими словами
_TYPE_WORDS = {
    "производитель": ("производ", "завод", "фабрик", "комбинат", "собственн"),
    "дистрибьютор": ("дистриб", "официальн", "эксклюзивн"),
    "оптовик": ("опт",),
}


def _words(text: str, filler: set[str]) -> list[str]:
    return [w for w in re.findall(r"[a-zа-я0-9]+", norm_text(text)) if len(w) >= 2 and not w.isdigit() and w not in filler]


def _word_matches(word: str, others: list[str]) -> bool:
    """Одно слово в разных падежах: общее начало не короче 5 букв или короткое слово целиком («сыр» — «сыры»)."""
    for other in others:
        common = 0
        for a, b in zip(word, other):
            if a != b:
                break
            common += 1
        shorter = min(len(word), len(other))
        if common >= 5 or common == shorter or (shorter <= 5 and common >= 3 and common >= shorter - 1):
            return True
    return False


def words_supported(value: str, quote: str, filler: set[str] = _FILLER) -> bool:
    """Слова значения есть в цитате (с поправкой на падежи).

    Короткое значение (до трёх слов) должно опираться на цитату целиком: «Татарстан» не подтверждается цитатой
    «Москва и область». В длинном (описание, ассортимент) модель пересказывает, поэтому достаточно половины слов.
    """
    words, quoted = _words(value, filler), _words(quote, filler)
    if not words:
        return True  # значение из одних чисел и единиц: проверяется по ним
    found = sum(_word_matches(w, quoted) for w in words)
    return found == len(words) if len(words) <= 3 else found / len(words) >= 0.5


def units_supported(value: str, quote: str) -> bool:
    """Единицы при числах значения (кг, руб, шт…) есть и в цитате."""
    v, q = norm_text(value), norm_text(quote)
    return all(pattern.search(q) for _, pattern in _UNIT_PATTERNS if pattern.search(v))


def value_supported_by_quote(field: str, value: str, quote: str) -> bool:
    """Значение должно следовать из цитаты, а не быть выдумано рядом с настоящей цитатой."""
    if field == "phone":
        return digits(value)[-10:] in digits(quote)
    if field == "email":
        return value.lower() in quote.lower()
    if field == "inn":
        return digits(value) in digits(quote)
    if field == "supplier_type":
        return any(stem in norm_text(quote) for stem in _TYPE_WORDS.get(value, ()))
    if field == "name":  # название из одних «ООО», «Торговый дом» проверяется по всем словам
        filler = _NAME_FILLER if _words(value, _NAME_FILLER) else _FILLER
        return bool(_words(value, filler)) and words_supported(value, quote, filler) and numbers_supported(value, quote)
    if field in _DESCRIPTIVE and not (words_supported(value, quote) and units_supported(value, quote)):
        return False
    return numbers_supported(value, quote)


_STOP_WORDS = {"для", "без", "или", "под", "при"}


def product_stems(phrases: tuple[str, ...]) -> list[str]:
    """Основы слов продукта и его синонимов: «моцарелла» → «моцарел», «сыр» → «сыр»."""
    stems = []
    for phrase in phrases:
        for word in re.findall(r"[a-zа-я0-9]+", norm_text(phrase)):
            if len(word) == 3 and word not in _STOP_WORDS:
                stems.append(word)
            elif len(word) >= 4:
                stems.append(word[: max(4, len(word) - 2)])
    return stems


def mentions_product(text: str, stems: list[str]) -> bool:
    folded = norm_text(text)
    return any(re.search(rf"\b{re.escape(s)}", folded) for s in stems)


# «Бесплатная доставка при заказе от 11 000 ₽» и «при заказе от 5000 ₽ доставка бесплатно»: порог доставки, а не минимальный заказ
_FREE_DELIVERY_FROM = re.compile(
    r"бесплатн[^.;!]{0,60}?(?:при|от)\s+(?:заказ|сумм|покупк)\w*\s+(?:от\s+)?(\d[\d\s]*)"
    r"|(?:при|от)\s+(?:заказ|сумм|покупк)\w*\s+(?:от\s+)?(\d[\d\s]*)[^.;!]{0,40}?бесплатн"
)
_ORDER_MINIMUM = re.compile(r"минимал|минимум|мин\.")


def text_around(quote: str, text: str, chars: int = 200) -> str:
    """Фрагмент страницы вокруг цитаты: в прайсе цена часто стоит рядом с названием товара, а не в одной строке с ним."""
    norm, q = norm_text(text), norm_text(quote)
    at = norm.find(q)
    return norm[max(0, at - chars) : at + len(q) + chars] if at >= 0 else ""


def fact_problem(field: str, value: str, quote: str, product: list[str], context: str = "") -> str | None:
    """Смысловая проверка: цитата есть на странице, но говорит ли она о том, что записано в поле.

    product — основы слов искомого продукта (product_stems); пустой список отключает проверку цены.
    context — заголовок страницы и текст вокруг цитаты; у сохранённых раньше полей его нет, проверяется только цитата.
    """
    if field == "moq":
        text = re.sub(r"\b(руб|р)\.", r"\1", norm_text(quote))  # точка в «руб.» — не конец предложения
        thresholds = {n for m in _FREE_DELIVERY_FROM.finditer(text) for n in _numbers(m.group(1) or m.group(2))}
        amounts = _numbers(value)
        if amounts and amounts <= thresholds and not _ORDER_MINIMUM.search(text):
            return "это сумма заказа для бесплатной доставки, а не минимальный заказ: такое значение относится к delivery_terms"
    if field == "price_hint" and product and not mentions_product(f"{value} {quote} {context}", product):
        return "цена не на нужный продукт: ни в цитате, ни рядом с ней продукт не назван. Цены на другие товары не сохраняй"
    return None
