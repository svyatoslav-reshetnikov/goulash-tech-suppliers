"""Deterministic supplier warnings (no LLM): each one is a code, a severity and a Russian text for the UI."""

import re
from dataclasses import asdict, dataclass

FREE_MAIL = {
    "gmail.com", "mail.ru", "yandex.ru", "ya.ru", "yandex.com", "bk.ru", "inbox.ru", "list.ru", "internet.ru", "outlook.com",
    "hotmail.com", "icloud.com", "rambler.ru", "yahoo.com", "proton.me", "protonmail.com", "me.com", "live.com",
}
# second-level public suffixes under which the registrable domain has three labels
_COMPOUND = {"com.ru", "net.ru", "org.ru", "msk.ru", "spb.ru", "pp.ru", "co.uk", "com.ua", "com.cn", "co.jp"}


@dataclass(frozen=True)
class Warning:
    code: str
    severity: str  # "warn" | "info"
    text: str


def registrable_domain(host: str) -> str:
    """"www.shop.example.ru" -> "example.ru"; "a.b.msk.ru" -> "b.msk.ru"."""
    labels = [part for part in host.lower().strip(".").split(".") if part]
    if labels and labels[0] == "www":
        labels = labels[1:]
    if len(labels) <= 2:
        return ".".join(labels)
    return ".".join(labels[-3:] if ".".join(labels[-2:]) in _COMPOUND else labels[-2:])


def email_domain(email: str) -> str | None:
    match = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})\s*$", email.strip().lower())
    return registrable_domain(match.group(1)) if match else None


def email_mismatches(emails: list[str], site_domain: str) -> list[str]:
    """Registrable domains of contact emails that differ from the supplier site (free mail providers are ignored)."""
    site = registrable_domain(site_domain)
    found: list[str] = []
    for email in emails:
        domain = email_domain(email)
        if domain and domain not in FREE_MAIL and domain != site and domain not in found:
            found.append(domain)
    return found


def build_warnings(
    *, site_domain: str, emails: list[str], pages_studied: int | None, tier: str, has_inn: bool, substitute: bool = False,
) -> list[dict]:
    result: list[Warning] = []
    for domain in email_mismatches(emails, site_domain)[:1]:
        result.append(Warning("email_domain_mismatch", "warn",
                              f"Домен почты ({domain}) не совпадает с сайтом ({registrable_domain(site_domain)}) — проверьте поставщика"))
    if pages_studied is not None and pages_studied <= 1:
        result.append(Warning("low_evidence", "warn", "Изучена только 1 страница сайта"))
    if tier == "unknown":
        result.append(Warning("region_unknown", "warn", "Регион неизвестен"))
    if substitute:
        result.append(Warning("substitute_product", "warn", "В ассортименте есть продукт с заменителем молочного жира: уточните, предлагают ли натуральный продукт"))
    if not has_inn:
        result.append(Warning("no_inn", "info", "ИНН не найден"))
    return [asdict(w) for w in result]
