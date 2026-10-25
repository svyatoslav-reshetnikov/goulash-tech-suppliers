"""Ограничения запуска и списки сайтов, которые не считаются поставщиками."""

from dataclasses import dataclass

# Маркетплейсы, доски объявлений, соцсети, справочники организаций, энциклопедии: не поставщики.
MARKETPLACES = {
    "avito.ru", "youla.ru", "ozon.ru", "wildberries.ru", "market.yandex.ru", "sbermegamarket.ru", "aliexpress.ru",
    "aliexpress.com", "lamoda.ru", "yandex.ru", "ya.ru", "dzen.ru", "vk.com", "ok.ru", "t.me", "youtube.com",
    "instagram.com", "facebook.com", "wikipedia.org", "hh.ru", "2gis.ru", "yell.ru", "zoon.ru", "otzovik.com",
    "irecommend.ru", "pikabu.ru", "rutube.ru", "mail.ru", "ru.wikipedia.org", "google.com",
}

# Каталоги-перепродавцы и B2B-площадки: страницы с десятками чужих предложений, а не сайт поставщика.
DIRECTORIES = {
    "pulscen.ru", "tiu.ru", "regtorg.ru", "agroserver.ru", "all-biz.ru", "b2b-center.ru", "fabrikant.ru",
    "zakupki.gov.ru", "rusprofile.ru", "list-org.com", "spark-interfax.ru", "checko.ru", "satom.ru", "prom.ua",
    "alibaba.com", "made-in-china.com", "1688.com", "produkty-optom.ru", "optlist.ru", "supl.biz", "orgpage.ru",
    "flagma.ru", "salesup.ru", "oborot.ru", "biz360.ru",
}

BLOCKED_DOMAINS = MARKETPLACES | DIRECTORIES

# Слова в ссылках, по которым на сайте поставщика ищутся нужные страницы
USEFUL_LINK_WORDS = (
    "контакт", "contact", "о компании", "о нас", "about", "доставк", "delivery", "оплат", "прайс", "price",
    "каталог", "catalog", "продукц", "сертифик", "партнёр", "партнер", "опт", "реквизит", "поставщик",
)


@dataclass(frozen=True)
class Limits:
    max_turns: int = 24
    max_seconds: int = 600
    max_total_tokens: int = 200_000  # вместе с вызовами модели при изучении сайтов
    target_suppliers: int = 5
    max_fetches: int = 45
    max_searches: int = 8  # поиски кандидатов; поиски по сайту поставщика считаются отдельно
    max_site_searches: int = 12
    max_site_searches_per_supplier: int = 3
    max_research: int = 8  # сколько сайтов можно изучить за запуск
    research_pages_first: int = 6  # страниц сайта в первом проходе (главная и разделы)
    research_pages_second: int = 3  # страниц на один дополнительный поиск по сайту
    evidence_chars_per_page: int = 3200
    tool_timeout: int = 45
    research_timeout: int = 240  # весь вызов research_suppliers (сайты изучаются параллельно)
    research_site_timeout: int = 180  # один сайт: медленный сайт не отменяет изучение остальных
    qualify: bool = True  # перед подробным изучением короткая проверка главной: продаёт ли сайт нужное бизнесу
    reuse_days: int = 7  # поставщик, изученный за эти дни, берётся из базы без повторного извлечения
    extraction_max_tokens: int = 4096
    page_text_limit: int = 5000  # сколько символов страницы видит модель при ручном fetch_page
    max_links: int = 8
    keep_recent_tool_results: int = 3  # более старые результаты инструментов сжимаются, чтобы контекст не рос


def is_blocked(domain: str) -> bool:
    domain = domain.lower().removeprefix("www.")
    return any(domain == d or domain.endswith("." + d) for d in BLOCKED_DOMAINS)
