"""Deterministic parsing of raw price and minimum-order strings into structured values.

The agent stores raw strings ("Рис для суши 'Легендагро Приморье' ГОСТ 25кг/м 2 100 руб."). All arithmetic happens here, never in
the LLM. When a string is ambiguous the parser still returns its best candidate but marks it `needs_confirmation`, and the
UI shows the raw string with the note "Уточните цену". Nothing is ever shown as a bare number without a unit.
"""

import re
from dataclasses import asdict, dataclass

from app.ranking.product import first_matching_phrase

BASE_UNITS = {"кг": ("kg", 1.0), "kg": ("kg", 1.0), "г": ("kg", 0.001), "гр": ("kg", 0.001), "л": ("l", 1.0), "мл": ("l", 0.001),
              "шт": ("pcs", 1.0), "уп": ("pcs", 1.0)}
BASE_LABELS = {"kg": "кг", "l": "л", "pcs": "шт."}
CONFIRM = "Уточните цену"

_PRICE = re.compile(r"(?<![\d.,])(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,](\d{1,2}))?\s*(?:₽|руб(?:лей|ля|\.)?|р\.?(?![а-яa-z]))", re.I)
_PACK = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|гр|г|мл|л|шт|уп)(?![а-яa-z])", re.I)
_PER_UNIT = re.compile(r"(?:/|за\s+|1\s*)(кг|kg|л|шт|уп)(?![а-яa-z])", re.I)
_UNIT_TOKEN = re.compile(r"^(?:\d+(?:[.,]\d+)?)?(?:кг|kg|г|гр|л|мл|шт|уп|м|мешок)(?:/\w+)?$", re.I)


@dataclass
class Price:
    item_name: str | None
    pack_value: float | None
    pack_unit: str | None  # unit as written (кг, л, шт, ...)
    price_rub: float | None
    price_per_base_unit: float | None
    base_unit: str | None  # kg | l | pcs
    raw: str
    source_ref: str
    needs_confirmation: bool
    note: str | None
    display: str
    matches_product: bool = False


@dataclass
class MinOrder:
    value: float | None
    unit: str | None  # RUB | kg | pcs
    raw: str
    source_ref: str
    display: str
    note: str | None = None


def _num(text: str) -> float:
    return float(re.sub(r"[  ]", "", text).replace(",", "."))


def money(value: float) -> str:
    whole = f"{value:,.0f}".replace(",", " ") if value >= 10 or value == int(value) else f"{value:,.2f}".replace(",", " ")
    return f"{whole} ₽"


def _per_unit_text(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ") if value >= 10 else f"{value:.1f}".replace(".", ",")


def _item(before: str) -> str | None:
    """Item name: the text before the price without the pack size and stray unit letters."""
    text = _PACK.sub("", before)
    text = re.sub(r"[/\\]\s*\w{1,3}\b", " ", text)  # "/м", "/уп" left over from the pack
    text = re.sub(r"\s+", " ", text).strip(" \t-–—:;,.|()[]")
    return text if len(text) >= 2 else None


def parse_prices(raw: str, source_ref: str = "", phrases: tuple[str, ...] = ()) -> list[Price]:
    """One Price per money amount found in the string; a string without amounts gives one unparsed Price."""
    matches = list(_PRICE.finditer(raw))
    if not matches:
        return [Price(None, None, None, None, None, None, raw, source_ref, True, CONFIRM, f"{raw} · {CONFIRM}", False)]
    prices: list[Price] = []
    previous_end = 0
    for match in matches:
        before = raw[previous_end : match.start()]
        after = raw[match.end() : match.end() + 12]
        previous_end = match.end()
        amount = _num(match.group(1) + (f".{match.group(2)}" if match.group(2) else ""))

        pack_found = list(_PACK.finditer(before))
        pack_value, pack_unit = (None, None)
        if pack_found:
            pack_value, pack_unit = _num(pack_found[-1].group(1)), pack_found[-1].group(2).lower()
        else:
            per = _PER_UNIT.search(after) or _PER_UNIT.search(before[-12:])
            if per:
                pack_value, pack_unit = 1.0, per.group(1).lower()
        base_unit, per_base = None, None
        if pack_value and pack_unit in BASE_UNITS:
            base_unit, factor = BASE_UNITS[pack_unit]
            per_base = amount / (pack_value * factor)

        # "25кг/м 2 100 руб.": "2 100" could be the price, or "2" belongs to the pack and the price is "100"
        grouped = bool(re.search(r"[  ]", match.group(1)))
        head = match.group(1).split()[0] if grouped else ""
        tail_before = before.rstrip().split(" ")[-1] if before.strip() else ""
        ambiguous = grouped and len(head) <= 2 and bool(_UNIT_TOKEN.match(tail_before))

        item = _item(before)
        name = item or None
        pack_label = BASE_LABELS.get(BASE_UNITS.get(pack_unit or "", ("",))[0], pack_unit)
        pack_text = f", {pack_value:g} {pack_label}" if pack_value and pack_unit and pack_value != 1.0 else ""
        per_text = f" (≈{_per_unit_text(per_base)} ₽/{BASE_LABELS[base_unit]})" if per_base is not None and base_unit and pack_value != 1.0 else ""
        unit_suffix = f"/{BASE_LABELS[base_unit]}" if base_unit and pack_value == 1.0 else ""
        display = f"{name or 'Позиция не указана'}{pack_text} — {money(amount)}{unit_suffix}{per_text}"
        note = None
        if ambiguous:
            note = f"{CONFIRM}: в строке может быть и {money(_num(match.group(1).split(' ', 1)[1] if ' ' in match.group(1) else match.group(1)))}"
        elif not name:
            note = "Не указано, на какой товар цена"
        prices.append(
            Price(name, pack_value, pack_unit, amount, per_base, base_unit, raw, source_ref, ambiguous, note, display,
                  matches_product=bool(name and phrases and first_matching_phrase(name, phrases) is not None))
        )
    return prices


_MIN_RUB = re.compile(r"(\d{1,3}(?:[  ]\d{3})+|\d+)(?:[.,]\d+)?\s*(?:₽|руб(?:лей|ля|\.)?|р\.?(?![а-яa-z]))", re.I)
_MIN_KG = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|kg|т|тонн\w*)(?![а-яa-z])", re.I)
_MIN_PCS = re.compile(r"(\d+)\s*(шт|штук\w*|уп|упаков\w*|коробк\w*|пач\w*)(?![а-яa-z])", re.I)


def parse_min_order(raws: list[tuple[str, str]]) -> MinOrder | None:
    """One minimum-order line from the stored strings: money first, then weight, then pieces; others go to a note."""
    parsed: list[MinOrder] = []
    for raw, ref in raws:
        if m := _MIN_RUB.search(raw):
            value = _num(m.group(1))
            parsed.append(MinOrder(value, "RUB", raw, ref, f"от {money(value)}"))
        elif m := _MIN_KG.search(raw):
            value = _num(m.group(1)) * (1000 if m.group(2).lower().startswith("т") else 1)
            parsed.append(MinOrder(value, "kg", raw, ref, f"от {value:g} кг"))
        elif m := _MIN_PCS.search(raw):
            parsed.append(MinOrder(float(m.group(1)), "pcs", raw, ref, f"от {m.group(1)} шт."))
        else:
            parsed.append(MinOrder(None, None, raw, ref, f"{raw} · Уточните единицы", "Уточните единицы измерения"))
    if not parsed:
        return None
    order = {"RUB": 0, "kg": 1, "pcs": 2, None: 3}
    parsed.sort(key=lambda p: order[p.unit])
    main, others = parsed[0], parsed[1:]
    if others:
        extra = "; ".join(p.display for p in others)
        main = MinOrder(main.value, main.unit, main.raw, main.source_ref, f"{main.display} (также: {extra})", main.note)
    return main


def commerce_payload(price_fields: list[tuple[str, str]], moq_fields: list[tuple[str, str]], phrases: tuple[str, ...]) -> dict:
    """Structured prices (headline for the searched product, the rest separately) and the single minimum-order line."""
    prices = [p for raw, ref in price_fields for p in parse_prices(raw, ref, phrases)]
    matching = [p for p in prices if p.matches_product and p.price_rub is not None]
    matching.sort(key=lambda p: (p.price_per_base_unit is None, p.needs_confirmation, p.price_per_base_unit or 0))
    headline = matching[0] if matching else None
    others = [p for p in prices if p is not headline]
    order = parse_min_order(moq_fields)
    return {
        "price_headline": asdict(headline) if headline else None,
        "prices_other": [asdict(p) for p in others],
        "min_order": asdict(order) if order else None,
    }
