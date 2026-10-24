"""Сетевые помощники: загрузка страниц и TLS-контекст."""

import gzip
import io
import ssl
import urllib.error
import urllib.request
from pathlib import Path

import certifi

from app.config import ENV_FILE, Settings

MAX_PAGE_BYTES = 20 * 1024 * 1024


def build_ssl_context(extra_ca_bundle: str | None = None) -> ssl.SSLContext:
    """Корневые сертификаты certifi и системные, плюс сертификаты НУЦ Минцифры, если файл есть.

    Многие российские сайты и сервисы подписаны сертификатами Минцифры, которым Python по умолчанию не доверяет.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_default_certs()
    if extra_ca_bundle:
        path = Path(extra_ca_bundle)
        if not path.is_absolute():
            path = ENV_FILE.parent / path
        if path.is_file():
            context.load_verify_locations(cafile=str(path))
    return context


def decode_body(body: bytes, content_encoding: str | None, charset: str = "utf-8") -> str:
    """Распаковывает gzip (если сайт сжал ответ) и превращает байты в текст."""
    if content_encoding and content_encoding.lower() == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as unpacked:
                body = unpacked.read(MAX_PAGE_BYTES + 1)
        except OSError as exc:
            raise RuntimeError(f"не удалось распаковать ответ: {exc}") from None
    if len(body) > MAX_PAGE_BYTES:
        raise RuntimeError("страница слишком большая")
    return body.decode(charset, errors="replace")


def http_get(url: str, user_agent: str, context: ssl.SSLContext, timeout: float = 40) -> str:
    """Синхронная загрузка страницы как текста (вызывать через asyncio.to_thread).

    Просит сайт сжать ответ: страница с меню весит 2,4 МБ без сжатия и около 300 КБ с gzip.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "text/html", "Accept-Encoding": "gzip"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read(MAX_PAGE_BYTES + 1)
            encoding = response.headers.get("Content-Encoding")
            charset = response.headers.get_content_charset() or "utf-8"
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    return decode_body(body, encoding, charset)


def settings_ssl_context(settings: Settings) -> ssl.SSLContext:
    return build_ssl_context(settings.extra_ca_bundle)
