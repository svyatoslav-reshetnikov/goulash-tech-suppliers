"""Сценарии проверки модели.

1. tools: агентный цикл с инструментами-заглушками (вызов, аргументы, цитата).
2. extraction: поля поставщика со страницы, с дословными цитатами и без выдумок.
3. ingredients: ингредиенты из описаний блюд (задача вывода категорий каталога).
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from .clients import ModelClient, Reply, ToolCall

FIXTURES = Path(__file__).parent / "fixtures"
PAGE_URL = "https://syrmaster-kazan.test/"
MAX_STEPS = 8


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower().replace("ё", "е")).strip()


def digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def extract_json(text: str | None):
    """Достаёт JSON-объект из ответа, даже если он обёрнут в ```json ... ```."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def quote_in_page(quote: str | None, page: str) -> bool:
    return bool(quote) and norm(quote) in norm(page)


@dataclass
class CaseResult:
    case: str
    score: float = 0.0
    passed: bool = False
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: str | None = None

    def add(self, reply: Reply) -> None:
        self.seconds += reply.seconds
        self.prompt_tokens += reply.prompt_tokens
        self.completion_tokens += reply.completion_tokens


def load_page() -> str:
    return (FIXTURES / "supplier_page.txt").read_text(encoding="utf-8")


def load_dishes() -> list[dict]:
    return json.loads((FIXTURES / "dishes.json").read_text(encoding="utf-8"))


# --- 1. Агентный цикл с инструментами -------------------------------------------------


class SearchWebArgs(BaseModel):
    query: str
    region: str | None = None


class FetchPageArgs(BaseModel):
    url: str


class SaveSupplierArgs(BaseModel):
    name: str
    website: str
    phone: str
    phone_quote: str


class FinishArgs(BaseModel):
    summary: str


ARG_MODELS: dict[str, type[BaseModel]] = {
    "search_web": SearchWebArgs,
    "fetch_page": FetchPageArgs,
    "save_supplier": SaveSupplierArgs,
    "finish": FinishArgs,
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TOOLS = [
    _tool(
        "search_web",
        "Поиск в интернете. Возвращает список результатов (заголовок, ссылка, описание).",
        {
            "query": {"type": "string", "description": "Поисковый запрос"},
            "region": {"type": "string", "description": "Город или регион"},
        },
        ["query"],
    ),
    _tool(
        "fetch_page",
        "Загружает страницу по ссылке и возвращает её текст.",
        {"url": {"type": "string"}},
        ["url"],
    ),
    _tool(
        "save_supplier",
        "Сохраняет поставщика. Телефон нужно подтвердить дословной цитатой со страницы.",
        {
            "name": {"type": "string"},
            "website": {"type": "string"},
            "phone": {"type": "string"},
            "phone_quote": {"type": "string", "description": "Дословный фрагмент страницы с телефоном"},
        },
        ["name", "website", "phone", "phone_quote"],
    ),
    _tool("finish", "Завершает работу.", {"summary": {"type": "string"}}, ["summary"]),
]

SEARCH_RESULTS = [
    {
        "title": "Моцарелла оптом Казань: объявления на маркетплейсе",
        "url": "https://marketplace-example.test/mozzarella",
        "snippet": "Агрегатор объявлений, цены разных продавцов.",
    },
    {
        "title": "СырМастер Казань: производство и оптовые поставки сыров",
        "url": PAGE_URL,
        "snippet": "Моцарелла для пиццы от производителя. Казань и Татарстан.",
    },
    {
        "title": "Рецепты пиццы с моцареллой",
        "url": "https://recipes-example.test/pizza",
        "snippet": "Как приготовить пиццу дома.",
    },
]

TOOLS_SYSTEM = (
    "Ты помощник по поиску поставщиков продуктов для ресторанов. Работай через инструменты. "
    "Не придумывай данные: сохраняй только то, что видел на странице, и подтверждай телефон "
    "дословной цитатой. Агрегаторы и сайты с рецептами не являются поставщиками. "
    "Когда поставщик сохранён, вызови finish."
)
TOOLS_TASK = (
    "Найди поставщика моцареллы для пиццерии в Казани: найди через поиск, открой страницу "
    "подходящего результата и сохрани поставщика."
)


def json_mode_system() -> str:
    """Запасной режим: модель отвечает JSON-действием, если нативные вызовы не работают."""
    lines = [TOOLS_SYSTEM, "", "Доступные инструменты:"]
    for tool in TOOLS:
        fn = tool["function"]
        lines.append(f"- {fn['name']}: {fn['description']} Параметры: {json.dumps(fn['parameters']['properties'], ensure_ascii=False)}")
    lines += [
        "",
        'Каждый ответ — ровно один JSON вида {"tool": "<имя>", "args": {...}} и больше ничего.',
    ]
    return "\n".join(lines)


def _json_calls(reply: Reply) -> list[ToolCall]:
    data = extract_json(reply.text)
    if not isinstance(data, dict) or "tool" not in data:
        return []
    return [ToolCall(id="json", name=str(data["tool"]), arguments=json.dumps(data.get("args", {}), ensure_ascii=False))]


def _run_tool(name: str, args: BaseModel, page: str, state: dict) -> str:
    if isinstance(args, SearchWebArgs):
        state["searched"] = True
        return json.dumps(SEARCH_RESULTS, ensure_ascii=False)
    if isinstance(args, FetchPageArgs):
        if args.url.rstrip("/") == PAGE_URL.rstrip("/"):
            state["fetched"] = True
            return page
        return "Ошибка 404: страница не найдена"
    if isinstance(args, SaveSupplierArgs):
        problems = []
        if "syrmaster-kazan.test" not in norm(args.website):
            problems.append("сохранён не тот поставщик")
        if "8435550123" not in digits(args.phone):
            problems.append("телефон не совпадает со страницей")
        if not quote_in_page(args.phone_quote, page):
            problems.append("цитата не найдена на странице дословно")
        if problems:
            state["save_problems"] = problems
            return "Ошибка: " + "; ".join(problems) + ". Исправь и повтори."
        state["saved"] = True
        return "Поставщик сохранён."
    state["finished"] = True
    return "Готово."


def tool_calling(client: ModelClient, mode: str = "native") -> CaseResult:
    result = CaseResult("tools")
    page = load_page()
    system = TOOLS_SYSTEM if mode == "native" else json_mode_system()
    messages: list[dict] = [{"role": "system", "content": system}, {"role": "user", "content": TOOLS_TASK}]
    state: dict = {"saved": False, "searched": False, "fetched": False, "finished": False}
    seen: Counter = Counter()
    invalid = 0
    steps = 0

    for steps in range(1, MAX_STEPS + 1):
        try:
            reply = client.chat(messages, tools=TOOLS if mode == "native" else None)
        except Exception as exc:  # ошибка API: например, модель не принимает tools
            result.error = f"{type(exc).__name__}: {exc}"
            break
        result.add(reply)
        calls = reply.tool_calls if mode == "native" else _json_calls(reply)
        if not calls:
            result.notes.append("ответил текстом без вызова инструмента")
            break

        if mode == "native":
            messages.append(
                {
                    "role": "assistant",
                    "content": reply.text,
                    "tool_calls": [
                        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                        for c in calls
                    ],
                }
            )
        else:
            messages.append({"role": "assistant", "content": reply.text})

        for call in calls:
            seen[(call.name, call.arguments)] += 1
            model_cls = ARG_MODELS.get(call.name)
            if model_cls is None:
                invalid += 1
                output = f"Неизвестный инструмент: {call.name}"
            else:
                try:
                    args = model_cls.model_validate_json(call.arguments)
                    output = _run_tool(call.name, args, page, state)
                except ValidationError as exc:
                    invalid += 1
                    output = f"Ошибка аргументов: {exc.error_count()} проблем(ы). Проверь параметры."
            if mode == "native":
                messages.append({"role": "tool", "tool_call_id": call.id, "content": output})
            else:
                messages.append({"role": "user", "content": f"Результат {call.name}: {output}"})
        if state["finished"]:
            break

    if invalid:
        result.notes.append(f"некорректных вызовов: {invalid}")
    if any(n >= 3 for n in seen.values()):
        result.notes.append("зациклился на одном и том же вызове")
    if state.get("save_problems") and not state["saved"]:
        result.notes.append("не сумел сохранить поставщика: " + "; ".join(state["save_problems"]))

    result.score = (
        0.15 * state["searched"]
        + 0.25 * state["fetched"]
        + 0.50 * state["saved"]
        + 0.10 * (state["finished"] and steps <= MAX_STEPS)
    )
    result.passed = bool(state["saved"]) and result.error is None
    return result


# --- 2. Извлечение полей поставщика с цитатами ----------------------------------------

FIELD_CHECKS = {
    "name": lambda v: "сырмастер" in norm(v),
    "website": lambda v: "syrmaster-kazan.test" in norm(v),
    "phone": lambda v: "8435550123" in digits(v),
    "email": lambda v: "sales@syrmaster-kazan.test" in norm(v),
    "inn": lambda v: "1655012345" in digits(v),
    "moq": lambda v: "20" in v and "кг" in norm(v),
    "price_hint": lambda v: "520" in digits(v),
    "delivery_terms": lambda v: "30000" in digits(v),
    "regions": lambda v: "татарстан" in norm(v),
    "certificates": None,  # в тексте нет: правильный ответ null
}

EXTRACTION_SYSTEM = (
    "Ты извлекаешь данные о поставщике из текста страницы. Отвечай только JSON без пояснений. "
    "Для каждого поля верни объект {\"value\": ..., \"quote\": ...}, где quote — дословный фрагмент "
    "страницы, подтверждающий значение. Если информации на странице нет, верни null. Ничего не выдумывай."
)


def extraction_prompt(page: str) -> str:
    fields = ", ".join(FIELD_CHECKS)
    return (
        f"Поля: {fields}.\n"
        "Смысл полей: moq — минимальный заказ, price_hint — примерная цена, certificates — сертификаты "
        "и документы, delivery_terms — условия доставки, regions — регионы работы.\n\n"
        f"Текст страницы:\n{page}"
    )


def score_extraction(data, page: str) -> tuple[float, list[str]]:
    if not isinstance(data, dict):
        return 0.0, ["не вернул валидный JSON"]
    notes: list[str] = []
    total = 0.0
    for name, check in FIELD_CHECKS.items():
        entry = data.get(name)
        # «нет данных» — это null, а также {"value": null, ...} или пустое значение
        raw_value = entry.get("value") if isinstance(entry, dict) else entry
        empty = raw_value in (None, "", [], {})
        if check is None:
            if empty:
                total += 1
            else:
                notes.append(f"выдумал {name}")
            continue
        if empty:
            notes.append(f"не нашёл {name}")
            continue
        value = entry.get("value") if isinstance(entry, dict) else entry
        quote = entry.get("quote") if isinstance(entry, dict) else None
        value_text = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        if check(value_text):
            total += 0.5
        else:
            notes.append(f"неверное значение {name}")
        if quote_in_page(quote, page):
            total += 0.5
        else:
            notes.append(f"цитата {name} не найдена на странице")
    return total / len(FIELD_CHECKS), notes


def extraction(client: ModelClient) -> CaseResult:
    result = CaseResult("extraction")
    page = load_page()
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM},
        {"role": "user", "content": extraction_prompt(page)},
    ]
    try:
        reply = client.chat(messages)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.add(reply)
    result.score, result.notes = score_extraction(extract_json(reply.text), page)
    result.passed = result.score >= 0.85 and not any(n.startswith("выдумал") for n in result.notes)
    return result


# --- 3. Ингредиенты из описаний блюд --------------------------------------------------

INGREDIENTS_SYSTEM = (
    "Ты выделяешь ингредиенты блюд: продукты, которые кухня закупает у поставщиков. "
    "Названия в именительном падеже, единственном числе, без маркетинговых слов. "
    "Не включай блюда, способы приготовления и то, чего нет в описании. "
    'Ответ — только JSON: {"items": [{"id": 1, "ingredients": ["..."]}]}.'
)


def ingredients_prompt(dishes: list[dict]) -> str:
    return "\n".join(f"{d['id']}. {d['text']}" for d in dishes)


def _matches(ingredient: str, key: str) -> bool:
    text = norm(ingredient)
    return any(k in text for k in key.split("|"))


def score_ingredients(data, dishes: list[dict]) -> tuple[float, list[str]]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return 0.0, ["не вернул валидный JSON"]
    by_id = {}
    for item in data["items"]:
        if isinstance(item, dict) and isinstance(item.get("ingredients"), list):
            by_id[item.get("id")] = [str(x) for x in item["ingredients"]]
    notes: list[str] = []
    expected_total = found = produced = correct = 0
    for dish in dishes:
        out = by_id.get(dish["id"], [])
        if not out:
            notes.append(f"нет ответа для блюда {dish['id']}")
        keys = dish["expected"]
        expected_total += len(keys)
        found += sum(any(_matches(i, k) for i in out) for k in keys)
        allowed = keys + dish.get("optional", [])
        for ingredient in out:
            produced += 1
            if any(_matches(ingredient, k) for k in allowed):
                correct += 1
            else:
                notes.append(f"лишнее в блюде {dish['id']}: {ingredient}")
    recall = found / expected_total if expected_total else 0.0
    precision = correct / produced if produced else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return f1, notes


def ingredients(client: ModelClient) -> CaseResult:
    result = CaseResult("ingredients")
    dishes = load_dishes()
    messages = [
        {"role": "system", "content": INGREDIENTS_SYSTEM},
        {"role": "user", "content": ingredients_prompt(dishes)},
    ]
    try:
        reply = client.chat(messages)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result
    result.add(reply)
    result.score, result.notes = score_ingredients(extract_json(reply.text), dishes)
    result.passed = result.score >= 0.85
    return result


CASES = {
    "tools": tool_calling,
    "extraction": extraction,
    "ingredients": ingredients,
}
