"""Инструменты агента и проверки в коде: лимиты, кэш страниц, дедупликация, проверка цитат."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.agent.config import Limits, is_blocked
from app.agent.memory import MemoryPageCache
from app.agent.models import (
    AcceptedField,
    PageCache,
    PageFetcher,
    PlacesProvider,
    RunRequest,
    SearchProvider,
    SupplierDraft,
    SupplierSink,
)
from app.agent.pages import Page, UnsafeUrl, normalize_url, url_variants, useful_links
from app.agent.research import (
    FIELD_LABELS,
    SITE_QUERIES,
    Qualification,
    build_evidence,
    build_extraction_messages,
    build_qualify_messages,
    missing_fields,
    parse_extraction,
    parse_qualification,
    select_pages,
)
from app.agent.validation import (
    CONTACT_FIELDS,
    FIELDS,
    fact_problem,
    host_of,
    norm_text,
    normalize_value,
    product_stems,
    quote_in_text,
    same_site,
    split_phones,
    text_around,
    value_supported_by_quote,
)
from app.llm import LLMTruncated

Emit = Callable[..., Awaitable[None]]


def tool_schemas(places_enabled: bool, research_enabled: bool = False) -> list[dict]:
    def tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    tools = [
        tool(
            "search_web",
            "Поиск в интернете (Яндекс). Возвращает до 8 результатов: заголовок, адрес, отрывок. "
            "Маркетплейсы и каталоги-перепродавцы отфильтрованы.",
            {"query": {"type": "string", "description": "Поисковый запрос, лучше с городом или регионом"}},
            ["query"],
        ),
        tool(
            "fetch_page",
            "Загружает страницу и возвращает её текст и полезные ссылки сайта (контакты, о компании, доставка, прайс).",
            {"url": {"type": "string", "description": "Адрес страницы"}},
            ["url"],
        ),
        tool(
            "save_supplier",
            "Сохраняет поставщика. Каждое поле подтверждается дословной цитатой со страницы, открытой через fetch_page. "
            "Обязателен хотя бы один phone или email; без подтверждённого name поставщик сохраняется под доменом.",
            {
                "website": {"type": "string", "description": "Адрес главной страницы сайта поставщика"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "field": {"type": "string", "enum": sorted(FIELDS)},
                            "value": {"type": "string", "description": "Значение поля"},
                            "quote": {"type": "string", "description": "Дословная цитата со страницы"},
                            "source_url": {"type": "string", "description": "Адрес страницы с цитатой"},
                        },
                        "required": ["field", "value", "quote", "source_url"],
                    },
                },
            },
            ["website", "fields"],
        ),
        tool("finish", "Завершает работу с кратким итогом поиска.", {"summary": {"type": "string"}}, ["summary"]),
    ]
    if research_enabled:
        tools = [x for x in tools if x["function"]["name"] not in ("fetch_page", "save_supplier")]
        tools.insert(
            1,
            tool(
                "research_suppliers",
                "Изучает сайты поставщиков и сохраняет данные. Для каждого сайта код сам открывает главную, контакты, "
                "о компании, доставку, прайс и сертификаты, выделяет данные с цитатами и при необходимости ищет недостающее "
                "по самому сайту. Передавай от 1 до 3 адресов подходящих сайтов из результатов поиска. Возвращает, что "
                "сохранено и что не найдено на сайте.",
                {"websites": {"type": "array", "items": {"type": "string"}, "description": "Адреса сайтов поставщиков (до 3)"}},
                ["websites"],
            ),
        )
    if places_enabled:
        tools.insert(
            1,
            tool(
                "search_places",
                "Организации из 2GIS по запросу в городе: название, адрес, телефоны, сайт.",
                {"query": {"type": "string"}, "city": {"type": "string"}},
                ["query", "city"],
            ),
        )
    return tools


@dataclass
class Counters:
    searches: int = 0
    fetches: int = 0  # сетевые загрузки (страницы из кэша не считаются)
    tool_calls: int = 0
    saved: int = 0
    researched: int = 0  # изученных сайтов
    site_searches: int = 0  # поиски по сайту поставщика
    rejected: int = 0  # сайтов, отсеянных проверкой главной страницы
    reused: int = 0  # поставщиков, взятых из базы без нового извлечения
    search_errors: int = 0  # неудачных попыток поиска (включая те, после которых повтор удался)


@dataclass
class ToolResult:
    text: str
    ok: bool = True


class ToolBox:
    def __init__(
        self,
        request: RunRequest,
        limits: Limits,
        searcher: SearchProvider,
        fetcher: PageFetcher,
        sink: SupplierSink,
        emit: Emit,
        places: PlacesProvider | None = None,
        cache: PageCache | None = None,
        llm: object | None = None,
    ):
        self.request, self.limits, self.emit = request, limits, emit
        self.product = product_stems((request.category, *request.synonyms))  # для проверки, что цена на нужный продукт
        self.searcher, self.fetcher, self.sink, self.places = searcher, fetcher, sink, places
        self.cache = cache or MemoryPageCache()
        self.pages: dict[str, Page] = {}  # страницы этого запуска: только по ним проверяются цитаты
        self.saved_domains: dict[str, str] = {}  # домен → название
        self.counters = Counters()
        self.finished = False
        self.finish_refusals = 0
        self.summary: str | None = None
        self.llm = llm  # если задана, включается изучение сайтов (research_suppliers)
        self.extra_prompt_tokens = 0  # токены вызовов модели внутри инструментов (извлечение данных)
        self.extra_completion_tokens = 0
        self.loop_prompt_tokens = 0  # токены основного цикла; loop.py обновляет их после каждого ответа модели
        self.loop_completion_tokens = 0
        self.started = time.monotonic()
        self.supplier_fields: dict[str, set[str]] = {}  # домен → поля, принятые у поставщика в этом запуске
        self.research_summaries: dict[str, dict] = {}
        self.snippets: dict[str, str] = {}  # домен → отрывок из выдачи: подсказка для проверки сайта
        self.qualifications: dict[str, dict] = {}  # домен → итог проверки сайта (для отчёта и разбора качества)
        self.supplier_values: dict[str, dict[str, list[str]]] = {}  # домен → поле → принятые значения
        self.unconfirmed: set[str] = set()  # сохранены, но товар на сайте не подтверждён: в результаты и в цель не идут

    def schemas(self) -> list[dict]:
        return tool_schemas(self.places is not None, research_enabled=self.llm is not None)

    def status(self, turns_left: int | None = None) -> dict:
        data = {"сохранено_поставщиков": self.counters.saved, "цель": self.request.target}
        if turns_left is not None:
            data["осталось_шагов"] = turns_left
        return data

    async def execute(self, name: str, raw_args: str, turns_left: int | None = None) -> ToolResult:
        self.counters.tool_calls += 1
        try:
            args = json.loads(raw_args) if raw_args.strip() else {}
            if not isinstance(args, dict):
                raise ValueError("аргументы должны быть объектом")
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResult(self._wrap({"ошибка": f"некорректные аргументы: {exc}"}, turns_left), ok=False)

        handlers = {
            "search_web": self._search_web,
            "search_places": self._search_places,
            "fetch_page": self._fetch_page,
            "save_supplier": self._save_supplier,
            "research_suppliers": self._research_suppliers,
            "finish": self._finish,
        }
        handler = handlers.get(name)
        if handler is None or (name == "search_places" and self.places is None):
            return ToolResult(self._wrap({"ошибка": f"неизвестный инструмент: {name}"}, turns_left), ok=False)
        timeout = self.limits.research_timeout if name == "research_suppliers" else self.limits.tool_timeout
        try:
            result = await asyncio.wait_for(handler(args), timeout=timeout)
        except TimeoutError:
            result = {"ошибка": f"инструмент не ответил за {timeout} с"}
        except Exception as exc:  # сбой инструмента не должен ронять запуск: модель получит текст ошибки
            result = {"ошибка": str(exc)[:300]}
        return ToolResult(self._wrap(result, turns_left), ok="ошибка" not in result)

    def _wrap(self, result: dict, turns_left: int | None) -> str:
        return json.dumps({**result, "статус": self.status(turns_left)}, ensure_ascii=False)

    # --- поиск ---

    async def _search_web(self, args: dict) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            return {"ошибка": "пустой запрос"}
        if self.counters.searches >= self.limits.max_searches:
            return {"ошибка": "лимит поисковых запросов исчерпан: переходи к сохранению или завершению"}
        self.counters.searches += 1
        await self.emit("search", f"Ищу в Яндексе: {query}", query=query)
        try:
            hits = await self._search(query, self.request.search_region, "web")
        except Exception as exc:  # поиск не ответил и после повторов: запрос в лимит не идёт, модель может продолжить
            self.counters.searches -= 1
            return {"ошибка": (f"поиск временно не ответил ({str(exc)[:150]}). Это сбой поиска, а не отсутствие поставщиков: "
                               f"изучи уже найденные сайты через {'research_suppliers' if self.llm is not None else 'fetch_page'} "
                               "или повтори запрос другими словами")}
        shown, skipped, seen = [], 0, set()
        for hit in hits:
            if is_blocked(hit.domain):
                skipped += 1
            elif hit.domain not in seen:
                seen.add(hit.domain)
                self.snippets.setdefault(hit.domain, f"{hit.title}. {hit.snippet}"[:400])
                shown.append(
                    {"домен": hit.domain, "заголовок": hit.title, "адрес": hit.url, "отрывок": hit.snippet,
                     **({"уже_сохранён": True} if hit.domain in self.saved_domains else {})}
                )
        await self.emit("search_result", f"Найдено сайтов: {len(shown)} (отфильтровано маркетплейсов и каталогов: {skipped})",
                        found=len(shown), skipped=skipped)
        return {"результаты": shown[:8], "отфильтровано": skipped}

    async def _search(self, query: str, region: int | None, kind: str) -> list:
        """Поиск с отчётом о сбоях: каждая неудачная попытка (и повторённая, и последняя) попадает в ленту событием
        search_error с HTTP-статусом, кодом Яндекса и request-id. Раньше сбои поиска по сайту не было видно совсем."""
        drain = getattr(self.searcher, "drain_errors", None)
        try:
            return await self.searcher.search(query, region=region)
        except Exception as exc:
            reported = drain(query) if drain else []
            if not reported:  # провайдер без журнала попыток: сообщаем саму ошибку
                reported = [{"query": query, "attempt": 1, "retried": False, "status": getattr(exc, "status", None),
                             "code": getattr(exc, "code", None), "request_id": getattr(exc, "request_id", None),
                             "text": str(exc)[:300] or type(exc).__name__}]
            await self._report_search_errors(reported, kind, final=True)
            raise
        finally:
            if drain:  # успешный запрос после повторов: повторённые попытки тоже показываем
                await self._report_search_errors(drain(query), kind, final=False)

    async def _report_search_errors(self, errors: list[dict], kind: str, final: bool) -> None:
        for i, error in enumerate(errors):
            self.counters.search_errors += 1
            last = final and i == len(errors) - 1
            what = "поиск по сайту" if kind == "site" else "поиск"
            status = f"HTTP {error['status']}" if error.get("status") else "ошибка"
            code = f", код {error['code']}" if error.get("code") else ""
            outcome = "не ответил" if last else "повторяю" if error.get("retried") else "отказ"
            await self.emit("search_error", f"Сбой: {what} «{error['query'][:80]}» — {status}{code}, {outcome}",
                            kind=kind, final=last, **error)

    async def _search_places(self, args: dict) -> dict:
        query, city = str(args.get("query", "")).strip(), str(args.get("city", "")).strip()
        if not query or not city:
            return {"ошибка": "нужны query и city"}
        if self.counters.searches >= self.limits.max_searches:
            return {"ошибка": "лимит поисковых запросов исчерпан"}
        self.counters.searches += 1
        await self.emit("search", f"Ищу в 2GIS: {query}, {city}", query=query, city=city)
        found = []
        for place in (await self.places.search(query, city))[:8]:  # type: ignore[union-attr]
            site = host_of(place.website) if place.website else None
            text = "\n".join([place.name, place.address, *(f"Телефон: {p}" for p in place.phones),
                              *([f"Сайт: {place.website}"] if place.website else [])])
            url = f"https://2gis.ru/firm/{place.id}"
            self.pages[url] = Page(url=url, title=place.name, text=text, site=site or "")  # карточка годится как источник
            if site and is_blocked(site):
                continue
            found.append({"название": place.name, "адрес": place.address, "телефоны": list(place.phones),
                          "сайт": place.website, "рубрики": list(place.rubrics), "карточка": url})
        await self.emit("search_result", f"Организаций в 2GIS: {len(found)}", found=len(found))
        return {"организации": found}

    # --- страницы ---

    async def _get_page(self, raw_url: str) -> tuple[Page | None, str | None, bool]:
        """Страница из памяти запуска, кэша или сети. Возвращает (страница, причина ошибки, из ли кэша)."""
        url = normalize_url(raw_url)
        page = self.pages.get(url) or await self.cache.get(url)
        cached = page is not None
        if page is None:
            if self.counters.fetches >= self.limits.max_fetches:
                return None, "лимит загрузок страниц исчерпан", False
            self.counters.fetches += 1
            host = host_of(url) or url
            await self.emit("fetch", f"Читаю страницу: {host}{urlsplit(url).path or '/'}", url=url)
            try:
                page = await self.fetcher.fetch(url)
            except UnsafeUrl as exc:
                return None, f"адрес не разрешён: {exc}", False
            except Exception as exc:  # любой сетевой сбой (таймаут, обрыв, TLS) не должен ронять изучение сайта
                return None, f"страница не загрузилась: {str(exc) or type(exc).__name__}", False
            await self.cache.put(page)
        self.pages[page.url] = page
        self.pages[url] = page  # по запрошенному и по итоговому адресу (после редиректа)
        return page, None, cached

    async def _fetch_page(self, args: dict) -> dict:
        raw = str(args.get("url", "")).strip()
        if not raw:
            return {"ошибка": "пустой адрес"}
        page, error, cached = await self._get_page(raw)
        if page is None:
            return {"ошибка": f"{error}: сохраняй найденное и завершай" if "лимит" in (error or "") else error}
        text = page.text
        limit = self.limits.page_text_limit
        return {
            "адрес": page.url,
            "из_кэша": cached,
            "заголовок": page.title,
            "текст": text[:limit] + ("\n…[текст обрезан]" if len(text) > limit else ""),
            "ссылки": [{"адрес": href, "подпись": label} for href, label in useful_links(page.links, self.limits.max_links)],
        }

    # --- сохранение поставщика ---

    async def _save_supplier(self, args: dict) -> dict:
        website = str(args.get("website", "")).strip()
        domain = host_of(normalize_url(website)) if website else None
        if domain is None:
            return {"ошибка": "укажи адрес сайта поставщика (website)"}
        if is_blocked(domain):
            return {"ошибка": f"{domain}: маркетплейс, каталог или справочник, а не поставщик"}
        entries = args.get("fields")
        if not isinstance(entries, list) or not entries:
            return {"ошибка": "fields должен быть непустым списком"}

        accepted: list[AcceptedField] = []
        rejected: list[dict] = []
        seen_quotes: set[tuple[str, str]] = set()
        for entry in self._split_phone_entries(entries):
            if not isinstance(entry, dict):
                rejected.append({"поле": "?", "причина": "запись должна быть объектом"})
                continue
            field_name = str(entry.get("field", "")).strip()
            reason = self._check_entry(field_name, entry, domain)
            if isinstance(reason, str):  # значение — для разбора отказов (в ленте и в eval) и чтобы модель видела, что отклонено
                rejected.append({"поле": field_name or "?", "значение": str(entry.get("value", ""))[:150], "причина": reason})
                continue
            # одна и та же цитата не сохраняется дважды; у телефонов и email из одной цитаты могут быть разные значения
            key = (reason.field, reason.value if reason.field in CONTACT_FIELDS else norm_text(reason.quote))
            if key not in seen_quotes:
                seen_quotes.add(key)
                accepted.append(reason)

        fields_present = {f.field for f in accepted}
        # нового поставщика сохраняем только с подтверждённым контактом. Без подтверждённого названия он сохраняется
        # под доменом: название часто написано иначе, чем на сайте («Пак-Крафт» при «pack-craft.ru»), а терять из-за
        # этого поставщика с телефоном нельзя
        if domain not in self.saved_domains and not fields_present & CONTACT_FIELDS:
            await self.emit("supplier_rejected", f"Не сохранён {domain}: нет подтверждённых phone или email",
                            domain=domain, rejected=rejected)
            return {"ошибка": "не сохранено: нет подтверждённых полей — phone или email", "отклонено": rejected}

        parts = urlsplit(normalize_url(website))
        origin = f"{parts.scheme}://{parts.netloc}/"  # модель может дать адрес внутренней страницы: сохраняем главную
        outcome = await self.sink.save(SupplierDraft(website=origin, domain=domain, fields=accepted))
        if outcome.duplicate_of:
            await self.emit("supplier_rejected", f"{domain}: дубликат {outcome.duplicate_of} (тот же ИНН)", domain=domain)
            return {"ошибка": f"дубликат: поставщик с таким ИНН уже сохранён ({outcome.duplicate_of})"}
        if domain not in self.saved_domains:
            self.counters.saved += 1
        self.saved_domains[domain] = outcome.name
        self.supplier_fields.setdefault(domain, set()).update(fields_present)
        values = self.supplier_values.setdefault(domain, {})
        for f in accepted:
            values.setdefault(f.field, []).append(f.value)
        await self.emit(
            "supplier_saved",
            f"{'Сохранён' if outcome.created else 'Дополнен'} поставщик: {outcome.name} ({domain})",
            domain=domain, name=outcome.name, created=outcome.created,
            fields=sorted(fields_present), rejected=rejected,
        )
        result = {"сохранено": sorted(fields_present), "результат": "создан" if outcome.created else "дополнен"}
        if rejected:
            result["отклонено"] = rejected
        return result

    @staticmethod
    def _split_phone_entries(entries: list) -> list:
        """«+7 (495) 133-00-60, +7 (495) 640-24-94» в одном значении — два телефона с той же цитатой."""
        result = []
        for entry in entries:
            if isinstance(entry, dict) and str(entry.get("field", "")).strip() == "phone":
                result += [{**entry, "value": part} for part in split_phones(str(entry.get("value", "")))]
            else:
                result.append(entry)
        return result

    def _check_entry(self, field_name: str, entry: dict, domain: str) -> AcceptedField | str:
        """Возвращает принятое поле или текст причины отказа."""
        if field_name not in FIELDS:
            return f"неизвестное поле, допустимо: {', '.join(sorted(FIELDS))}"
        value, quote = str(entry.get("value", "")), str(entry.get("quote", ""))
        source = normalize_url(str(entry.get("source_url", "")))
        page = next((self.pages[u] for u in url_variants(source) if u in self.pages), None)
        if page is None:
            opened = sorted({p.url for p in self.pages.values() if not p.url.startswith("https://2gis.ru")})[:8]
            return ("source_url: эту страницу ты не открывал через fetch_page в этом запуске. "
                    f"Открытые страницы: {', '.join(opened) or 'пока нет'}")
        if not page.site or not same_site(page.site, domain):
            return f"source_url не принадлежит сайту {domain}"
        if not quote_in_text(quote, page.text):
            return "цитата не найдена на странице дословно: цитируй текст именно со страницы source_url, а не из результатов поиска"
        normalized, problem = normalize_value(field_name, value)
        if normalized is None:
            return problem or "некорректное значение"
        if not value_supported_by_quote(field_name, normalized, quote):
            return "значение не следует из цитаты"
        if problem := fact_problem(field_name, normalized, quote, self.product, f"{page.title} {text_around(quote, page.text)}"):
            return problem
        return AcceptedField(field=field_name, value=normalized, quote=quote.strip(), source_url=page.url)

    def _may_finish(self) -> bool:
        """Завершать можно, если цель достигнута, поиск добросовестно выполнен или бюджет исчерпан."""
        # в режиме изучения сайтов страницы открывает код (6 и больше за сайт), поэтому считаются проверенные сайты
        checked = (self.counters.researched + self.counters.rejected >= 3) if self.llm is not None else self.counters.fetches >= 4
        thorough = self.counters.searches >= 3 and checked
        spent = (
            self.counters.searches >= self.limits.max_searches
            or self.counters.fetches >= self.limits.max_fetches
            or self.counters.researched >= self.limits.max_research
        )
        return self.counters.saved >= self.request.target or thorough or spent

    async def _finish(self, args: dict) -> dict:
        # Модель склонна сдаваться после первой находки: один раз просим продолжить, повторный finish принимаем
        if not self._may_finish() and self.finish_refusals == 0:
            self.finish_refusals += 1
            how = ("передай другие сайты из результатов поиска в research_suppliers" if self.llm is not None
                   else "открой их через fetch_page и сохрани подходящих")
            return {
                "ошибка": (
                    f"Рано завершать: сохранено {self.counters.saved} из {self.request.target}, а проверено мало сайтов "
                    f"(поисков {self.counters.searches}, изучено сайтов {self.counters.researched + self.counters.rejected}). "
                    f"Выбери другие сайты, {how}; можно изменить формулировку запроса. "
                    "Если подходящих поставщиков действительно больше нет, вызови finish ещё раз."
                )
            }
        self.finished = True
        self.summary = str(args.get("summary", "")).strip() or None
        return {"результат": "работа завершена"}

    # --- изучение сайтов поставщиков ---

    async def _fetch_many(self, urls: list[str]) -> list[Page]:
        gate = asyncio.Semaphore(3)

        async def one(url: str) -> Page | None:
            async with gate:
                page, _, _ = await self._get_page(url)
                return page

        return [page for page in await asyncio.gather(*(one(u) for u in urls)) if page is not None]

    async def emit_usage(self) -> None:
        """Текущий расход: токены модели (цикл и изучение сайтов) и запросы к поиску. Цену добавляет тот, кто знает тарифы."""
        await self.emit(
            "usage", "",
            prompt_tokens=self.loop_prompt_tokens + self.extra_prompt_tokens,
            completion_tokens=self.loop_completion_tokens + self.extra_completion_tokens,
            searches=self.counters.searches, site_searches=self.counters.site_searches,
            seconds=round(time.monotonic() - self.started, 1),
        )

    async def _llm(self, messages: list[dict], max_tokens: int):
        """Вызов модели внутри инструмента. Токены считаются и у обрезанного ответа: он тоже оплачен."""
        try:
            reply = await self.llm.chat(messages, None, max_tokens=max_tokens)  # type: ignore[union-attr]
        except LLMTruncated as exc:
            self.extra_prompt_tokens += exc.prompt_tokens
            self.extra_completion_tokens += exc.completion_tokens
            await self.emit_usage()
            raise
        self.extra_prompt_tokens += getattr(reply, "prompt_tokens", 0)
        self.extra_completion_tokens += getattr(reply, "completion_tokens", 0)
        await self.emit_usage()
        return reply

    async def _extract_and_save(self, origin: str, domain: str, pages: list[Page], focus: list[str] | None, split: int = 1) -> dict:
        """Один вызов модели: извлечь поля из текстов страниц. Затем те же проверки, что у save_supplier.

        Если ответ обрезан по длине (много страниц и полей), страницы делятся на две половины и разбираются по отдельности.
        """
        evidence = build_evidence(pages, self.limits.evidence_chars_per_page)
        messages = build_extraction_messages(self.request, evidence, focus)
        for _ in range(2):  # ответ не по формату повторяем один раз
            try:
                reply = await self._llm(messages, self.limits.extraction_max_tokens)
            except LLMTruncated:
                if split <= 0 or len(pages) < 2:
                    return {"ошибка": "ответ модели обрезан по длине"}
                half = len(pages) // 2
                first = await self._extract_and_save(origin, domain, pages[:half], focus, split - 1)
                second = await self._extract_and_save(origin, domain, pages[half:], focus, split - 1)
                return second if "ошибка" in first else first
            extraction = parse_extraction(getattr(reply, "text", None))
            if not extraction.error:
                break
        else:
            return {"ошибка": f"извлечение данных не удалось: {extraction.error}"}
        if not extraction.entries:
            return {"сохранено": []}
        return await self._save_supplier({"website": origin, "fields": extraction.entries})

    async def _qualify(self, domain: str, home: Page):
        """Короткая проверка главной страницы: продаёт ли сайт нужный продукт бизнесу. Сбой проверки сайт не отсеивает."""
        messages = build_qualify_messages(self.request, home, self.snippets.get(domain))
        try:
            reply = await self._llm(messages, 300)
        except Exception as exc:
            return Qualification(error=f"проверка не удалась: {str(exc)[:120] or type(exc).__name__}")
        return parse_qualification(getattr(reply, "text", None))

    async def _research_supplier(self, website: str) -> dict:
        domain = host_of(normalize_url(website)) if website else None
        if domain is None:
            return {"сайт": website, "результат": "некорректный адрес"}
        if is_blocked(domain):
            return {"сайт": domain, "результат": "маркетплейс, каталог или справочник: не поставщик"}
        if domain in self.research_summaries:
            return {**self.research_summaries[domain], "примечание": "этот сайт уже изучен в этом запуске"}
        if self.counters.researched >= self.limits.max_research:
            return {"сайт": domain, "результат": "лимит изучаемых сайтов исчерпан"}
        self.research_summaries[domain] = {"сайт": domain, "результат": "изучается"}  # второй такой же вызов не начнёт заново
        parts = urlsplit(normalize_url(website))
        origin = f"{parts.scheme}://{parts.netloc}/"
        await self.emit("research", f"Изучаю сайт поставщика: {domain}", domain=domain)

        home, error, _ = await self._get_page(origin)
        if home is None:
            await self.emit("research_failed", f"Сайт {domain} не открылся: {error}", domain=domain)
            failure = {"сайт": domain, "результат": f"главная страница не открылась: {error}. Этот сайт больше не пробуй"}
            self.research_summaries[domain] = failure  # повторно сайт не открываем: не тратим шаги
            return failure

        qualification = None
        if self.limits.qualify:
            verdict = await self._qualify(domain, home)
            qualification = {"supplier": verdict.supplier, "product": verdict.product, "reason": verdict.reason,
                             **({"error": verdict.error} if verdict.error else {})}
            self.qualifications[domain] = qualification
            if verdict.rejected:
                self.counters.rejected += 1
                reason = verdict.reason or "по главной странице не понять, продаёт ли компания нужный продукт бизнесу"
                await self.emit("research_skipped", f"{domain}: не подходит — {reason}", domain=domain, reason=reason)
                summary = {"сайт": domain, "результат": f"не подходит, не сохранён: {reason}. Ищи другие сайты"}
                self.research_summaries[domain] = summary
                return summary
        self.counters.researched += 1

        reuse = getattr(self.sink, "reuse", None)
        reused = await reuse(domain, self.limits.reuse_days) if reuse is not None else None
        if reused is not None:  # уже подробно изучен недавно: поля в базе, повторно за извлечение не платим
            self.counters.reused += 1
            if domain not in self.saved_domains:
                self.counters.saved += 1
            self.saved_domains[domain] = reused.name
            self.supplier_fields.setdefault(domain, set()).update(reused.fields)
            self.supplier_values.setdefault(domain, {}).update({k: list(v) for k, v in reused.fields.items()})
            missing = missing_fields(set(reused.fields))
            if not self._product_confirmed(domain):
                return await self._drop_unconfirmed(domain, len(reused.fields))
            await self.emit("supplier_saved", f"Уже изучен недавно, беру сохранённые данные: {reused.name} ({domain})",
                            domain=domain, name=reused.name, created=False, fields=sorted(reused.fields), rejected=[])
            summary = {"сайт": domain, "результат": "сохранён (данные изучения за последние дни)",
                       "сохранено_полей": sorted(reused.fields), "не_найдено_на_сайте": [FIELD_LABELS[f] for f in missing]}
            self.research_summaries[domain] = summary
            return summary

        checked = [home]
        checked += await self._fetch_many(
            select_pages(home.links, self.limits.research_pages_first, exclude={home.url, origin, normalize_url(origin)})
        )
        await self.emit("research_extract", f"Выделяю данные с {len(checked)} страниц сайта {domain}", domain=domain, pages=len(checked))
        first = await self._extract_and_save(origin, domain, checked, None)

        # второй проход: обязательные по ТЗ поля, которых не нашлось, ищем по самому сайту (site:домен …);
        # сайт, где не нашлось даже контакта, дальше не изучаем: это не поставщик или сайт не читается
        missing = missing_fields(self.supplier_fields.get(domain, set()))
        used = 0
        for group, query in SITE_QUERIES if domain in self.saved_domains else []:
            wanted = [f for f in group if f in missing]
            if not wanted:
                continue
            if used >= self.limits.max_site_searches_per_supplier or self.counters.site_searches >= self.limits.max_site_searches:
                break
            used += 1
            self.counters.site_searches += 1
            await self.emit_usage()
            labels = ", ".join(FIELD_LABELS[f] for f in wanted)
            await self.emit("research_more", f"Не нашёл на {domain}: {labels}. Ищу по сайту", domain=domain, missing=wanted)
            try:
                hits = await self._search(f"site:{domain} {query}", None, "site")
            except Exception:  # сбой уже в ленте (search_error); недостающие поля этого сайта остаются ненайденными
                hits = []
            urls = []
            for hit in hits:
                if same_site(hit.domain, domain) and normalize_url(hit.url) not in self.pages and hit.url not in urls:
                    urls.append(hit.url)
            extra = await self._fetch_many(urls[: self.limits.research_pages_second])
            if extra:
                checked += extra
                await self._extract_and_save(origin, domain, extra, wanted)
                missing = missing_fields(self.supplier_fields.get(domain, set()))

        saved = domain in self.saved_domains
        record = getattr(self.sink, "record_research", None)
        if saved and record is not None:
            await record(domain, sorted({p.url for p in checked}), missing, qualification)
        if saved and not self._product_confirmed(domain):
            return await self._drop_unconfirmed(domain, len(checked))
        have = sorted(self.supplier_fields.get(domain, set()))
        summary = {
            "сайт": domain,
            "результат": "сохранён" if saved else "не сохранён: на страницах не нашлось подтверждённого телефона или email",
            "сохранено_полей": have,
            "не_найдено_на_сайте": [FIELD_LABELS[f] for f in missing] if saved else [],
            "изучено_страниц": len(checked),
        }
        if not saved and isinstance(first, dict) and first.get("ошибка"):
            summary["причина"] = str(first["ошибка"])[:200]
        self.research_summaries[domain] = summary
        await self.emit(
            "research_done",
            f"{domain}: изучено страниц {len(checked)}, не найдено: {', '.join(summary['не_найдено_на_сайте']) or 'всё нашлось'}",
            domain=domain, pages=len(checked), missing=missing,
        )
        return summary

    def _product_confirmed(self, domain: str) -> bool:
        """Тот же критерий, что при показе результатов: товар назван в прайсе, ассортименте или описании."""
        from app.ranking.scoring import FieldInfo, RankContext, SupplierInfo, product_confirmed

        values = self.supplier_values.get(domain, {})
        info = SupplierInfo(id=0, domain=domain, name=self.saved_domains.get(domain, domain), inn=None,
                            fields={name: [FieldInfo(v) for v in items] for name, items in values.items()})
        return product_confirmed(info, RankContext(category=self.request.category, synonyms=self.request.synonyms))

    async def _drop_unconfirmed(self, domain: str, pages: int) -> dict:
        """Сайт изучен, но нужный товар на нём не назван: в результатах такого поставщика не будет, в цель он не идёт."""
        if domain not in self.unconfirmed:
            self.unconfirmed.add(domain)
            self.counters.saved -= 1
        await self.emit("research_skipped", f"{domain}: товар «{self.request.category}» на сайте не подтверждён — в результаты не попадёт",
                        domain=domain, reason="товар не подтверждён")
        summary = {"сайт": domain, "результат": f"не подходит: товар «{self.request.category}» на сайте не назван. Ищи другие сайты",
                   "изучено_страниц": pages}
        self.research_summaries[domain] = summary
        return summary

    async def _research_one(self, site: str) -> dict:
        """Один сайт со своим таймаутом: медленный сайт не отменяет изучение остальных, и повторно его не изучаем."""
        try:
            return await asyncio.wait_for(self._research_supplier(site), timeout=self.limits.research_site_timeout)
        except TimeoutError:
            domain = host_of(normalize_url(site)) or site
            saved = domain in self.saved_domains
            summary = {"сайт": domain, "результат": ("сохранён частично: сайт отвечал слишком медленно" if saved
                                                    else "не изучен: сайт отвечает слишком медленно. Больше не пробуй"),
                       "сохранено_полей": sorted(self.supplier_fields.get(domain, set()))}
            self.research_summaries[domain] = summary
            await self.emit("research_failed", f"{domain}: не успел изучить за {self.limits.research_site_timeout} с", domain=domain)
            return summary
        except Exception as exc:  # сбой на одном сайте не ломает остальные; сайт можно передать ещё раз
            domain = host_of(normalize_url(site)) or site
            if self.research_summaries.get(domain, {}).get("результат") == "изучается":
                del self.research_summaries[domain]
            return {"сайт": site, "результат": f"ошибка при изучении: {str(exc)[:150] or type(exc).__name__}"}

    async def _research_suppliers(self, args: dict) -> dict:
        sites = args.get("websites")
        if sites is None and args.get("website"):
            sites = [args["website"]]
        if isinstance(sites, str):
            sites = [sites]
        if not isinstance(sites, list) or not sites:
            return {"ошибка": "websites должен быть непустым списком адресов"}
        if self.llm is None:
            return {"ошибка": "изучение сайтов недоступно"}
        # до цели не хватает меньше трёх: лишние сайты не изучаем, за них пришлось бы платить
        need = max(1, self.request.target - self.counters.saved)
        selected = [str(s) for s in sites[: min(3, need)]]
        outcomes = await asyncio.gather(*(self._research_one(s) for s in selected), return_exceptions=True)
        results = [
            {"сайт": site, "результат": f"ошибка при изучении: {str(out)[:150] or type(out).__name__}"}
            if isinstance(out, BaseException) else out
            for site, out in zip(selected, outcomes)
        ]
        result: dict = {"сайты": results}
        if len(sites) > len(selected):
            result["примечание"] = (f"изучены первые {len(selected)}: до цели не хватало {need}. "
                                    "Остальные передай позже, если эти не подойдут")
        return result
