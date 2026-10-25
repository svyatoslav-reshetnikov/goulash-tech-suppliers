"""Scoring (0-100): product match 40, location 20, data completeness 25, reliability 15 (see `ranking/config.py`).

The supplier type is not scored: a mismatching type only blocks the recommendation badge.
"""

import re
from dataclasses import dataclass, field

from app.locations import fold, get_locations
from app.ranking.config import (
    LOCATION_POINTS,
    NEUTRAL_LOCATION_POINTS,
    PRODUCT_POINTS,
    RECOMMEND_MIN_PRODUCT_SHARE,
    RECOMMEND_MIN_SCORE,
    RECOMMEND_TOP,
    WEIGHTS,
)
from app.ranking.places import PlaceDetector, detector_for
from app.ranking.product import first_matching_phrase, is_generic, partially_matches
from app.ranking.warnings import build_warnings

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
TIER_LABELS = {
    "local": "В вашем регионе",
    "delivers": "Доставляет в ваш регион",
    "neighbor": "В соседнем регионе",
    "nationwide": "Заявляет доставку по России",
    "other": "В другом регионе",
    "unknown": "Регион неизвестен",
    "any": "Вся Россия",
}
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
    pages_studied: int | None = None  # None: unknown (the supplier was saved before page counts were recorded)

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
    warnings: list[dict] = field(default_factory=list)  # [{code, severity, text}]
    recommended: bool = False
    evidence: dict | None = None  # what confirmed the product: {kind, phrase, where, text, label}
    breakdown_max: dict[str, int] = field(default_factory=lambda: dict(WEIGHTS))


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


def product_evidence(info: SupplierInfo, ctx: RankContext) -> tuple[int, dict]:
    """Product match points by the strongest evidence: price list > assortment > description > name > generic claim."""
    phrases = (ctx.category, *ctx.synonyms)
    for field_name, kind, where in (("price_hint", "price_list", "в прайс-листе"), ("assortment", "assortment", "в ассортименте"),
                                    ("description", "description", "в описании")):
        for value in info.values(field_name):
            if phrase := first_matching_phrase(value, phrases):
                return PRODUCT_POINTS[kind], {"kind": kind, "phrase": phrase, "where": where, "text": value[:200],
                                              "label": f"найдено: {phrase} — {where}"}
    if phrase := first_matching_phrase(info.name, phrases):
        return PRODUCT_POINTS["name"], {"kind": "name", "phrase": phrase, "where": "в названии", "text": info.name,
                                        "label": f"только в названии компании ({phrase}): это не подтверждает товар"}
    texts = [*info.values("assortment"), *info.values("description"), *info.values("price_hint")]
    if any(is_generic(v) for v in texts) or any(partially_matches(v, phrases) for v in texts):
        return PRODUCT_POINTS["generic"], {"kind": "generic", "phrase": None, "where": None, "text": None,
                                           "label": "товар не назван: только общие формулировки"}
    return PRODUCT_POINTS["none"], {"kind": "none", "phrase": None, "where": None, "text": None, "label": "товар на сайте не найден"}


def _score(info: SupplierInfo, ctx: RankContext, tier: str) -> tuple[dict[str, int], dict]:
    relevance, evidence = product_evidence(info, ctx)
    location = NEUTRAL_LOCATION_POINTS if tier == "any" else LOCATION_POINTS[tier]
    completeness = sum(points for name, points in COMPLETENESS.items() if info.fields.get(name))
    contacts = {name for name in ("phone", "email") if info.fields.get(name)}
    sources = {f.source_url for values in info.fields.values() for f in values if f.source_url}
    reliability = (8 if info.inn else 0) + (4 if len(contacts) >= 2 else 0) + (3 if len(sources) >= 2 else 0)
    breakdown = {"relevance": relevance, "location": location, "completeness": completeness, "reliability": reliability}
    return breakdown, evidence


def _reasons(info: SupplierInfo, ctx: RankContext, evidence: dict, location_note: str, type_match: str) -> list[str]:
    reasons = [evidence["label"][0].upper() + evidence["label"][1:] + "."]
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


def _substitute(info: SupplierInfo) -> bool:
    text = fold(" ".join([info.name, info.text("description", "assortment", "price_hint")]))
    return SUBSTITUTE.search(text) is not None


def _questions(missing: list[str], ctx: RankContext, tier: str, regions, substitute: bool) -> list[str]:
    asks = []
    if substitute:
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
    """Ranks the run's suppliers and marks the recommended ones: the first three with a score above the threshold that also have
    a phone, a fitting type, a confirmed product (at least half of the product points) and a known region."""
    focus = frozenset(({ctx.primary_region_id} if ctx.primary_region_id else set()) | set(ctx.neighbor_region_ids))
    detector = detector_for(focus)
    regions = get_locations().regions
    ranked: list[Ranked] = []
    eligible: set[int] = set()
    for info in suppliers:
        tier, location_note = classify_location(info, ctx, detector)
        type_match = _type_match(info, ctx)
        breakdown, evidence = _score(info, ctx, tier)
        missing = [FIELD_NAMES[f] for f in WANTED if not (info.inn if f == "inn" else info.fields.get(f))]
        substitute = _substitute(info)
        confirmed = breakdown["relevance"] >= RECOMMEND_MIN_PRODUCT_SHARE * WEIGHTS["relevance"]
        warnings = build_warnings(
            site_domain=info.domain, emails=info.values("email"), pages_studied=info.pages_studied, tier=tier,
            product_confirmed=confirmed, has_inn=bool(info.inn), substitute=substitute,
        )
        # the badge needs a phone, a fitting type, a confirmed product and a known region
        if info.fields.get("phone") and type_match != "mismatch" and confirmed and tier != "unknown":
            eligible.add(info.id)
        ranked.append(
            Ranked(
                supplier_id=info.id, rank=0, score=sum(breakdown.values()), tier=tier, tier_label=TIER_LABELS[tier],
                breakdown=breakdown, reasons=_reasons(info, ctx, evidence, location_note, type_match), missing=missing,
                questions=_questions(missing, ctx, tier, regions, substitute), warnings=warnings, evidence=evidence,
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
