"""Ранжирование: определение региона по тексту, метки локации, баллы, объяснение и рекомендация."""

import pytest

from app.locations import get_locations
from app.ranking import FieldInfo, RankContext, SupplierInfo, rank_suppliers
from app.ranking.places import detector_for

L = get_locations()
BY_NAME = {r.name: r.id for r in L.regions.values()}
TA = BY_NAME["Республика Татарстан"]
CTX = RankContext(category="Моцарелла", synonyms=("Сыр моцарелла",), primary_region_id=TA,
                  neighbor_region_ids=tuple(L.regions[TA].neighbors))
detector = detector_for(frozenset({TA, *L.regions[TA].neighbors}))


def supplier(sid=1, name="ООО Тест", inn=None, **fields: str | list[str]) -> SupplierInfo:
    return SupplierInfo(
        id=sid, domain=f"s{sid}.test", name=name, inn=inn,
        fields={k: [FieldInfo(v, f"https://s{sid}.test/{k}/{i}") for i, v in enumerate([val] if isinstance(val, str) else val)]
                for k, val in fields.items()},  # у каждого поля своя страница-источник
    )


def only(info: SupplierInfo, ctx: RankContext = CTX):
    return rank_suppliers([info], ctx)[0]


# --- регион по тексту ---


@pytest.mark.parametrize(
    "text, expected",
    [
        ("г. Казань, ул. Бухарская 4В", {"Республика Татарстан"}),
        ("находимся в Казани", {"Республика Татарстан"}),
        ("Доставка по Татарстану и Свердловской области", {"Республика Татарстан", "Свердловская область"}),
        ("г. Москва, ул. Тверская", {"Москва"}),  # «Тверская» — улица, не Тверь
        ("Казанская ул. 3, Самара", {"Самарская область"}),  # «Казанская» — улица, не Казань
        ("Московский проспект, Казань", {"Республика Татарстан"}),  # «Московский» — не Москва и не область
        ("Московская обл., г. Мытищи", {"Московская область"}),
        ("в Екатеринбурге", {"Свердловская область"}),
        ("Республика Саха (Якутия), Якутск", {"Республика Саха (Якутия)"}),
        ("склад в Санкт-Петербурге", {"Санкт-Петербург"}),
        ("ул. Ленина 5", set()),
    ],
)
def test_detects_regions_with_declensions_and_ignores_street_names(text, expected):
    assert {L.regions[i].name for i in detector.detect(text)} == expected


def test_detects_nationwide_delivery():
    for text in ["Доставка по всей России", "работаем по России", "вся Россия", "по территории РФ"]:
        assert detector.nationwide(text), text
    assert not detector.nationwide("Доставка по Казани")


# --- метки локации ---


@pytest.mark.parametrize(
    "fields, tier",
    [
        ({"address": "г. Казань, ул. Бухарская 4В"}, "local"),
        ({"address": "Республика Татарстан, г. Набережные Челны"}, "local"),
        ({"regions": "Доставка по Татарстану"}, "delivers"),
        ({"delivery_terms": "Доставляем по всей России"}, "nationwide"),  # общее обещание: регион не назван
        ({"address": "г. Москва", "regions": "по России"}, "nationwide"),  # обещание доставки важнее чужого адреса
        ({"address": "г. Москва", "regions": "по Татарстану и России"}, "delivers"),
        ({"address": "г. Ижевск, ул. Пушкинская 1"}, "neighbor"),
        ({"address": "г. Москва, ул. Тверская 1"}, "other"),
        ({"regions": "Москва, Санкт-Петербург, Краснодар"}, "other"),  # перечислены регионы, нашего среди них нет
        # адрес без региона: регион берётся из описания компании (пгт Рыбная Слобода — Татарстан)
        ({"address": "пгт. Рыбная Слобода, ул. Октябрьская, д.36/2",
          "description": "Современное предприятие молочной отрасли Республики Татарстан."}, "local"),
        ({"description": "Производим сыр"}, "unknown"),
        ({}, "unknown"),
    ],
)
def test_location_tiers(fields, tier):
    assert only(supplier(**fields)).tier == tier


def test_without_selected_location_region_does_not_matter():
    no_location = RankContext(category="Моцарелла")
    local = only(supplier(address="г. Казань"), no_location)
    far = only(supplier(address="г. Москва"), no_location)
    assert local.tier == far.tier == "any" and local.breakdown["location"] == far.breakdown["location"] == 10
    assert "не выбрана" in local.reasons[0] or any("не выбрана" in r for r in local.reasons)


def test_tier_labels_and_explanations_name_the_regions():
    other = only(supplier(address="г. Москва"))
    assert other.tier_label == "В другом регионе" and any("Москва" in r and "Республика Татарстан" in r for r in other.reasons)
    near = only(supplier(address="г. Ижевск"))
    assert any("Удмуртская Республика" in r for r in near.reasons)


# --- баллы ---


def test_location_orders_otherwise_identical_suppliers():
    base = {"description": "Моцарелла оптом", "phone": "+7 (843) 555-01-23"}
    infos = [
        supplier(1, **base, address="г. Москва"),
        supplier(2, **base),
        supplier(3, **base, address="г. Казань"),
        supplier(4, **base, address="г. Ижевск"),
        supplier(5, **base, regions="по Татарстану"),
    ]
    order = [(r.supplier_id, r.tier) for r in rank_suppliers(infos, CTX)]
    assert [t for _, t in order] == ["local", "delivers", "neighbor", "unknown", "other"]


def test_more_confirmed_data_gives_a_higher_score_and_perfect_supplier_gets_100():
    poor = only(supplier(phone="+7 (843) 555-01-23"))
    richer = only(supplier(phone="+7 (843) 555-01-23", email="a@b.ru", moq="от 20 кг"))
    assert richer.score > poor.score and richer.breakdown["completeness"] > poor.breakdown["completeness"]
    perfect = only(supplier(
        name="Моцарелла Сыр", inn="7707083893", phone="+7 (843) 555-01-23", email="a@b.ru", address="г. Казань",
        supplier_type="производитель", description="Производим моцареллу", assortment="Моцарелла", regions="Татарстан",
        delivery_terms="бесплатно", moq="20 кг", price_hint="Моцарелла 500 руб/кг", certificates="ХАССП"))
    assert perfect.score == 100 and sum(perfect.breakdown.values()) == 100


def test_relevance_and_reliability_components():
    mentions = only(supplier(description="Поставляем сыр моцарелла оптом"))
    in_name = only(supplier(name="Моцарелла-Маркет"))
    nothing = only(supplier(description="Разные продукты"))
    assert (mentions.breakdown["relevance"], in_name.breakdown["relevance"], nothing.breakdown["relevance"]) == (20, 10, 0)
    assert only(supplier(description="Моцарелла")).breakdown["reliability"] == 0
    two_pages = supplier(phone="+7 (843) 555-01-23", email="a@b.ru", inn="7707083893")
    assert only(two_pages).breakdown["reliability"] == 8 + 4 + 3


# --- объяснение ---


def test_explanation_lists_what_is_missing_and_what_to_ask():
    result = only(supplier(phone="+7 (843) 555-01-23", address="г. Москва"))
    assert result.missing[:3] == ["минимальный заказ", "цены", "условия доставки"]
    questions = " ".join(result.questions)
    assert "минимальный заказ и цены" in questions and "Республика Татарстан" in questions and "ИНН" in questions
    full = only(supplier(inn="7707083893", phone="+7 (843) 555-01-23", moq="20 кг", price_hint="500", delivery_terms="бесплатно", regions="Татарстан",
                         certificates="ХАССП", address="г. Казань"))
    assert full.missing == [] and full.questions == []
    assert any("7707083893" in r for r in full.reasons)


def test_reasons_use_only_saved_facts():
    result = only(supplier(phone="+7 (843) 555-01-23", supplier_type="производитель", moq="от 20 кг"))
    text = " ".join(result.reasons)
    assert "телефон" in text and "email" not in text and "производитель" in text and "от 20 кг" in text and "ИНН" not in text


# --- рекомендация ---


def test_top_three_are_recommended_only_above_threshold():
    rich = dict(phone="+7 (843) 555-01-23", email="a@b.ru", address="г. Казань", description="Моцарелла")
    infos = [supplier(i, name=f"Поставщик {i}", **rich) for i in range(1, 5)] + [supplier(9, name="Бедный")]
    ranked = rank_suppliers(infos, CTX)
    assert [r.rank for r in ranked] == [1, 2, 3, 4, 5]
    assert [r.recommended for r in ranked] == [True, True, True, False, False]
    assert ranked[-1].supplier_id == 9 and ranked[-1].score < 40
    weak = rank_suppliers([supplier(1, name="Один")], CTX)
    assert weak[0].score < 40 and not weak[0].recommended  # данных слишком мало: рекомендовать нечего


def test_ties_are_broken_by_location_then_completeness_then_name():
    a = supplier(1, name="Б-сыр", phone="+7 (843) 555-01-23", email="a@b.ru")
    b = supplier(2, name="А-сыр", phone="+7 (843) 555-01-23", email="a@b.ru")
    assert [r.supplier_id for r in rank_suppliers([a, b], CTX)] == [2, 1]
    assert rank_suppliers([], CTX) == []


def test_real_run_3_suppliers_are_ranked_sensibly():
    """Данные запуска №3 («Моцарелла», Казань): единственный местный поставщик должен быть первым."""
    inlut = supplier(1, name='ООО "Инлут"', phone="+7 (905) 038-20-96", email="info@farfatorre.ru", address="г. Казань, ул. Бухарская 4В",
                     description="Производим и поставляем сыры Моцарелла по итальянским рецептам", supplier_type="производитель")
    gurman = supplier(2, name="ТД «Гурман»", phone="+7 (495) 133-00-60", email="office@gurman-llc.ru",
                      description="ТД «Гурман» предлагает сыр моцарелла оптом по цене производителя")
    gfc = supplier(3, name="GFC", email="msk@gfc-russia.ru", description="Компания GFC предлагает сыр моцарелла оптом")
    ranked = rank_suppliers([gfc, gurman, inlut], CTX)
    assert [r.supplier_id for r in ranked] == [1, 2, 3]
    assert [r.tier for r in ranked] == ["local", "unknown", "unknown"]
    assert ranked[0].recommended and ranked[0].score > ranked[1].score > ranked[2].score


# --- рекомендация только тем, кому можно позвонить и кто подходит по типу ---


def test_supplier_without_phone_is_not_recommended_and_the_next_one_is():
    rich = dict(email="a@b.ru", address="г. Казань, ул. Бухарская 4В", description="Моцарелла", moq="20 кг", delivery_terms="бесплатно")
    no_phone = supplier(1, name="Без телефона", inn="7707083893", price_hint="моцарелла 500 руб/кг", certificates="ХАССП", **rich)
    callable_ = [supplier(i, name=f"Поставщик {i}", phone="+7 (843) 555-01-23", **rich) for i in (2, 3, 4)]
    ranked = rank_suppliers([no_phone, *callable_], CTX)
    assert ranked[0].supplier_id == 1 and not ranked[0].recommended  # лучшая оценка, но звонить некуда
    assert [r.recommended for r in ranked[1:]] == [True, True, True]
    assert any("Телефона на сайте нет" in r for r in ranked[0].reasons) and "телефон" in ranked[0].missing


def test_requested_supplier_type_counts_in_score_and_recommendation():
    ctx = RankContext(category="Моцарелла", primary_region_id=TA, neighbor_region_ids=CTX.neighbor_region_ids, supplier_type="производитель")
    base = dict(phone="+7 (843) 555-01-23", email="a@b.ru", address="г. Казань, ул. Бухарская 4В", description="Моцарелла")
    maker = only(supplier(1, supplier_type="производитель", **base), ctx)
    dealer = only(supplier(2, supplier_type="дистрибьютор", **base), ctx)
    unknown = only(supplier(3, **base), ctx)
    assert "supplier_type" not in maker.breakdown  # the type is a gate for the badge, not points
    assert maker.recommended and unknown.recommended and not dealer.recommended
    assert any("вы искали: производитель" in r for r in dealer.reasons)
    assert only(supplier(4, supplier_type="дистрибьютор", **base)).recommended  # тип не важен


def test_local_producer_outranks_moscow_wholesaler_who_only_promises_delivery_across_russia():
    """Данные запуска №5 («Моцарелла», Казань) после проверки полей."""
    gfc = supplier(1, name="GFC", inn="7726410031", phone=["+7 (800) 550-41-83", "+7 (903) 410-60-20"], email="msk@gfc-russia.ru",
                   address="123022, г. Москва, Звенигородское шоссе, д. 9/27с1", supplier_type="оптовик",
                   description="Компания GFC предлагает сыр моцарелла оптом", assortment="Молочная продукция, сыры",
                   regions=["16 регионов России", "Москва"], moq="от 11 000 ₽",
                   delivery_terms="Доставка осуществляется по всей России и регионам транспортом компании.")
    inlut = supplier(2, name='ООО "Инлут"', phone="+7 (905) 038-20-96", email="info@farfatorre.ru", address="г. Казань, ул. Бухарская 4В",
                     description="Производим и поставляем сыры Моцарелла по итальянским рецептам", supplier_type="производитель",
                     assortment="Моцарелла, молокосодержащий продукт с заменителем молочного жира")
    ranked = rank_suppliers([gfc, inlut], CTX)
    assert [r.supplier_id for r in ranked] == [2, 1]  # the local producer is first
    assert ranked[1].tier == "nationwide" and ranked[1].tier_label == "Заявляет доставку по России"
    # у местного производителя не спрашиваем, возят ли в Татарстан, но предупреждаем об аналоге
    assert "substitute_product" in [w["code"] for w in ranked[0].warnings]
    assert not any("доставляют ли" in q for q in ranked[0].questions) and "СЗМЖ" in ranked[0].questions[0]
    assert not [w for w in ranked[1].warnings if w["code"] == "substitute_product"]


def test_village_address_does_not_match_a_similar_city_name():
    # «Рыбно-Слободский район» — не город Слободской Кировской области
    assert detector.detect("Рыбно-Слободский район, пгт. Рыбная Слобода, ул. Октябрьская, д.36/2") == set()
    assert {L.regions[i].name for i in detector.detect("г. Слободской, ул. Ленина 1")} == {"Кировская область"}
