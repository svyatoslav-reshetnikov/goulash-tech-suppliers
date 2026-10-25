"""Справочник локаций: регионы России с соседями и города.

Данные лежат в regions.json и cities.json (собираются скриптом scripts/build_locations.py) и целиком
загружаются в память: это около 1200 записей. Используется формой поиска (выбор локации) и агентом
(в каких регионах искать поставщиков).
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    short: str
    type: str
    aliases: tuple[str, ...]
    neighbors: tuple[str, ...]


@dataclass(frozen=True)
class City:
    name: str
    region_id: str
    population: int
    lat: float | None
    lon: float | None
    capital: bool

    @property
    def key(self) -> str:
        return f"{self.region_id}|{self.name}"


@dataclass(frozen=True)
class Match:
    kind: str  # city | region
    key: str  # для региона его id, для города «id региона|название»
    label: str
    region_id: str
    region_name: str
    population: int | None = None


def fold(text: str) -> str:
    """Для сравнения: регистр, ё→е, всё кроме букв и цифр заменено пробелом."""
    return " ".join(re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е")))


class Locations:
    def __init__(self, regions: list[Region], cities: list[City]):
        self.regions = {r.id: r for r in regions}
        self.cities = cities
        self._cities_by_key = {c.key: c for c in cities}
        self._region_names = [(r, [fold(n) for n in (r.name, r.short, *r.aliases) if n]) for r in regions]
        self._city_names = [(c, fold(c.name)) for c in cities]

    # --- поиск для автодополнения ---

    @staticmethod
    def _score(query: str, names: list[str]) -> int | None:
        """0 — полное совпадение, 1 — начало названия, 2 — начало слова, 3 — вхождение; None — не подходит."""
        best: int | None = None
        for name in names:
            if name == query:
                score = 0
            elif name.startswith(query):
                score = 1
            elif any(word.startswith(query) for word in name.split()):
                score = 2
            elif query in name:
                score = 3
            else:
                continue
            best = score if best is None else min(best, score)
        return best

    def search(self, query: str, limit: int = 10) -> list[Match]:
        q = fold(query)
        if not q:
            return []
        found: list[tuple[tuple, Match]] = []
        for region, names in self._region_names:
            score = self._score(q, names)
            if score is not None:
                found.append(((score, 0, 0, region.name), Match("region", region.id, region.name, region.id, region.name)))
        for city, name in self._city_names:
            score = self._score(q, [name])
            if score is not None:
                region = self.regions[city.region_id]
                found.append(
                    ((score, 1, -city.population, city.name),
                     Match("city", city.key, city.name, region.id, region.name, city.population))
                )
        found.sort(key=lambda item: item[0])
        return [match for _, match in found[:limit]]

    # --- выбор и расширение на соседей ---

    def resolve(self, kind: str, key: str) -> Match | None:
        if kind == "region" and key in self.regions:
            region = self.regions[key]
            return Match("region", region.id, region.name, region.id, region.name)
        if kind == "city" and key in self._cities_by_key:
            city = self._cities_by_key[key]
            region = self.regions[city.region_id]
            return Match("city", city.key, city.name, region.id, region.name, city.population)
        return None

    def expand(self, region_id: str, include_neighbors: bool) -> list[tuple[Region, str]]:
        """Регионы для поиска: выбранный первым (primary), затем соседи по алфавиту (neighbor)."""
        primary = self.regions[region_id]
        result = [(primary, "primary")]
        if include_neighbors:
            neighbors = sorted((self.regions[n] for n in primary.neighbors), key=lambda r: r.name)
            result += [(n, "neighbor") for n in neighbors]
        return result


@lru_cache
def get_locations() -> Locations:
    regions_data = json.loads((DATA_DIR / "regions.json").read_text(encoding="utf-8"))["items"]
    cities_data = json.loads((DATA_DIR / "cities.json").read_text(encoding="utf-8"))["items"]
    regions = [
        Region(r["id"], r["name"], r["short"], r["type"], tuple(r["aliases"]), tuple(r["neighbors"]))
        for r in regions_data
    ]
    cities = [
        City(c["name"], c["region"], c["population"], c["lat"], c["lon"], c["capital"]) for c in cities_data
    ]
    return Locations(regions, cities)


@lru_cache
def _yandex_codes() -> dict:
    return json.loads((DATA_DIR / "yandex_regions.json").read_text(encoding="utf-8"))


def yandex_region(kind: str | None, key: str | None) -> int:
    """Код региона Яндекс Поиска для выбранного места: у города свой код, если он есть, иначе код региона, иначе России.

    Поиск с кодом региона ранжирует выдачу для этого места: местные поставщики оказываются выше.
    """
    codes = _yandex_codes()
    if kind == "city" and key:
        if key in codes["cities"]:
            return codes["cities"][key]
        key = key.split("|", 1)[0]
    if key and key in codes["regions"]:
        return codes["regions"][key]["lr"]
    return codes["russia"]
