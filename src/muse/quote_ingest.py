"""Stdlib-only extraction of supplier quotes from CSV, XLSX and text-PDF (WS7).

Output feeds ``muse.quote_intelligence.audit_quotes`` unchanged. Extraction never
guesses commercial facts: supplier, currency, tax status and whether the total is
complete come from the operator. Every line item keeps a source anchor
(``file`` + ``locator`` such as ``Sheet1!A4`` or ``page 2 line 7`` + excerpt).
Each extraction reports ``confidence`` and ``ocr_needed``; PDF text is always
``low`` confidence and requires human confirmation.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
import zlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_XLSX_MEMBER_BYTES = 40 * 1024 * 1024
MAX_ROWS = 200
HEADER_ALIASES = {
    "item_key": {"item_key", "item", "sku", "code", "编号", "项目编号", "料号"},
    "description": {"description", "desc", "name", "item name", "品名", "描述", "名称", "项目"},
    "quantity": {"quantity", "qty", "数量"},
    "unit": {"unit", "uom", "单位"},
    "unit_price": {"unit_price", "unit price", "price", "单价"},
}
_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


class QuoteExtractionError(ValueError):
    """The document cannot be converted into reviewable quote line items."""


def _normalize_header(value: str) -> str | None:
    key = re.sub(r"\s+", " ", str(value or "").strip().casefold())
    for field, aliases in HEADER_ALIASES.items():
        if key in aliases:
            return field
    return None


def _decimal_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("，", "")
    text = re.sub(r"^[^\d\-.]+", "", text)  # drop leading currency symbols (¥, $, HK$)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return format(number.normalize(), "f") if number.is_finite() else None


def _slug(value: str, index: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._:-]+", "-", value).strip("-")[:60]
    return slug or f"item-{index + 1}"


def _rows_to_items(rows: list[tuple[str, list[str]]], file_name: str) -> tuple[list[dict], list[str]]:
    """rows: (locator, cells). First row with >=3 recognised headers is the header row."""
    header_index, mapping = None, {}
    for index, (_locator, cells) in enumerate(rows[:20]):
        candidate = {}
        for column, cell in enumerate(cells):
            field = _normalize_header(cell)
            if field and field not in candidate.values():
                candidate[column] = field
        if {"description", "quantity", "unit_price"} <= set(candidate.values()):
            header_index, mapping = index, candidate
            break
    if header_index is None:
        raise QuoteExtractionError("no header row with description/quantity/unit_price columns was found")
    items, warnings, seen = [], [], set()
    for locator, cells in rows[header_index + 1:]:
        record = {field: (cells[column].strip() if column < len(cells) else "") for column, field in mapping.items()}
        if not any(record.values()):
            continue
        quantity, price = _decimal_text(record.get("quantity")), _decimal_text(record.get("unit_price"))
        if not record.get("description") or quantity is None or price is None:
            warnings.append(f"{locator}: skipped row without description/quantity/unit_price")
            continue
        key = _slug(record.get("item_key") or record["description"], len(items))
        if key in seen:
            key = f"{key}-{len(items) + 1}"
        seen.add(key)
        items.append({
            "item_key": key, "description": record["description"][:500], "quantity": quantity,
            "unit": (record.get("unit") or "unit")[:40], "unit_price": price,
            "source": {"file": file_name[:256], "locator": locator[:256],
                       "excerpt": " | ".join(cell.strip() for cell in cells if cell.strip())[:1200] or locator},
        })
        if len(items) > MAX_ROWS:
            raise QuoteExtractionError(f"more than {MAX_ROWS} line items")
    if not items:
        raise QuoteExtractionError("no complete line items were found under the header row")
    return items, warnings


def extract_csv(data: bytes, file_name: str) -> dict[str, Any]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    rows = [(f"row {number}", row) for number, row in enumerate(csv.reader(io.StringIO(text)), start=1)]
    items, warnings = _rows_to_items(rows, file_name)
    return {"format": "csv", "line_items": items, "warnings": warnings, "confidence": "high", "ocr_needed": False}


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference or "")
    if not letters:
        raise QuoteExtractionError("cell reference is malformed")
    index = 0
    for char in letters.group(0):
        index = index * 26 + (ord(char) - 64)
    return index - 1


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XLSX_MEMBER_BYTES or (info.compress_size and info.file_size / info.compress_size > 200):
        raise QuoteExtractionError("xlsx member is too large or looks like a compression bomb")
    return archive.read(name)


def extract_xlsx(data: bytes, file_name: str) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise QuoteExtractionError("file is not a valid .xlsx (zip) document") from exc
    with archive:
        names = set(archive.namelist())
        if len(names) > 2000:
            raise QuoteExtractionError("xlsx contains too many members")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(_read_member(archive, "xl/sharedStrings.xml"))
            for si in root.findall("m:si", _NS):
                shared.append("".join(node.text or "" for node in si.iter(f"{{{_NS['m']}}}t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in names:
            raise QuoteExtractionError("xlsx has no first worksheet")
        root = ET.fromstring(_read_member(archive, sheet_name))
    rows: list[tuple[str, list[str]]] = []
    for row in root.iter(f"{{{_NS['m']}}}row"):
        cells: dict[int, str] = {}
        first_ref = None
        for cell in row.findall("m:c", _NS):
            reference = cell.get("r", "")
            first_ref = first_ref or reference
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(node.text or "" for node in cell.iter(f"{{{_NS['m']}}}t"))
            else:
                node = cell.find("m:v", _NS)
                value = node.text if node is not None and node.text is not None else ""
                if kind == "s":
                    try:
                        value = shared[int(value)]
                    except (ValueError, IndexError) as exc:
                        raise QuoteExtractionError(f"{reference}: shared string index invalid") from exc
            cells[_column_index(reference)] = value
        if cells:
            width = max(cells) + 1
            if width > 64:
                raise QuoteExtractionError("xlsx row is wider than 64 columns")
            rows.append((f"Sheet1!{first_ref}", [cells.get(index, "") for index in range(width)]))
        if len(rows) > MAX_ROWS + 25:
            break
    items, warnings = _rows_to_items(rows, file_name)
    return {"format": "xlsx", "line_items": items, "warnings": warnings, "confidence": "high", "ocr_needed": False}


_PDF_STREAM = re.compile(rb"<<(.{0,2000}?)>>\s*stream\r?\n(.*?)\r?\nendstream", re.S)
_PDF_TEXT_OP = re.compile(rb"\((?:\\.|[^\\)])*\)\s*Tj|\[(?:[^\]]{0,4000})\]\s*TJ|T\*|Td|TD|ET")


def _pdf_unescape(raw: bytes) -> str:
    out, index = bytearray(), 0
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while index < len(raw):
        byte = raw[index]
        if byte == 0x5C and index + 1 < len(raw):
            nxt = raw[index + 1]
            if nxt in escapes:
                out.append(escapes[nxt]); index += 2; continue
            octal = re.match(rb"[0-7]{1,3}", raw[index + 1:index + 4])
            if octal:
                out.append(int(octal.group(0), 8) & 0xFF); index += 1 + len(octal.group(0)); continue
            out.append(nxt); index += 2; continue
        out.append(byte); index += 1
    return out.decode("latin-1")


def extract_pdf_text(data: bytes, file_name: str) -> dict[str, Any]:
    """Best-effort text extraction from uncompressed or Flate streams; scanned PDFs need OCR."""
    if not data.startswith(b"%PDF-"):
        raise QuoteExtractionError("file is not a PDF")
    lines: list[str] = []
    for dictionary, body in _PDF_STREAM.findall(data):
        if b"/Image" in dictionary:
            continue
        if b"/FlateDecode" in dictionary:
            try:
                body = zlib.decompressobj().decompress(body, 8 * 1024 * 1024)
            except zlib.error:
                continue
        elif b"/Filter" in dictionary:
            continue
        current: list[str] = []
        for match in _PDF_TEXT_OP.finditer(body):
            token = match.group(0)
            if token.endswith(b"Tj"):
                current.append(_pdf_unescape(token[1:token.rindex(b")")]))
            elif token.endswith(b"TJ"):
                current.append("".join(_pdf_unescape(part[1:-1]) for part in re.findall(rb"\((?:\\.|[^\\)])*\)", token)))
            elif current:
                lines.append(" ".join(part.strip() for part in current if part.strip()))
                current = []
        if current:
            lines.append(" ".join(part.strip() for part in current if part.strip()))
    lines = [line for line in lines if line.strip()]
    if not lines:
        return {"format": "pdf", "line_items": [], "warnings": ["no extractable text; scanned PDF needs OCR"],
                "confidence": "none", "ocr_needed": True}
    pattern = re.compile(r"^(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+(?P<unit>[A-Za-z\u4e00-\u9fff]{1,10})\s+"
                         r"[^\d\s]{0,4}(?P<price>\d[\d,]*(?:\.\d+)?)$")
    rows = [("line 0", ["description", "quantity", "unit", "unit_price"])]
    for number, line in enumerate(lines, start=1):
        match = pattern.match(line.strip())
        if match:
            rows.append((f"text line {number}", [match["desc"], match["qty"], match["unit"], match["price"]]))
    try:
        items, warnings = _rows_to_items(rows, file_name)
    except QuoteExtractionError:
        return {"format": "pdf", "line_items": [], "warnings": ["text found but no 'description qty unit price' rows"],
                "confidence": "none", "ocr_needed": False, "text_lines": lines[:50]}
    return {"format": "pdf", "line_items": items, "warnings": warnings + ["PDF rows are heuristic; confirm every line"],
            "confidence": "low", "ocr_needed": False}


def extract_document(path: str | Path) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.is_file() or candidate.is_symlink():
        raise QuoteExtractionError("quote document must be a regular file")
    if candidate.stat().st_size > MAX_FILE_BYTES:
        raise QuoteExtractionError("quote document exceeds 20 MiB")
    data = candidate.read_bytes()
    suffix = candidate.suffix.casefold()
    extractor = {".csv": extract_csv, ".xlsx": extract_xlsx, ".pdf": extract_pdf_text}.get(suffix)
    if extractor is None:
        raise QuoteExtractionError("supported formats: .csv, .xlsx, .pdf (text layer)")
    return extractor(data, candidate.name)


def build_quote(extraction: dict[str, Any], *, quote_id: str, supplier: str, currency: str,
                tax_status: str = "unknown", total_complete: bool = False) -> dict[str, Any]:
    """Wrap extracted rows with operator-confirmed commercial facts for ``audit_quotes``."""
    if not extraction.get("line_items"):
        raise QuoteExtractionError("extraction has no line items to build a quote from")
    return {"quote_id": quote_id, "supplier": supplier, "currency": currency.upper(), "tax_status": tax_status,
            "total_complete": bool(total_complete), "line_items": extraction["line_items"],
            "extraction": {"format": extraction["format"], "confidence": extraction["confidence"],
                           "ocr_needed": extraction["ocr_needed"], "warnings": extraction["warnings"]}}
