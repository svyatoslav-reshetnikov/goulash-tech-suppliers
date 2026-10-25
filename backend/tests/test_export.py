"""Export: xlsx structure and content, CSV safety, file name; rows match what the table view shows."""

import io
import json
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from app.export import HEADERS, build_csv, build_xlsx, export_rows, file_name, search_parameters
from app.ranking.commerce import compact_price

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ITEMS = json.loads((Path(__file__).parents[2] / "frontend/src/test/fixtures/search-rice-moscow.json").read_text(encoding="utf-8"))["suppliers"]
RUN = {"category": "Рис для суши", "location": "Москва", "include_neighbors": False, "supplier_type": None, "created_at": "2026-10-24T19:00:00+00:00"}
for item in ITEMS:
    item.setdefault("status", {"value": "new", "updated_at": None})
    item.setdefault("collected_at", "2026-10-24T19:05:00+00:00")


def read_sheet(data: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/_rels/workbook.xml.rels", "xl/styles.xml", "xl/worksheets/sheet1.xml"} <= names
        for name in names:
            ET.fromstring(archive.read(name))  # every part is well-formed XML
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    table = []
    for row in sheet.findall(".//m:row", NS):
        cells = []
        for c in row.findall("m:c", NS):
            text = c.find("m:is/m:t", NS)
            value = c.find("m:v", NS)
            cells.append(text.text or "" if text is not None else value.text if value is not None else "")
        table.append(cells)
    return table


def test_xlsx_has_header_rows_in_rank_order_and_cyrillic():
    rows = export_rows(RUN, ITEMS)
    table = read_sheet(build_xlsx(HEADERS, rows))
    assert table[0] == HEADERS and len(table) == 1 + len(ITEMS)
    assert [r[1] for r in table[1:]] == [i["name"] for i in ITEMS]  # same order as the list
    assert table[1][1] == "SuomiFish" and table[1][0] == "1"
    assert "Рис для суши" in table[1][HEADERS.index("Параметры поиска")]
    assert table[1][HEADERS.index("Дата поиска")] == "24.10.2026" and table[1][HEADERS.index("Данные собраны")] == "24.10.2026"


def test_export_columns_match_the_table_view():
    rows = export_rows(RUN, ITEMS)
    for item, row in zip(ITEMS, rows, strict=True):
        r = item["ranking"]
        commerce = item["commerce"]
        assert row[:5] == [r["rank"], item["name"], (item["fields"].get("supplier_type") or [{"value": ""}])[0]["value"], r["score"], r["tier_label"]]
        assert row[5] == (compact_price(commerce["price_headline"]) if commerce["price_headline"] else "")
        assert row[6] == (commerce["min_order"]["display"] if commerce["min_order"] else "")
        assert row[10] == len([w for w in r["warnings"] if w["severity"] == "warn"]) and row[11] == "Новый"
    suomi = rows[0]
    assert suomi[5] == "≈84 ₽/кг" and "info@suomifish.ru" in suomi[HEADERS.index("Все email")]
    assert suomi[HEADERS.index("Адрес")].startswith("127224") and suomi[HEADERS.index("Сайт")] == "https://suomifish.ru/"
    assert suomi[HEADERS.index("Источники")].count("https://") >= 3


def test_status_and_notes_are_exported():
    items = [{**ITEMS[0], "status": {"value": "awaiting_price", "updated_at": "2026-10-25T10:00:00+00:00"}, "note": "Звонили 25.10"}]
    row = export_rows(RUN, items)[0]
    assert row[HEADERS.index("Статус")] == "Ждём прайс" and row[HEADERS.index("Заметка")] == "Звонили 25.10"


def test_xlsx_keeps_untrusted_text_as_text():
    nasty = [{**ITEMS[0], "name": '=HYPERLINK("http://evil.test","x") & <b>"1"</b>', "note": "tab\x00bell\x07ok"}]
    table = read_sheet(build_xlsx(HEADERS, export_rows(RUN, nasty)))
    assert table[1][1] == '=HYPERLINK("http://evil.test","x") & <b>"1"</b>'  # a string cell, never a formula
    assert table[1][HEADERS.index("Заметка")] == "tabbellok"  # control characters that break XML are dropped
    with zipfile.ZipFile(io.BytesIO(build_xlsx(HEADERS, export_rows(RUN, nasty)))) as archive:
        assert "<f>" not in archive.read("xl/worksheets/sheet1.xml").decode()


def test_csv_is_utf8_with_bom_semicolon_delimited_and_formula_safe():
    nasty = [{**ITEMS[0], "name": "=1+1", "note": "@SUM(A1)"}, ITEMS[1]]
    data = build_csv(HEADERS, export_rows(RUN, nasty))
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    lines = text.split("\r\n")
    assert lines[0].split(";")[:3] == ["#", "Поставщик", "Тип"]
    assert "'=1+1" in lines[1] and "'@SUM(A1)" in text and ";=1+1" not in text
    assert "+7 (495) 988-64-94" in text and "'+7 (495)" not in text  # phone numbers are left alone


def test_file_name_pattern():
    name = file_name(RUN, "xlsx", datetime(2026, 10, 26))
    assert name == "suppliers_Рис_для_суши_Москва_2026-10-26.xlsx"
    assert file_name({"category": 'a/b:"c"', "location": None}, "csv", datetime(2026, 1, 2)) == "suppliers_a_b_c_Вся_Россия_2026-01-02.csv"
    assert search_parameters({**RUN, "include_neighbors": True, "supplier_type": "оптовик"}) == "Рис для суши; Москва; с соседними регионами; тип: оптовик"
