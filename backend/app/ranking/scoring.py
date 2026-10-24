"""Баллы (0–100): совпадение с категорией 20, локация 30, полнота данных 35, надёжность 15."""

import re
from dataclasses import dataclass, field

from app.locations import fold, get_locations
from app.ranking.places import PlaceDetector, detector_for

# вес полей в оценке полноты данных (сумма 35)
COMPLETENESS = {
    "phone": 5, "email": 4, "address": 4, "supplier_type": 3, "description": 2, "assortment": 3,
    "regions": 3, "delivery_terms": 3, "moq": 3, "price_hint": 3, "certificates": 2,
}
FIELD_NAMES = {
    "phone": "телефон", "email": "email", "address": "адрес", "supplier_type": "тип поставщика",
    "description": "описание", "assortment": "ассортимент", "regions": "регионы поставки",
    "delivery_terms": "условия доставки", "moq": "минимальный заказ", "price_hint": "цены",
    "certificates": "сертификаты", "inn": "ИНН",
}
# что стоит выяснить в первую очередь, если данных нет (в порядке важности)
WANTED = ["moq", "price_hint", "delivery_terms", "regions", "certificates", "inn", "address"]
# «другой регион» ниже «неизвестно»: у поставщика с известным чужим адресом есть и баллы за заполненный адрес,
# поэтому разрыв больше, чем кажется (2 против 10 + 4 за адрес)
LOCATION_POINTS = {"local": 30, "delivers": 26, "neighbor": 18, "other": 2, "unknown": 10}
NEUTRAL_LOCATION_POINTS = 15  # локация не выбрана: у всех одинаково
TIER_LABELS = {
    "local": "В вашем регионе",
    "delivers": "Доставляет в ваш регион",
    "neighbor": "В соседнем регионе",
    "other": "В другом регионе",
    "unknown": "Регион неизвестен",
    "any": "Вся Россия",
}
RECOMMEND_TOP = 3
RECOMMEND_MIN_SCORE = 40


@dataclass(frozen=True)
class FieldInfo:
    value: str
    source_url: str = ""


@dataclass
class SupplierInfo:
    id: int
    domain: str
    name: str
    inn: str | None
    fields: dict[str, list[FieldInfo]] = field(default_factory=dict)

    def values(self, name: str) -> list[str]:
        return [f.value for f in self.fields.get(name, [])]

    def text(self, *names: str) -> str:
        return " ".join(v for n in names for v in self.values(n))


@dataclass(frozen=True)
class RankContext:
    category: str
    synonyms: tuple[str, ...] = ()
    primary_region_id: str | None = None  # None: «Вся Россия», локация не влияет на оценку
    neighbor_region_ids: tuple[str, ...] = ()


@dataclass
class Ranked:
    supplier_id: int
    rank: int
    score: int
    tier: str
    tier_label: str
    breakdown: dict[str, int]
    reasons: list[str]
    missing: list[str]
    questions: list[str]
    recommended: bool = False


def _stems(phrases: tuple[str, ...]) -> list[str]:
    stems = []
    for phrase in phrases:
        for word in fold(phrase).split():
            if len(word) >= 4:
                stems.append(word[: max(4, len(word) - 2)])  # «моцарелла» → «моцарел»
    return stems


def _mentions(text: str, stems: list[str]) -> bool:
    folded = fold(text)
    return any(re.search(rf"\b{re.escape(s)}", folded) for s in stems)


def _region_names(ids: set[str]) -> str:
    regions = get_locations().regions
    return ", ".join(sorted(regions[i].name for i in ids if i in regions))


def classify_location(info: SupplierInfo, ctx: RankContext, detector: PlaceDetector) -> tuple[str, str]:
    """Возвращает (метка, пояснение). Метки: local, delivers, neighbor, other, unknown, any."""
    if ctx.primary_region_id is None:
        return "any", "Локация поиска не выбрана: регион на оценку не влияет."
    primary = ctx.primary_region_id
    home = detector.detect(info.text("address"))
    delivery_text = info.text("regions", "delivery_terms", "description")
    delivers = detector.detect(delivery_text)
    nationwide = detector.nationwide(delivery_text)
    regions = get_locations().regions
    primary_name = regions[primary].name

    if primary in home:
        return "local", f"Адрес в вашем регионе ({primary_name})."
    if primary in delivers or nationwide:
        where = "по всей России" if nationwide and primary not in delivers else f"в регион {primary_name}"
        return "delivers", f"Заявляет доставку {where}."
    near = home & set(ctx.neighbor_region_ids)
    if near:
        return "neighbor", f"Адрес в соседнем регионе: {_region_names(near)}."
    if home:
        return "other", f"Адрес в другом регионе: {_region_names(home)}; доставка в {primary_name} не подтверждена."
    return "unknown", "Адрес и регионы доставки не найдены: расположение неизвестно."


def _score(info: SupplierInfo, ctx: RankContext, tier: str) -> tuple[dict[str, int], str | None]:
    stems = _stems((ctx.category, *ctx.synonyms))
    if _mentions(info.text("description", "assortment"), stems):
        relevance, relevance_note = 20, "В описании или ассортименте упоминается нужный продукт."
    elif _mentions(info.name, stems):
        relevance, relevance_note = 15, "Нужный продукт упоминается в названии компании."
    else:
        relevance, relevance_note = 8, None  # найден поиском по этому продукту, но подтверждения в данных нет

    location = NEUTRAL_LOCATION_POINTS if tier == "any" else LOCATION_POINTS[tier]
    completeness = sum(points for name, points in COMPLETENESS.items() if info.fields.get(name))
    contacts = {name for name in ("phone", "email") if info.fields.get(name)}
    sources = {f.source_url for values in info.fields.values() for f in values if f.source_url}
    reliability = (8 if info.inn else 0) + (4 if len(contacts) >= 2 else 0) + (3 if len(sources) >= 2 else 0)
    return {"relevance": relevance, "location": location, "completeness": completeness, "reliability": reliability}, relevance_note


def _reasons(info: SupplierInfo, ctx: RankContext, relevance_note: str | None, location_note: str) -> list[str]:
    reasons = []
    if relevance_note:
        reasons.append(relevance_note)
    reasons.append(location_note)
    contacts = [label for key, label in (("phone", "телефон"), ("email", "email")) if info.fields.get(key)]
    if contacts:
        reasons.append(f"Есть контакты со ссылкой на источник: {' и '.join(contacts)}.")
    if info.inn:
        reasons.append(f"Указан ИНН {info.inn}, он прошёл проверку контрольной суммы.")
    kind = info.values("supplier_type")
    if kind:
        reasons.append(f"Тип: {kind[0]}.")
    for name, label in (("moq", "Минимальный заказ"), ("delivery_terms", "Доставка")):
        if info.fields.get(name):
            reasons.append(f"{label}: {info.values(name)[0]}.")
    return reasons


def _questions(missing: list[str], ctx: RankContext, tier: str, regions) -> list[str]:
    asks = []
    if "минимальный заказ" in missing or "цены" in missing:
        parts = [p for p in ("минимальный заказ" if "минимальный заказ" in missing else "", "цены" if "цены" in missing else "") if p]
        asks.append(f"Уточните {' и '.join(parts)} на «{ctx.category}».")
    if tier in ("unknown", "other", "neighbor") or "условия доставки" in missing or "регионы поставки" in missing:
        target = f"регион «{regions[ctx.primary_region_id].name}»" if ctx.primary_region_id else "вашу точку"
        asks.append(f"Узнайте, доставляют ли в {target} и на каких условиях.")
    extra = [p for p in ("сертификаты" if "сертификаты" in missing else "", "реквизиты (ИНН)" if "ИНН" in missing else "") if p]
    if extra:
        asks.append(f"Попросите {' и '.join(extra)}.")
    return asks


def rank_suppliers(suppliers: list[SupplierInfo], ctx: RankContext) -> list[Ranked]:
    """Ранжирует поставщиков запуска и помечает рекомендованных (топ-3 с оценкой не ниже порога)."""
    focus = frozenset(({ctx.primary_region_id} if ctx.primary_region_id else set()) | set(ctx.neighbor_region_ids))
    detector = detector_for(focus)
    regions = get_locations().regions
    ranked: list[Ranked] = []
    for info in suppliers:
        tier, location_note = classify_location(info, ctx, detector)
        breakdown, relevance_note = _score(info, ctx, tier)
        missing = [FIELD_NAMES[f] for f in WANTED if not (info.inn if f == "inn" else info.fields.get(f))]
        ranked.append(
            Ranked(
                supplier_id=info.id, rank=0, score=sum(breakdown.values()), tier=tier, tier_label=TIER_LABELS[tier],
                breakdown=breakdown, reasons=_reasons(info, ctx, relevance_note, location_note), missing=missing,
                questions=_questions(missing, ctx, tier, regions),
            )
        )
    names = {s.id: s.name for s in suppliers}
    ranked.sort(key=lambda r: (-r.score, -r.breakdown["completeness"], names[r.supplier_id]))
    for position, item in enumerate(ranked, start=1):
        item.rank = position
        item.recommended = position <= RECOMMEND_TOP and item.score >= RECOMMEND_MIN_SCORE
    return ranked
