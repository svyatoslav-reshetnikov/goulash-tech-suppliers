"""План поиска кандидатов, который выполняет код, а не модель.

Модель сама выбирала запросы: на три региона она делала три-четыре запроса, повторяла один и тот же и смотрела только
первую страницу выдачи. Код перебирает регионы, формулировки и страницы выдачи по плану; модель подключается после этого
и может искать дополнительно.
"""

from dataclasses import dataclass

from app.agent.models import RunRequest

# (шаблон запроса, страницы выдачи). Хороший сайт нередко стоит на второй странице: на первой его вытесняют каталоги
TEMPLATES: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("{product} оптом производитель {place}", (0, 1)),
    ("{product} поставщик для ресторанов {place}", (0, 1)),
    ("купить {product} оптом {place}", (0,)),
    ("{product} оптом с доставкой {place}", (0,)),
)
SYNONYM_TEMPLATE = "{product} оптом {place}"
MAX_SYNONYMS = 2


@dataclass(frozen=True)
class SearchStep:
    query: str
    region: int | None  # код региона Яндекса для выдачи
    page: int = 0


def places(request: RunRequest) -> list[tuple[str, int | None]]:
    """Места поиска по приоритету: город (если выбран), основной регион, соседние. Для каждого свой код региона."""
    result: list[tuple[str, int | None]] = []
    if request.city:
        result.append((request.city, request.search_region))
    for region in request.regions:
        code = request.search_region if region.role == "primary" else (region.lr or request.search_region)
        result.append((region.name, code))
    return result or [("", request.search_region)]


def build_plan(request: RunRequest) -> list[SearchStep]:
    """Запросы без повторов: сначала основной регион (все формулировки и страницы), затем соседние."""
    product = request.category.lower()
    steps: list[SearchStep] = []
    seen: set[tuple[str, int | None, int]] = set()

    def add(template: str, name: str, place: str, region: int | None, page: int) -> None:
        query = " ".join(template.format(product=name, place=place).split())
        if (query.lower(), region, page) not in seen:
            seen.add((query.lower(), region, page))
            steps.append(SearchStep(query, region, page))

    for index, (place, region) in enumerate(places(request)):
        for template, pages in TEMPLATES:
            for page in pages:
                add(template, product, place, region, page)
        if index == 0:  # другие названия продукта: достаточно основного места, выдача по соседним похожа
            for synonym in request.synonyms[:MAX_SYNONYMS]:
                add(SYNONYM_TEMPLATE, synonym.lower(), place, region, 0)
    return steps
