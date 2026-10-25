"""Загрузка страниц сайтов и извлечение из них текста и полезных ссылок.

Адреса выбирает модель по результатам поиска, то есть они недоверенные: загрузчик отказывается ходить
на локальные и внутренние адреса (защита от SSRF), проверяет каждый редирект и ограничивает размер.
"""

import asyncio
import ipaddress
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from app.agent.config import USEFUL_LINK_WORDS
from app.agent.validation import host_of, same_site
from app.net import decode_body

MAX_BYTES = 2_000_000
MAX_REDIRECTS = 4
MAX_STORED_TEXT = 60_000
_SKIP = {"script", "style", "noscript", "svg", "iframe", "template", "head"}
_MENU = {"nav"}  # меню навигации: подписи ссылок нужны для выбора разделов, а в текст страницы оно не идёт
_BREAK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table", "ul", "ol",
          "footer", "header", "address", "dd", "dt", "form"}


class UnsafeUrl(ValueError):
    """Адрес нельзя загружать: не http(s), внутренний или не разрешается."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (url, подпись) всех ссылок на тот же сайт (до 150)
    site: str = ""  # домен, которому принадлежит страница


class _Extractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._menu_depth = 0
        self._chunks: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in _SKIP and tag != "head":
            self._skip_depth += 1
        if tag in _MENU:
            self._menu_depth += 1
        if tag in _BREAK:
            self._chunks.append("\n")
        if tag == "a" and attrs.get("href"):
            href = attrs["href"].strip()
            if href.startswith("tel:") or href.startswith("mailto:"):  # телефон и email часто лежат только в ссылке
                self._chunks.append(f" {href.split(':', 1)[1].split('?')[0]} ")
            else:
                self._href, self._link_text = href, []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and tag != "head" and self._skip_depth:
            self._skip_depth -= 1
        if tag in _MENU and self._menu_depth:
            self._menu_depth -= 1
        if tag in _BREAK:
            self._chunks.append("\n")
        if tag == "a" and self._href:
            text = re.sub(r"\s+", " ", "".join(self._link_text)).strip()
            self.links.append((urljoin(self.base_url, self._href), text))
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip_depth:
            return
        if not self._menu_depth:
            self._chunks.append(data)
        if self._href is not None:
            self._link_text.append(data)

    def text(self) -> str:
        lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in "".join(self._chunks).split("\n")]
        out: list[str] = []
        for line in lines:
            if line and (not out or out[-1] != line):
                out.append(line)
        return "\n".join(out)


def extract_page(html: str, url: str) -> Page:
    parser = _Extractor(url)
    parser.feed(html)
    host = host_of(url) or ""
    return Page(
        url=url,
        title=re.sub(r"\s+", " ", parser.title).strip(),
        text=parser.text()[:MAX_STORED_TEXT],
        links=_same_site_links(parser.links, host),
        site=host,
    )


MAX_SITE_LINKS = 150


def _same_site_links(links: list[tuple[str, str]], host: str) -> list[tuple[str, str]]:
    """Ссылки на тот же сайт без дублей и якорей: из них потом выбираются нужные страницы."""
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for href, text in links:
        href = urldefrag(href)[0]
        link_host = host_of(href)
        if not link_host or not same_site(link_host, host) or href in seen or len(result) >= MAX_SITE_LINKS:
            continue
        seen.add(href)
        result.append((href, text[:80]))
    return result


def useful_links(links: list[tuple[str, str]], limit: int = 8) -> list[tuple[str, str]]:
    """Самые полезные ссылки (контакты, о компании, доставка, прайс…) по ключевым словам."""
    scored: list[tuple[int, int, str, str]] = []
    for order, (href, text) in enumerate(links):
        haystack = f"{text} {urlsplit(href).path}".lower()
        score = sum(word in haystack for word in USEFUL_LINK_WORDS)
        if score:
            scored.append((-score, order, href, text[:60]))
    return [(href, text) for _, _, href, text in sorted(scored)[:limit]]


def normalize_url(url: str) -> str:
    url = urldefrag(url.strip())[0]
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def check_public_url(url: str) -> str:
    """Бросает UnsafeUrl, если адрес ведёт не в публичный интернет. Возвращает имя хоста."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeUrl("поддерживаются только http и https")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parts.hostname, parts.port or 443)}
    except socket.gaierror:
        raise UnsafeUrl("адрес не разрешается") from None
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise UnsafeUrl("внутренние и локальные адреса запрещены")
    return parts.hostname


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public_url(urljoin(req.full_url, newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_html(body: bytes, content_encoding: str | None, header_charset: str | None) -> str:
    charset = header_charset
    if not charset:
        match = re.search(rb"charset=[\"']?([a-zA-Z0-9_-]+)", body[:4096])
        charset = match.group(1).decode("ascii") if match else "utf-8"
    try:
        return decode_body(body, content_encoding, charset)
    except LookupError:
        return decode_body(body, content_encoding, "utf-8")


def fetch_html_sync(url: str, user_agent: str, context, timeout: float = 20) -> tuple[str, str]:
    """Возвращает (итоговый адрес после редиректов, html). Бросает UnsafeUrl или RuntimeError."""
    check_public_url(url)
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "gzip"}
    )
    opener = urllib.request.build_opener(_SafeRedirect, urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=timeout) as response:
            kind = (response.headers.get_content_type() or "").lower()
            if kind and not (kind.startswith("text/") or "html" in kind or "xml" in kind):
                raise RuntimeError(f"не страница: {kind}")
            body = response.read(MAX_BYTES + 1)
            final = response.geturl()
            encoding, charset = response.headers.get("Content-Encoding"), response.headers.get_content_charset()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, UnsafeUrl):
            raise reason from None
        raise RuntimeError(f"не удалось загрузить: {reason}") from None
    if len(body) > MAX_BYTES:
        raise RuntimeError("страница слишком большая")
    return final, _decode_html(body, encoding, charset)


class HttpFetcher:
    """Загрузчик страниц для агента (интерфейс: async fetch(url) -> Page)."""

    def __init__(self, user_agent: str, context):
        self._user_agent, self._context = user_agent, context

    async def fetch(self, url: str) -> Page:
        final, html = await asyncio.to_thread(fetch_html_sync, url, self._user_agent, self._context)
        return extract_page(html, final)
