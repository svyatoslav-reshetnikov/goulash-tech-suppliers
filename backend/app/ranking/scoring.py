"""Баллы (0–100): совпадение с категорией 20, локация 30, полнота данных 25, надёжность 15, тип поставщика 10."""

import re
from dataclasses import dataclass, field

from app.agent.validation import mentions_product, product_stems
from app.locations import fold, get_locations
from app.ranking.places import PlaceDetector, detector_for

# вес полей в оценке полноты данных (сумма 25): больше всего весят телефон и условия закупки, а не тексты о компании,
# иначе первым оказывается сайт с длинным описанием, а не поставщик, у которого понятно, как и почём купить
COMPLETENESS = {
    "phone": 5, "email": 2, "address": 2, "supplier_type": 1, "description": 1, "assortment": 1,
    "regions": 2, "delivery_terms": 3, "moq": 3, "price_hint": 3, "certificates": 2,
}
FIELD_NAMES = {
    "phone": "телефон", "email": "email", "address": "адрес", "supplier_type": "тип поставщика",
    "description": "описание", "assortment": "ассортимент", "regions": "регионы поставки",
    "delivery_terms": "условия доставки", "moq": "минимальный заказ", "price_hint": "цены",
    "certificates": "сертификаты", "inn": "ИНН",
}
# что стоит выяснить в первую очередь, если данных нет (в порядке важности)
WANTED = ["phone", "moq", "price_hint", "delivery_terms", "regions", "certificates", "inn", "address"]
# «доставка по всей России» без названия региона — обещание общее, оно ниже соседнего региона;
# «другой регион» ниже «неизвестно»: у поставщика с известным чужим адресом есть и баллы за заполненный адрес
LOCATION_POINTS = {"local": 30, "delivers": 26, "neighbor": 18, "nationwide": 14, "other": 2, "unknown": 10}
NEUTRAL_LOCATION_POINTS = 15  # локация не выбрана: у всех одинаково
TIER_LABELS = {
    "local": "В вашем регионе",
    "delivers": "Доставляет в ваш регион",
    "neighbor": "В соседнем регионе",
    "nationwide": "Заявляет доставку по России",
    "other": "В другом регионе",
    "unknown": "Регион неизвестен",
    "any": "Вся Россия",
}
# тип поставщика: совпал с искомым (или не важен) 10, не указан на сайте 4, другой 0
TYPE_POINTS = {"match": 10, "unknown": 4, "mismatch": 0}
RECOMMEND_TOP = 3
RECOMMEND_MIN_SCORE = 40
# признаки аналога вместо натурального продукта: для пиццы и роллов важно, сыр это или продукт с заменителем
SUBSTITUTE = re.compile(r"заменител\w*\s+молочн\w*\s+жир|\bсзмж\b|молокосодержащ|растительн\w*\s+жир|сырн\w*\s+продукт")


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
    supplier_type: str | None = None  # None: тип не важен


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
    warnings: list[str] = field(default_factory=list)
    recommended: bool = False


def _region_names(ids: set[str]) -> str:
    regions = get_locations().regions
    return ", ".join(sorted(regions[i].name for i in ids if i in regions))


def classify_location(info: SupplierInfo, ctx: RankContext, detector: PlaceDetector) -> tuple[str, str]:
    """Возвращает (метка, пояснение). Метки: local, delivers, neighbor, nationwide, other, unknown, any."""
    if ctx.primary_region_id is None:
        return "any", "Локация поиска не выбрана: регион на оценку не влияет."
    primary = ctx.primary_region_id
    home = detector.detect(info.text("address"))
    home_from = "Адрес"
    if not home and info.fields.get("address"):
        # адрес без региона («пгт. Рыбная Слобода, ул. Октябрьская»): регион берём из описания компании
        home, home_from = detector.detect(info.text("description")), "По описанию компании"
    delivery_text = info.text("regions", "delivery_terms")
    delivers = detector.detect(delivery_text)
    nationwide = detector.nationwide(info.text("regions", "delivery_terms", "description"))
    regions = get_locations().regions
    primary_name = regions[primary].name

    if primary in home:
        return "local", f"{home_from} в вашем регионе ({primary_name})."
    if primary in delivers:
        return "delivers", f"Заявляет доставку в регион {primary_name}."
    near = home & set(ctx.neighbor_region_ids)
    if near:
        return "neighbor", f"{home_from} в соседнем регионе: {_region_names(near)}."
    if nationwide:
        where = f" Адрес: {_region_names(home)}." if home else ""
        return "nationwide", f"Заявляет доставку по всей России, но {primary_name} отдельно не называет.{where}"
    if home:
        return "other", f"{home_from} в другом регионе: {_region_names(home)}; доставка в {primary_name} не подтверждена."
    if delivers:
        return "other", f"Работает в регионах: {_region_names(delivers)}; {primary_name} среди них не назван."
    return "unknown", "Адрес и регионы доставки не найдены: расположение неизвестно."


def _type_match(info: SupplierInfo, ctx: RankContext) -> str:
    if not ctx.supplier_type:
        return "match"
    kinds = info.values("supplier_type")
    if not kinds:
        return "unknown"
    return "match" if ctx.supplier_type in kinds else "mismatch"


def _score(info: SupplierInfo, ctx: RankContext, tier: str, type_match: str) -> tuple[dict[str, int], str | None]:
    stems = product_stems((ctx.category, *ctx.synonyms))
    if mentions_product(info.text("description", "assortment"), stems):
        relevance, relevance_note = 20, "В описании или ассортименте упоминается нужный продукт."
    elif mentions_product(info.name, stems):
        relevance, relevance_note = 15, "Нужный продукт упоминается в названии компании."
    else:
        relevance, relevance_note = 8, None  # найден поиском по этому продукту, но подтверждения в данных нет

    location = NEUTRAL_LOCATION_POINTS if tier == "any" else LOCATION_POINTS[tier]
    completeness = sum(points for name, points in COMPLETENESS.items() if info.fields.get(name))
    contacts = {name for name in ("phone", "email") if info.fields.get(name)}
    sources = {f.source_url for values in info.fields.values() for f in values if f.source_url}
    reliability = (8 if info.inn else 0) + (4 if len(contacts) >= 2 else 0) + (3 if len(sources) >= 2 else 0)
    breakdown = {"relevance": relevance, "location": location, "completeness": completeness, "reliability": reliability,
                 "supplier_type": TYPE_POINTS[type_match]}
    return breakdown, relevance_note


def _reasons(info: SupplierInfo, ctx: RankContext, relevance_note: str | None, location_note: str, type_match: str) -> list[str]:
    reasons = []
    if relevance_note:
        reasons.append(relevance_note)
    reasons.append(location_note)
    contacts = [label for key, label in (("phone", "телефон"), ("email", "email")) if info.fields.get(key)]
    if contacts:
        reasons.append(f"Есть контакты со ссылкой на источник: {' и '.join(contacts)}.")
    if not info.fields.get("phone"):
        reasons.append("Телефона на сайте нет: позвонить некуда, поэтому в рекомендации не попадает.")
    if info.inn:
        reasons.append(f"Указан ИНН {info.inn}, он прошёл проверку контрольной суммы.")
    kind = info.values("supplier_type")
    if type_match == "mismatch":
        reasons.append(f"Тип: {kind[0]}, а вы искали: {ctx.supplier_type}. В рекомендации не попадает.")
    elif type_match == "unknown":
        reasons.append(f"Тип поставщика на сайте не указан, а вы искали: {ctx.supplier_type}.")
    elif kind:
        reasons.append(f"Тип: {kind[0]}.")
    for name, label in (("moq", "Минимальный заказ"), ("delivery_terms", "Доставка")):
        if info.fields.get(name):
            reasons.append(f"{label}: {info.values(name)[0]}.")
    return reasons


def _warnings(info: SupplierInfo) -> list[str]:
    text = fold(" ".join([info.name, info.text("description", "assortment", "price_hint")]))
    if SUBSTITUTE.search(text):
        return ["В ассортименте есть продукт с заменителем молочного жира (молокосодержащий или «сырный» продукт). "
                "Уточните, предлагают ли натуральный продукт, а не аналог."]
    return []


def _questions(missing: list[str], ctx: RankContext, tier: str, regions, warnings: list[str]) -> list[str]:
    asks = []
    if warnings:
        asks.append(f"Уточните, что «{ctx.category}» натуральный продукт, а не аналог с заменителем молочного жира (СЗМЖ).")
    if "минимальный заказ" in missing or "цены" in missing:
        parts = [p for p in ("минимальный заказ" if "минимальный заказ" in missing else "", "цены" if "цены" in missing else "") if p]
        asks.append(f"Уточните {' и '.join(parts)} на «{ctx.category}».")
    if tier == "local":  # поставщик рядом: спрашивать, возят ли в наш регион, незачем
        if "условия доставки" in missing:
            asks.append("Уточните условия и стоимость доставки до вашей точки.")
    elif tier in ("unknown", "other", "neighbor", "nationwide") or "условия доставки" in missing or "регионы поставки" in missing:
        target = f"регион «{regions[ctx.primary_region_id].name}»" if ctx.primary_region_id else "вашу точку"
        asks.append(f"Узнайте, доставляют ли в {target} и на каких условиях.")
    extra = [p for p in ("сертификаты" if "сертификаты" in missing else "", "реквизиты (ИНН)" if "ИНН" in missing else "") if p]
    if extra:
        asks.append(f"Попросите {' и '.join(extra)}.")
    return asks


def rank_suppliers(suppliers: list[SupplierInfo], ctx: RankContext) -> list[Ranked]:
    """Ранжирует поставщиков запуска и помечает рекомендованных: первые три с оценкой не ниже порога,
    телефоном и подходящим типом (без телефона связаться не получится, чужой тип закупщику не нужен)."""
    focus = frozenset(({ctx.primary_region_id} if ctx.primary_region_id else set()) | set(ctx.neighbor_region_ids))
    detector = detector_for(focus)
    regions = get_locations().regions
    ranked: list[Ranked] = []
    eligible: set[int] = set()
    for info in suppliers:
        tier, location_note = classify_location(info, ctx, detector)
        type_match = _type_match(info, ctx)
        breakdown, relevance_note = _score(info, ctx, tier, type_match)
        missing = [FIELD_NAMES[f] for f in WANTED if not (info.inn if f == "inn" else info.fields.get(f))]
        warnings = _warnings(info)
        if info.fields.get("phone") and type_match != "mismatch":
            eligible.add(info.id)
        ranked.append(
            Ranked(
                supplier_id=info.id, rank=0, score=sum(breakdown.values()), tier=tier, tier_label=TIER_LABELS[tier],
                breakdown=breakdown, reasons=_reasons(info, ctx, relevance_note, location_note, type_match), missing=missing,
                questions=_questions(missing, ctx, tier, regions, warnings), warnings=warnings,
            )
        )
    names = {s.id: s.name for s in suppliers}
    # при равной оценке выше тот, кто ближе: местному поставщику проще и дешевле возить
    ranked.sort(key=lambda r: (-r.score, -r.breakdown["location"], -r.breakdown["completeness"], names[r.supplier_id]))
    recommended = 0
    for position, item in enumerate(ranked, start=1):
        item.rank = position
        item.recommended = recommended < RECOMMEND_TOP and item.score >= RECOMMEND_MIN_SCORE and item.supplier_id in eligible
        recommended += item.recommended
    return ranked
