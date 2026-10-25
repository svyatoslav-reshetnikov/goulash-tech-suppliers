"""Scoring weights, the recommendation gate, price and minimum-order parsing, warnings.

Real data: tests/fixtures/search_rice_moscow.json is the stored result of the "Рис для суши", Москва search.
"""

import json
from pathlib import Path

from app.locations import get_locations
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.ranking.commerce import commerce_payload, parse_min_order, parse_prices
from app.ranking.config import PRODUCT_POINTS, RECOMMEND_MIN_PRODUCT_SHARE, WEIGHTS
from app.ranking.warnings import email_mismatches, registrable_domain

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "search_rice_moscow.json").read_text(encoding="utf-8"))
PHRASES = ("Рис для суши", "Рис")
MOSCOW = next(r.id for r in get_locations().regions.values() if r.name == "Москва")
CTX = RankContext(category="Рис для суши", synonyms=("Рис",), primary_region_id=MOSCOW,
                  neighbor_region_ids=tuple(get_locations().regions[MOSCOW].neighbors))


def fixture_infos() -> list[SupplierInfo]:
    return [
        SupplierInfo(
            id=s["id"], domain=s["domain"], name=s["name"], inn=s["inn"],
            fields={k: [FieldInfo(v["value"], v["source_url"]) for v in vs] for k, vs in s["fields"].items()},
            pages_studied=len(s["research"]["pages"]) if s["research"] else None,
        )
        for s in FIXTURE["suppliers"]
    ]


def ranked_by_name() -> dict[str, object]:
    names = {i.id: i.name for i in fixture_infos()}
    return {names[r.supplier_id]: r for r in rank_suppliers(fixture_infos(), CTX)}


def make(sid=1, name="Поставщик", domain="s1.test", **fields) -> SupplierInfo:
    return SupplierInfo(id=sid, domain=domain, name=name, inn=fields.pop("inn", None),
                        fields={k: [FieldInfo(v, f"https://{domain}/{k}")] for k, v in fields.items()}, pages_studied=5)


# --- weights and the recommendation gate ---


def test_weights_sum_to_100_and_match_the_spec():
    assert WEIGHTS == {"relevance": 40, "location": 20, "completeness": 25, "reliability": 15}
    assert sum(WEIGHTS.values()) == 100 and PRODUCT_POINTS["price_list"] == WEIGHTS["relevance"]
    for ranked in ranked_by_name().values():
        assert ranked.breakdown_max == WEIGHTS and all(ranked.breakdown[k] <= WEIGHTS[k] for k in WEIGHTS)


def test_suomifish_ranks_above_gfc_and_gfc_is_not_recommended():
    ranked = ranked_by_name()
    suomi, gfc = ranked["SuomiFish"], ranked["GFC-Russia"]
    assert suomi.score > gfc.score and suomi.rank < gfc.rank
    assert suomi.recommended and not gfc.recommended
    assert suomi.evidence["kind"] == "price_list" and suomi.evidence["label"] == "найдено: Рис для суши — в прайс-листе"
    assert gfc.breakdown["relevance"] < RECOMMEND_MIN_PRODUCT_SHARE * WEIGHTS["relevance"]  # "полный ассортимент" proves nothing
    assert "product_unconfirmed" in [w["code"] for w in gfc.warnings]


def test_recommendation_needs_confirmed_product_and_known_region():
    base = dict(phone="+7 (495) 123-45-67", email="a@s1.test", address="г. Москва, ул. Тверская, 1", supplier_type="оптовик")
    confirmed = rank_suppliers([make(**base, assortment="Рис, нори")], CTX)[0]
    generic = rank_suppliers([make(**base, assortment="Полный ассортимент продуктов питания")], CTX)[0]
    unknown_region = rank_suppliers([make(phone=base["phone"], assortment="Рис для суши")], CTX)[0]
    assert confirmed.recommended and not generic.recommended and not unknown_region.recommended
    assert unknown_region.tier == "unknown" and "region_unknown" in [w["code"] for w in unknown_region.warnings]


def test_rice_vinegar_is_not_rice_and_generic_phrase_scores_low():
    vinegar = rank_suppliers([make(assortment="Рисовый уксус, соевый соус")], CTX)[0]
    generic = rank_suppliers([make(description="Полный ассортимент продуктов питания для ресторанов")], CTX)[0]
    assert vinegar.evidence["kind"] != "assortment"
    assert generic.breakdown["relevance"] == PRODUCT_POINTS["generic"] <= 0.25 * WEIGHTS["relevance"]


# --- warnings ---


def test_email_domain_mismatch_and_free_mail():
    assert registrable_domain("www.shop.example.ru") == "example.ru" and registrable_domain("a.b.msk.ru") == "b.msk.ru"
    assert email_mismatches(["zakaz@osttorg.ru"], "vosttorg.ru") == ["osttorg.ru"]
    assert email_mismatches(["me@gmail.com", "x@mail.ru", "sales@mail.vosttorg.ru"], "vosttorg.ru") == []
    vostorg = ranked_by_name()["Восторг"]
    texts = {w["code"]: w["text"] for w in vostorg.warnings}
    assert texts["email_domain_mismatch"] == "Домен почты (osttorg.ru) не совпадает с сайтом (vosttorg.ru) — проверьте поставщика"
    assert "low_evidence" in texts and texts["no_inn"] == "ИНН не найден"


def test_one_studied_page_is_low_evidence_and_many_pages_are_not():
    for name, ranked in ranked_by_name().items():
        pages = len(next(s for s in FIXTURE["suppliers"] if s["name"] == name)["research"]["pages"])
        assert ("low_evidence" in [w["code"] for w in ranked.warnings]) == (pages <= 1), name


# --- prices ---


def test_suomifish_rice_string_resolves_to_2100_per_25kg_flagged_for_confirmation():
    raw = next(s for s in FIXTURE["suppliers"] if s["name"] == "SuomiFish")["fields"]["price_hint"][0]["value"]
    (price,) = parse_prices(raw, "https://suomifish.ru/price", PHRASES)
    assert (price.price_rub, price.pack_value, price.base_unit) == (2100.0, 25.0, "kg")
    assert round(price.price_per_base_unit) == 84
    assert price.needs_confirmation and price.note.startswith("Уточните цену")
    assert price.matches_product and "2 100 ₽" in price.display and "≈84 ₽/кг" in price.display and price.raw == raw


def test_unambiguous_prices_are_not_flagged_and_arithmetic_is_exact():
    (rice,) = parse_prices("Рис для суши, 25 кг — 2100 руб.", "", PHRASES)
    assert not rice.needs_confirmation and rice.price_per_base_unit == 84.0
    (per_kg,) = parse_prices("Рис 1 кг 130 р.", "", PHRASES)
    assert per_kg.price_per_base_unit == 130.0 and "130 ₽/кг" in per_kg.display
    (grams,) = parse_prices("Васаби 500 г 450 руб", "", PHRASES)
    assert grams.price_per_base_unit == 900.0 and grams.base_unit == "kg"


def test_price_without_item_and_unrelated_price_do_not_become_the_headline():
    (gfc,) = parse_prices("925 ₽/шт", "", PHRASES)
    assert gfc.item_name is None and not gfc.matches_product and gfc.note == "Не указано, на какой товар цена"
    payload = commerce_payload([("Рис для суши, 25 кг — 2100 руб; водоросли для онигири 1 313 руб.", "u"), ("925 ₽/шт", "u")], [], PHRASES)
    assert payload["price_headline"]["item_name"].startswith("Рис") and len(payload["prices_other"]) == 2
    assert commerce_payload([("925 ₽/шт", "u")], [], PHRASES)["price_headline"] is None


def test_unparseable_price_shows_the_raw_string_with_a_note_not_a_bare_number():
    (price,) = parse_prices("от 100", "", PHRASES)
    assert price.price_rub is None and price.needs_confirmation and price.display == "от 100 · Уточните цену"


def test_prices_never_render_without_a_unit():
    for raw in ("Рис 25кг/м 2 100 руб.", "925 ₽/шт", "Нори 50 листов 300 руб", "1 000 руб"):
        for price in parse_prices(raw, "", PHRASES):
            assert "₽" in price.display, price.display


# --- minimum order ---


def test_min_order_is_one_line_with_units():
    both = parse_min_order([("от 11 000 ₽", "a"), ("1 шт.", "a")])
    assert both.value == 11000 and both.unit == "RUB" and both.display == "от 11 000 ₽ (также: от 1 шт.)"
    assert parse_min_order([("2000 рублей", "a")]).display == "от 2 000 ₽"
    assert parse_min_order([("от 20 кг", "a")]).display == "от 20 кг"
    assert parse_min_order([("1,5 т", "a")]).value == 1500
    unitless = parse_min_order([("50", "a")])
    assert unitless.value is None and unitless.display == "50 · Уточните единицы"
    assert parse_min_order([]) is None


def test_fixture_commerce_for_every_supplier_is_well_formed():
    for s in FIXTURE["suppliers"]:
        fields = s["fields"]
        payload = commerce_payload([(v["value"], v["source_url"]) for v in fields.get("price_hint", [])],
                                   [(v["value"], v["source_url"]) for v in fields.get("moq", [])], PHRASES)
        if "moq" in fields:
            assert payload["min_order"] and payload["min_order"]["unit"] in ("RUB", "kg", "pcs")
        else:
            assert payload["min_order"] is None
