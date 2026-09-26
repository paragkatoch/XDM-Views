"""Serialize joined view rows and per-source native documents."""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Any, Dict, List
from xml.sax.saxutils import escape


EXPORT_FORMATS = ("json", "csv", "xlsx", "native")


def download_stem(view_name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", view_name or "").strip("_")
    return cleaned or "view"


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def rows_to_csv(columns: List[str], rows: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([cell_text(row.get(column)) for column in columns])
    return buffer.getvalue()


def rows_to_json_document(
    view_name: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "view": view_name,
        "columns": columns,
        "rows": rows,
    }


def build_native_document(
    view_name: str,
    sources: List[Dict[str, Any]],
    columns: List[str],
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "format": "native",
        "view": view_name,
        "sources": sources,
        "joined": {
            "shape": "tabular",
            "columns": columns,
            "rows": rows,
        },
    }


def _column_letter(index: int) -> str:
    letters = ""
    number = index + 1
    while number:
        number, remainder = divmod(number - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _xml_text(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch in "\t\n\r" or ord(ch) >= 32)
    return escape(cleaned)


def _worksheet_cell(column_index: int, row_number: int, value: Any) -> str:
    ref = f"{_column_letter(column_index)}{row_number}"
    if value is None:
        return f'<c r="{ref}"/>'
    if isinstance(value, bool):
        text = _xml_text("true" if value else "false")
        return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"><v>{value}</v></c>'
    text = _xml_text(cell_text(value))
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def rows_to_xlsx(columns: List[str], rows: List[Dict[str, Any]]) -> bytes:
    sheet_rows = []
    header = "".join(
        _worksheet_cell(index, 1, column) for index, column in enumerate(columns)
    )
    sheet_rows.append(f'<row r="1">{header}</row>')
    for row_index, row in enumerate(rows, start=2):
        cells = "".join(
            _worksheet_cell(index, row_index, row.get(column))
            for index, column in enumerate(columns)
        )
        sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData>"
        "</worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Results" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buffer.getvalue()


def render_export(
    export_format: str,
    view_name: str,
    columns: List[str],
    rows: List[Dict[str, Any]],
    native: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    stem = download_stem(view_name)
    if export_format == "json":
        return {
            "kind": "json",
            "attachment": True,
            "filename": f"{stem}.json",
            "body": rows_to_json_document(view_name, columns, rows),
        }
    if export_format == "csv":
        return {
            "kind": "file",
            "contentType": "text/csv; charset=utf-8",
            "filename": f"{stem}.csv",
            "body": rows_to_csv(columns, rows).encode("utf-8"),
        }
    if export_format == "xlsx":
        return {
            "kind": "file",
            "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": f"{stem}.xlsx",
            "body": rows_to_xlsx(columns, rows),
        }
    if export_format == "native":
        if native is None:
            raise ValueError("Native export requires a native document.")
        return {
            "kind": "json",
            "attachment": True,
            "filename": f"{stem}.native.json",
            "body": native,
        }
    raise ValueError(f"Unsupported export format: {export_format}")
