"""Справочник локаций: целостность данных, известные факты о границах, поиск и расширение на соседей."""

import json
from collections import deque

import pytest

from app.locations import DATA_DIR, fold, get_locations

L = get_locations()
BY_NAME = {r.name: r for r in L.regions.values()}


def names(region_ids) -> set[str]:
    return {L.regions[i].name for i in region_ids}


# --- целостность данных ---


def test_regions_cover_all_89_subjects_with_unique_ids_and_names():
    assert len(L.regions) == 89
    assert len({r.name for r in L.regions.values()}) == 89


def test_neighbors_are_symmetric_valid_and_without_self():
    for region in L.regions.values():
        assert region.id not in region.neighbors
        for other in region.neighbors:
            assert other in L.regions, f"{region.name}: неизвестный сосед {other}"
            assert region.id in L.regions[other].neighbors, f"граница {region.name} → {L.regions[other].name} не взаимна"


def test_all_regions_are_connected_except_kaliningrad():
    start = BY_NAME["Свердловская область"].id
    seen, queue = {start}, deque([start])
    while queue:
        for n in L.regions[queue.popleft()].neighbors:
            if n not in seen:
                seen.add(n)
                queue.append(n)
    assert names(set(L.regions) - seen) == {"Калининградская область"}  # анклав: общей границы с регионами нет


def test_cities_have_known_regions_unique_keys_and_sane_values():
    assert len(L.cities) > 1000
    assert len({c.key for c in L.cities}) == len(L.cities)
    for city in L.cities:
        assert city.region_id in L.regions and city.population >= 0
    federal = {"Москва", "Санкт-Петербург", "Севастополь"}
    assert not [c for c in L.cities if c.name in federal and L.regions[c.region_id].name == c.name]  # не дублируют регион
    assert max(L.cities, key=lambda c: c.population).name == "Новосибирск"


def test_data_files_name_their_sources():
    for filename in ("regions.json", "cities.json"):
        sources = " ".join(json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))["sources"])
        assert "Wikidata" in sources and "hflabs" in sources


# --- известные факты о границах (независимая проверка по общеизвестным данным) ---


@pytest.mark.parametrize(
    "region, expected",
    [
        ("Свердловская область", {"Курганская область", "Пермский край", "Республика Башкортостан", "Республика Коми",
                                  "Тюменская область", "Ханты-Мансийский автономный округ — Югра", "Челябинская область"}),
        ("Республика Татарстан", {"Кировская область", "Оренбургская область", "Республика Башкортостан",
                                  "Республика Марий Эл", "Самарская область", "Удмуртская Республика",
                                  "Ульяновская область", "Чувашская Республика"}),
        ("Республика Адыгея", {"Краснодарский край"}),
        ("Санкт-Петербург", {"Ленинградская область"}),
        ("Приморский край", {"Хабаровский край"}),
        ("Калининградская область", set()),
    ],
)
def test_known_neighbors_match_reality(region, expected):
    assert names(BY_NAME[region].neighbors) == expected


def test_moscow_and_crimea_special_cases():
    assert {"Московская область"} <= names(BY_NAME["Москва"].neighbors)
    assert {"Республика Крым"} == names(BY_NAME["Севастополь"].neighbors)
    assert "Краснодарский край" in names(BY_NAME["Республика Крым"].neighbors)  # добавлено вручную: мост и паром


# --- поиск ---


def first(query: str):
    results = L.search(query)
    assert results, f"ничего не найдено по запросу {query!r}"
    return results[0]


def test_search_finds_city_by_prefix_and_ignores_case_and_yo():
    top = first("екатер")
    assert (top.kind, top.label, top.region_name) == ("city", "Екатеринбург", "Свердловская область")
    assert first("орел").label == "Орёл"
    assert first("ОРЁЛ").label == "Орёл"


def test_search_region_by_short_name_alias_and_abbreviation():
    assert first("свердл").key == BY_NAME["Свердловская область"].id
    assert first("питер").key == BY_NAME["Санкт-Петербург"].id
    assert first("хмао").region_name.startswith("Ханты-Мансийский")
    assert first("Удмуртия").region_name == "Удмуртская Республика"
    assert first("кузбасс").region_name == "Кемеровская область — Кузбасс"


def test_exact_and_prefix_matches_come_before_substring_matches_and_big_cities_first():
    results = L.search("киров")
    assert results[0].label == "Кировская область" or results[0].label == "Киров"  # полное или начало названия
    assert all(m.kind in {"city", "region"} for m in results)
    same_name = [m for m in L.search("Заречный", 30) if m.label == "Заречный"]
    assert len(same_name) > 1 and len({m.region_name for m in same_name}) == len(same_name)  # одноимённые различимы по региону
    populations = [m.population for m in same_name]
    assert populations == sorted(populations, reverse=True)


def test_search_limits_and_empty_query():
    assert L.search("") == [] and L.search("   !!! ") == []
    assert len(L.search("а", limit=5)) == 5
    assert L.search("нет такого места xyz") == []


def test_fold():
    assert fold("  Орёл, г. ") == "орел г"


# --- выбор и расширение ---


def test_resolve_city_and_region_and_unknown():
    city = L.resolve("city", "RU-SVE|Екатеринбург")
    assert city and city.region_name == "Свердловская область" and city.population > 1_000_000
    assert L.resolve("region", "RU-SVE").label == "Свердловская область"
    assert L.resolve("city", "RU-SVE|Нет такого") is None and L.resolve("region", "XX") is None and L.resolve("x", "y") is None


def test_expand_with_and_without_neighbors():
    only = L.expand("RU-SVE", False)
    assert [(r.name, role) for r, role in only] == [("Свердловская область", "primary")]
    both = L.expand("RU-SVE", True)
    assert both[0][1] == "primary" and all(role == "neighbor" for _, role in both[1:])
    assert len(both) == 8 and [r.name for r, _ in both[1:]] == sorted(r.name for r, _ in both[1:])
    assert len({r.id for r, _ in both}) == len(both)
