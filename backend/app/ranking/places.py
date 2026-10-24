"""Определение регионов по тексту (адрес, регионы доставки) с учётом русских падежей.

Для региона и города строится шаблон по основам слов: «Татарстан» находит «в Татарстане», «Казань» находит «в Казани».
Области и края распознаются только вместе со словом «область», «край» (иначе «Московский проспект» в Казани
сошёл бы за Московскую область); республики, округа, Москва и Петербург однозначны и без уточнений.
"""

import re
from functools import lru_cache

from app.locations import Locations, fold, get_locations

_ADJECTIVE_ENDINGS = ("ская", "ской", "ский", "ское", "ого", "ая", "ой", "ий", "ый", "ое")
_NEEDS_QUALIFIER = {"область", "край"}
_QUALIFIER = r"(?:обл|област\w*|кра[йяю]\w*|край)"
NATIONWIDE = re.compile(
    r"(?:по|всей|вся|всю)\s+(?:всей\s+)?(?:росси\w*|рф\b|территории\s+рф|стране)|вся\s+росси\w*|все\s+регионы|в\s+любой\s+регион"
)
BIG_CITY = 100_000


_NOUN_SUFFIX = r"(?:а|я|ь|и|ы|у|ю|е|ом|ем|ой|ей|ью|ою|ею)?"


def _word_pattern(word: str) -> str:
    """Шаблон одного слова с падежами. Прилагательные («Свердловская») гибки, существительные («Казань») строже:
    иначе улица «Казанская» или «Тверская» принималась бы за город."""
    for ending in _ADJECTIVE_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 4:
            return rf"\b{re.escape(word[: -len(ending)])}\w{{0,5}}"
    stem = word[:-1] if word[-1] in "аяьй" and len(word) > 4 else word
    return rf"\b{re.escape(stem)}{_NOUN_SUFFIX}\b"


def _pattern(phrase: str) -> str | None:
    words = [w for w in fold(phrase).split() if len(w) > 1]
    return r"\s+".join(_word_pattern(w) for w in words) if words else None


class PlaceDetector:
    """Находит в тексте упоминания регионов и городов. Города: крупные и все города интересующих регионов."""

    def __init__(self, locations: Locations, focus_regions: frozenset[str] = frozenset()):
        self._regions: list[tuple[str, re.Pattern]] = []
        for region in locations.regions.values():
            patterns = []
            for name in (region.short, *region.aliases):
                pattern = _pattern(name)
                if pattern:
                    patterns.append(pattern)
            if not patterns:
                continue
            joined = "(?:" + "|".join(patterns) + ")"
            if region.type in _NEEDS_QUALIFIER:
                joined += rf"\s*[.,]?\s*{_QUALIFIER}"
            self._regions.append((region.id, re.compile(joined)))
        self._cities: list[tuple[str, re.Pattern]] = []
        seen: set[tuple[str, str]] = set()
        for city in locations.cities:
            if city.population < BIG_CITY and city.region_id not in focus_regions:
                continue
            pattern = _pattern(city.name)
            if pattern and (city.region_id, pattern) not in seen:
                seen.add((city.region_id, pattern))
                self._cities.append((city.region_id, re.compile(pattern)))

    def detect(self, text: str) -> set[str]:
        """Идентификаторы регионов, упомянутых в тексте (прямо или через город)."""
        folded = fold(text)
        found = {rid for rid, regex in self._regions if regex.search(folded)}
        found |= {rid for rid, regex in self._cities if regex.search(folded)}
        return found

    @staticmethod
    def nationwide(text: str) -> bool:
        return bool(NATIONWIDE.search(fold(text)))


@lru_cache(maxsize=64)
def detector_for(focus_regions: frozenset[str]) -> PlaceDetector:
    return PlaceDetector(get_locations(), focus_regions)
