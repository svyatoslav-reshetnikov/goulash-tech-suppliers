"""Собирает справочник локаций: регионы России с соседями и города. Запускается вручную, результат лежит в репозитории.

    python -m scripts.build_locations

Источники:
  * Wikidata (CC0): 89 субъектов РФ и связи «имеет общую границу» (P47) между ними;
  * hflabs/city (CC BY-SA 4.0, https://github.com/hflabs/city): города России с регионом, населением и координатами.

Результат: app/locations/regions.json и app/locations/cities.json. Загруженные данные кэшируются в
scripts/locations_cache/ (не в репозитории), поэтому повторный запуск не ходит в сеть.
"""

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from app.net import build_ssl_context

OUT_DIR = Path(__file__).resolve().parents[1] / "app" / "locations"
CACHE_DIR = Path(__file__).resolve().parent / "locations_cache"
USER_AGENT = "goulash-suppliers-test/0.1 (test assignment; location reference build)"
WDQS = "https://query.wikidata.org/sparql"
HFLABS_CSV = "https://raw.githubusercontent.com/hflabs/city/master/city.csv"

REGIONS_QUERY = """
SELECT ?r ?rLabel ?iso WHERE {
  ?r wdt:P31 wd:Q43263 .
  FILTER NOT EXISTS { ?r wdt:P576 ?end }
  OPTIONAL { ?r wdt:P300 ?iso }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "ru". }
}"""
BORDERS_QUERY = """
SELECT DISTINCT ?a ?b WHERE {
  ?a wdt:P31 wd:Q43263 . FILTER NOT EXISTS { ?a wdt:P576 ?e1 }
  ?a wdt:P47 ?b .
  ?b wdt:P31 wd:Q43263 . FILTER NOT EXISTS { ?b wdt:P576 ?e2 }
}"""

# В Wikidata у этих субъектов нет кода ISO 3166-2 или он нестандартный: задаём идентификаторы сами.
MANUAL_IDS = {
    "Республика Крым": "RU-CR",
    "Севастополь": "RU-SEV",
    "Донецкая Народная Республика": "RU-DON",
    "Луганская Народная Республика": "RU-LUG",
    "Херсонская область": "RU-KHE",
    "Запорожская область": "RU-ZAP",
}

# Wikidata называет республики коротко («Татарстан»); задаём официальные названия.
OFFICIAL_NAMES = {
    "RU-AD": "Республика Адыгея", "RU-BA": "Республика Башкортостан", "RU-BU": "Республика Бурятия",
    "RU-CE": "Чеченская Республика", "RU-CU": "Чувашская Республика", "RU-DA": "Республика Дагестан",
    "RU-IN": "Республика Ингушетия", "RU-KB": "Кабардино-Балкарская Республика",
    "RU-KC": "Карачаево-Черкесская Республика", "RU-KK": "Республика Хакасия", "RU-KL": "Республика Калмыкия",
    "RU-KR": "Республика Карелия", "RU-ME": "Республика Марий Эл", "RU-MO": "Республика Мордовия",
    "RU-SA": "Республика Саха (Якутия)", "RU-SE": "Республика Северная Осетия — Алания",
    "RU-TA": "Республика Татарстан", "RU-TY": "Республика Тыва", "RU-UD": "Удмуртская Республика",
    "RU-KEM": "Кемеровская область — Кузбасс",
}

# Привычные сокращения и разговорные названия для поиска (помимо официального и короткого).
EXTRA_ALIASES = {
    "RU-MOW": ["Москва", "МСК"], "RU-SPE": ["Питер", "СПб", "Петербург"], "RU-MOS": ["Подмосковье", "МО"],
    "RU-KHM": ["ХМАО", "Югра"], "RU-YAN": ["ЯНАО"], "RU-NEN": ["НАО"], "RU-SA": ["Якутия"],
    "RU-UD": ["Удмуртия"], "RU-CU": ["Чувашия"], "RU-CE": ["Чечня"], "RU-KEM": ["Кузбасс"],
    "RU-SE": ["Северная Осетия", "Алания"], "RU-KB": ["Кабардино-Балкария"], "RU-KC": ["Карачаево-Черкесия"],
    "RU-CR": ["Крым"], "RU-LEN": ["Ленобласть"], "RU-KGD": ["Калининград"], "RU-TA": ["Татарстан"],
    "RU-BA": ["Башкирия"], "RU-KK": ["Хакасия"], "RU-KR": ["Карелия"], "RU-DON": ["ДНР"], "RU-LUG": ["ЛНР"],
}

# Написания регионов в CSV, которые не удаётся сопоставить автоматически.
CSV_ALIASES = {"Чувашия Чувашская Республика": "RU-CU"}

# Соседство без общей сухопутной границы, важное для доставки: к общим границам из Wikidata добавляются вручную.
EXTRA_NEIGHBORS = [
    ("RU-CR", "RU-KDA"),  # Крым и Краснодарский край: Крымский мост и паромная переправа
]

GENERIC_WORDS = {
    "область", "край", "республика", "респ", "обл", "автономный", "автономная", "аобл", "ао", "округ",
    "город", "г", "федерального", "значения",
}


def fetch(url: str, cache_name: str, params: dict | None = None, attempts: int = 4) -> str:
    CACHE_DIR.mkdir(exist_ok=True)
    cached = CACHE_DIR / cache_name
    if cached.is_file():
        return cached.read_text(encoding="utf-8")
    full = url + ("?" + urllib.parse.urlencode(params) if params else "")
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json, text/csv, */*"})
    context = build_ssl_context()
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=120, context=context) as response:
                text = response.read().decode("utf-8")
            cached.write_text(text, encoding="utf-8")
            return text
        except Exception as exc:
            print(f"  попытка {attempt + 1}/{attempts}: {exc}", file=sys.stderr)
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"не удалось загрузить {url}")


def sparql(query: str, cache_name: str) -> list[dict]:
    return json.loads(fetch(WDQS, cache_name, {"query": query, "format": "json"}))["results"]["bindings"]


def region_tokens(name: str) -> frozenset[str]:
    """Название региона без родовых слов («область», «Респ» и т. п.) для сопоставления разных написаний."""
    words = re.findall(r"[а-яa-z0-9]+", name.lower().replace("ё", "е"))
    return frozenset(w for w in words if w not in GENERIC_WORDS)


def region_type(name: str) -> str:
    lowered = name.lower()
    for marker, label in [
        ("город федерального значения", "город федерального значения"),
        ("автономный округ", "автономный округ"),
        ("автономная область", "автономная область"),
        ("республика", "республика"),
        ("край", "край"),
        ("область", "область"),
    ]:
        if marker in lowered:
            return label
    return "город федерального значения" if name in {"Москва", "Санкт-Петербург", "Севастополь"} else "регион"


def short_name(name: str) -> str:
    """«Свердловская область» → «Свердловская», «Республика Татарстан» → «Татарстан»: для поиска по короткому названию."""
    words = [w for w in re.split(r"\s+", name) if w.lower() not in GENERIC_WORDS]
    return " ".join(words).strip(" —-") or name


def build() -> tuple[list[dict], list[dict]]:
    rows = sparql(REGIONS_QUERY, "regions.json")
    regions: dict[str, dict] = {}  # по QID
    for row in rows:
        qid = row["r"]["value"].rsplit("/", 1)[1]
        name = row["rLabel"]["value"].strip()
        iso = row.get("iso", {}).get("value")
        region_id = MANUAL_IDS.get(name) or iso
        if not region_id or region_id.startswith("UA"):
            raise SystemExit(f"нет идентификатора для региона {name}: добавьте в MANUAL_IDS")
        official = OFFICIAL_NAMES.get(region_id, name)
        aliases = [a for a in [name, *EXTRA_ALIASES.get(region_id, [])] if a != official]
        regions[qid] = {"id": region_id, "name": official, "short": short_name(official), "type": region_type(official),
                        "aliases": sorted(set(aliases)), "neighbors": set(), "wikidata": qid}
    if len(regions) != 89:
        print(f"ВНИМАНИЕ: регионов {len(regions)}, ожидалось 89", file=sys.stderr)

    for row in sparql(BORDERS_QUERY, "borders.json"):
        a, b = (row[k]["value"].rsplit("/", 1)[1] for k in ("a", "b"))
        if a in regions and b in regions and a != b:
            regions[a]["neighbors"].add(regions[b]["id"])
            regions[b]["neighbors"].add(regions[a]["id"])  # граница взаимна
    by_id = {r["id"]: r for r in regions.values()}
    for a, b in EXTRA_NEIGHBORS:
        by_id[a]["neighbors"].add(b)
        by_id[b]["neighbors"].add(a)

    names = {r["id"]: r["name"] for r in regions.values()}
    result_regions = []
    for region in sorted(regions.values(), key=lambda r: r["name"]):
        result_regions.append({**region, "neighbors": sorted(region["neighbors"], key=lambda i: names[i])})

    # --- города ---
    by_tokens: dict[frozenset[str], str] = {}
    for region in regions.values():
        by_tokens[region_tokens(region["name"])] = region["id"]
    reader = csv.DictReader(fetch(HFLABS_CSV, "hflabs_city.csv").splitlines())
    cities, unmapped = [], defaultdict(int)
    for row in reader:
        name = row["city"].strip()
        if not name:
            continue
        csv_name = f"{row['region_type']} {row['region']}"
        region_id = CSV_ALIASES.get(csv_name) or by_tokens.get(region_tokens(csv_name))
        if region_id is None:
            unmapped[f"{row['region_type']} {row['region']}"] += 1
            continue
        if name == names[region_id] or (region_id in {"RU-MOW", "RU-SPE", "RU-SEV"} and name in {"Москва", "Санкт-Петербург", "Севастополь"}):
            continue  # город федерального значения совпадает со своим регионом
        cities.append({
            "name": name,
            "region": region_id,
            "population": int(row["population"] or 0),
            "lat": round(float(row["geo_lat"]), 4) if row["geo_lat"] else None,
            "lon": round(float(row["geo_lon"]), 4) if row["geo_lon"] else None,
            "capital": row["capital_marker"] in {"1", "2", "3"},
        })
    if unmapped:
        print("Не сопоставлены регионы из CSV:", dict(unmapped), file=sys.stderr)
        raise SystemExit("добавьте сопоставление или исправьте GENERIC_WORDS")
    # В исходных данных встречается один город дважды (например, «Ялта»: посёлок и город): оставляем с большим населением
    unique: dict[tuple[str, str], dict] = {}
    for city in cities:
        known = unique.get((city["region"], city["name"]))
        if known is None or city["population"] > known["population"]:
            unique[(city["region"], city["name"])] = city
    cities = sorted(unique.values(), key=lambda c: (names[c["region"]], c["name"]))
    return result_regions, cities


def main() -> None:
    regions, cities = build()
    OUT_DIR.mkdir(exist_ok=True)
    meta = {
        "sources": [
            "Wikidata (CC0): субъекты РФ и общие границы, https://www.wikidata.org",
            "hflabs/city (CC BY-SA 4.0): города России, https://github.com/hflabs/city",
        ],
        "note": "Файл создан скриптом scripts/build_locations.py; вручную добавлено соседство Крым — Краснодарский край.",
    }
    (OUT_DIR / "regions.json").write_text(
        json.dumps({**meta, "items": regions}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (OUT_DIR / "cities.json").write_text(
        json.dumps({**meta, "items": cities}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"регионов: {len(regions)}, городов: {len(cities)}")


if __name__ == "__main__":
    main()
