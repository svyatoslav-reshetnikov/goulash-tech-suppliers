"""Export of a search result to .xlsx (hand-written minimal writer, no new dependency) or CSV (UTF-8 with BOM, ';' delimiter).

Columns follow the table view, then add everything else a purchasing manager may need offline. Values come from the same payload
the UI renders, so the file matches the screen. Untrusted scraped text is written as plain strings; in CSV, cells that Excel could
read as a formula are prefixed with an apostrophe.
"""

import csv
import io
import re
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

from app.ranking.commerce import compact_price

HEADERS = [
    "#", "Поставщик", "Тип", "Оценка", "Регион", "Цена на искомый товар (за ед.)", "Минимальный заказ", "Доставка", "Телефон", "Email",
    "Предупреждений",
    "Цена на искомый товар (полностью)", "Прочие цены", "Все телефоны", "Все email", "Адрес", "Сайт", "ИНН", "Заметка", "Предупреждения",
    "Источники", "Данные собраны", "Параметры поиска", "Дата поиска",
]
_WIDTHS = [5, 30, 14, 8, 24, 22, 22, 40, 20, 28, 10, 44, 44, 30, 34, 40, 28, 14, 40, 50, 60, 14, 40, 12]
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_PHONE_LIKE = re.compile(r"^\+\d[\d\s()\-]+$")


def _values(item: dict, field: str) -> list[str]:
    return [v["value"] for v in item["fields"].get(field, [])]


def _date(value: str | None) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def search_parameters(run: dict) -> str:
    where = run.get("location") or "Вся Россия"
    parts = [run["category"], where, "с соседними регионами" if run.get("include_neighbors") else "без соседних регионов"]
    return "; ".join(parts)


def export_rows(run: dict, items: list[dict]) -> list[list[str | int]]:
    """One row per supplier in the given order; text columns are strings, rank/score/warning count are numbers."""
    rows: list[list[str | int]] = []
    for item in items:
        ranking, commerce = item["ranking"], item.get("commerce") or {}
        headline, order = commerce.get("price_headline"), commerce.get("min_order")
        warns = [w["text"] for w in ranking["warnings"] if w["severity"] == "warn"]
        sources = list(dict.fromkeys(v["source_url"] for values in item["fields"].values() for v in values))
        rows.append([
            ranking["rank"], item["name"], (_values(item, "supplier_type") or [""])[0], ranking["score"], ranking["tier_label"],
            compact_price(headline) if headline else "", order["display"] if order else "", (_values(item, "delivery_terms") or [""])[0],
            (_values(item, "phone") or [""])[0], (_values(item, "email") or [""])[0], len(warns),
            headline["display"] if headline else "",
            "\n".join(p["display"] for p in commerce.get("prices_other", [])),
            "\n".join(_values(item, "phone")), "\n".join(_values(item, "email")), "\n".join(_values(item, "address")),
            item["website"], item["inn"] or "", item["note"] or "", "\n".join(warns), "\n".join(sources),
            _date(item.get("collected_at")), search_parameters(run), _date(run.get("created_at")),
        ])
    return rows


def _clean(value: str | int) -> str:
    return _ILLEGAL.sub("", str(value))


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def build_xlsx(headers: list[str], rows: list[list[str | int]]) -> bytes:
    def cell(row: int, col: int, value: str | int, style: int) -> str:
        ref = f"{_column(col)}{row}"
        if isinstance(value, int) and not isinstance(value, bool):
            return f'<c r="{ref}" s="{style}"><v>{value}</v></c>'
        return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(_clean(value))}</t></is></c>'

    sheet_rows = ['<row r="1">' + "".join(cell(1, c, h, 1) for c, h in enumerate(headers)) + "</row>"]
    for r, row in enumerate(rows, start=2):
        sheet_rows.append(f'<row r="{r}">' + "".join(cell(r, c, v, 2) for c, v in enumerate(row)) + "</row>")
    last = f"{_column(len(headers) - 1)}{len(rows) + 1}"
    cols = "".join(f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>' for i, w in enumerate(_WIDTHS[: len(headers)], start=1))
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"<cols>{cols}</cols><sheetData>{''.join(sheet_rows)}</sheetData><autoFilter ref=\"A1:{last}\"/></worksheet>"
    )
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        ),
        "xl/workbook.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Поставщики" sheetId="1" r:id="rId1"/></sheets></workbook>'
        ),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            "</Relationships>"
        ),
        "xl/styles.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
            '<fill><patternFill patternType="solid"><fgColor rgb="FFE2E8F0"/><bgColor indexed="64"/></patternFill></fill></fills>'
            '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf>'
            '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="top" wrapText="1"/></xf></cellXfs>'
            "</styleSheet>"
        ),
        "xl/worksheets/sheet1.xml": sheet,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _csv_safe(value: str | int) -> str:
    text = _clean(value)
    if text[:1] in ("=", "@", "\t", "\r") or (text[:1] in ("+", "-") and not _PHONE_LIKE.match(text) and not re.match(r"^-?\d", text)):
        return "'" + text
    return text


def build_csv(headers: list[str], rows: list[list[str | int]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_csv_safe(v) for v in row])
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8")


def file_name(run: dict, extension: str, today: datetime | None = None) -> str:
    """suppliers_<product>_<region>_<YYYY-MM-DD>.<ext>; unsafe characters become underscores."""
    def part(text: str) -> str:
        return re.sub(r"[^\w\-]+", "_", text, flags=re.UNICODE).strip("_")[:40] or "x"

    date = (today or datetime.now()).strftime("%Y-%m-%d")
    return f"suppliers_{part(run['category'])}_{part((run.get('location') or 'Вся Россия').split(',')[0])}_{date}.{extension}"
