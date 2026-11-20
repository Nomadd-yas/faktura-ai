"""Выгрузки для людей: CSV с ';' и десятичной запятой, TSV, XLSX, JSON (P2, spec/04 §4.16; REQ-015, REQ-113).

Это отдельный от сдачи диалект: UTF-8 с BOM, `;`, CRLF, числа с запятой (открывается русским Excel).
Текстовые ячейки, начинающиеся с `= + - @`, экранируются апострофом (защита от формул в Excel);
в XLSX такие ячейки пишутся строкой.

Плоские строки (`rows` у `export_flat`/`preview`) - словари с ключами: `paper_id, paper_title, year, sample,
parameter, raw, value, unit, status, page, source, quote` (+ необязательные `authors, journal, collection,
confidence, edited, raw_unit, where, issues`). Их собирает API из таблицы фактов.

`options` (ExportOptions): `with_source` (по умолчанию true), `with_quote` (false), `normalized` (true),
`only_accepted` (false), `include_out_of_scope` (true для relational), `collection` ("solar").
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from ..domain.records import PaperBundle, ReviewStatus
from ..domain.tables import BOOL_FIELDS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS, NUMERIC_FIELDS, TABLES

__all__ = ["export_flat", "export_relational", "preview", "flat_head", "FLAT_FORMATS", "RELATIONAL_FORMATS",
           "STATUS_LABELS", "PREVIEW_LIMIT", "content_type", "file_extension"]

FLAT_FORMATS = ("csv", "tsv", "xlsx", "json")
RELATIONAL_FORMATS = ("csv", "xlsx", "json")
PREVIEW_LIMIT = 50
HEAD_BASE = ["Статья", "Год", "Образец", "Параметр", "Значение", "Единица", "Статус"]
STATUS_LABELS = {"accepted": "принят", "new": "на проверке", "rejected": "отклонён"}
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

_CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8",
    "json": "application/json", "zip": "application/zip",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def content_type(fmt: str, layout: str = "flat") -> str:
    """MIME-тип файла выгрузки (для `Content-Type` в API)."""
    if layout == "relational" and fmt == "csv":
        return _CONTENT_TYPES["zip"]
    return _CONTENT_TYPES[fmt]


def file_extension(fmt: str, layout: str = "flat") -> str:
    return "zip" if layout == "relational" and fmt == "csv" else fmt


def _opt(options: dict | None, key: str, default: Any) -> Any:
    if not options:
        return default
    v = options.get(key, default)
    return default if v is None else v


def _to_decimal(v: Any) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, (int, float)):
        try:
            d = Decimal(repr(v)) if isinstance(v, float) else Decimal(v)
        except InvalidOperation:
            return None
        return d if d.is_finite() else None
    return None


def _num_text(d: Decimal, comma: bool = True) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    return s.replace(".", ",") if comma else s


def _num_json(d: Decimal | None) -> int | float | None:
    if d is None:
        return None
    return int(d) if d == d.to_integral_value() and abs(d) < 10 ** 15 else float(d)


def _safe_text(s: str) -> str:
    """Защита от формул в табличных редакторах."""
    return "'" + s if s and s.startswith(_FORMULA_START) else s


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "да" if v else "нет"
    if isinstance(v, (list, tuple)):
        return "; ".join(str(x) for x in v)
    d = _to_decimal(v)
    if d is not None:
        return _num_text(d)
    return _safe_text(str(v))


def flat_head(options: dict | None) -> list[str]:
    head = list(HEAD_BASE)
    if _opt(options, "with_source", True):
        head += ["Страница", "Источник"]
    if _opt(options, "with_quote", False):
        head.append("Цитата")
    return head


def _status(v: Any) -> str:
    key = getattr(v, "value", v)
    return STATUS_LABELS.get(str(key), "" if key is None else str(key))


def _flat_value(row: dict, options: dict | None) -> tuple[Any, Any]:
    """(значение, единица) с учётом `normalized`: как напечатано - исходный текст и исходная единица."""
    if _opt(options, "normalized", True):
        return row.get("value"), row.get("unit")
    return row.get("raw"), row.get("raw_unit")


def _flat_cells(row: dict, options: dict | None) -> list[Any]:
    """Типизированные ячейки плоской строки (Decimal/int/str/None) в порядке flat_head."""
    value, unit = _flat_value(row, options)
    cells: list[Any] = [row.get("paper_title"), row.get("year"), row.get("sample"), row.get("parameter"), value,
                        unit, _status(row.get("status"))]
    if _opt(options, "with_source", True):
        cells += [row.get("page"), row.get("source")]
    if _opt(options, "with_quote", False):
        cells.append(row.get("quote"))
    return cells


def _text_cell(v: Any) -> str:
    if isinstance(v, int) and not isinstance(v, bool):
        return str(v)
    return _text(v)


def _dsv(head: list[str], rows: Iterable[list[Any]], delimiter: str, bom: bool) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, delimiter=delimiter, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(head)
    for r in rows:
        w.writerow([_text_cell(c) for c in r])
    data = buf.getvalue()
    return data.encode("utf-8-sig" if bom else "utf-8")


def _xlsx_sheet(ws: Any, head: list[str], rows: Iterable[list[Any]]) -> None:
    ws.append(head)
    for r in rows:
        out: list[Any] = []
        for c in r:
            if c is None:
                out.append(None)
            elif isinstance(c, bool):
                out.append("да" if c else "нет")
            elif isinstance(c, int):
                out.append(c)
            elif (d := _to_decimal(c)) is not None:
                out.append(_num_json(d))
            elif isinstance(c, (list, tuple)):
                out.append("; ".join(str(x) for x in c))
            else:
                out.append(str(c))
        ws.append(out)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            if isinstance(cell.value, str) and cell.value.startswith(_FORMULA_START):
                cell.data_type = "s"  # текст, а не формула


def _xlsx_bytes(sheets: list[tuple[str, list[str], list[list[Any]]]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for title, head, rows in sheets:
        ws = wb.create_sheet(title=title[:31])
        _xlsx_sheet(ws, head, rows)
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _year_cell(v: Any) -> Any:
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return v


def _flat_json(rows: list[dict], options: dict | None) -> bytes:
    papers: dict[str, dict[str, Any]] = {}
    for r in rows:
        pid = str(r.get("paper_id") or "")
        if pid not in papers:
            year = _year_cell(r.get("year"))
            papers[pid] = {"paper": {"id": pid, "title": r.get("paper_title"), "authors": r.get("authors"),
                                     "year": year if isinstance(year, int) else _num_json(_to_decimal(year)),
                                     "journal": r.get("journal"), "collection": r.get("collection")},
                           "facts": [], "issues": []}
        value, unit = _flat_value(r, options)
        d = _to_decimal(value)
        fact: dict[str, Any] = {"sample": r.get("sample"), "parameter": r.get("parameter"), "as_printed": r.get("raw"),
                                "value": _num_json(d) if d is not None else value, "unit": unit,
                                "status": str(getattr(r.get("status"), "value", r.get("status")) or "") or None,
                                "edited": bool(r.get("edited", False)), "confidence": r.get("confidence")}
        if _opt(options, "with_source", True):
            page = _to_decimal(r.get("page"))
            fact["source"] = {"page": _num_json(page) if page is not None else r.get("page"), "ref": r.get("source"),
                              "where": r.get("where")}
        if _opt(options, "with_quote", False):
            fact["quote"] = r.get("quote")
        papers[pid]["facts"].append(fact)
        for iss in r.get("issues") or []:
            if iss not in papers[pid]["issues"]:
                papers[pid]["issues"].append(iss)
    return json.dumps(list(papers.values()), ensure_ascii=False, indent=2, default=str).encode("utf-8")


def export_flat(rows: list[dict], fmt: str, options: dict) -> bytes:
    """Плоская таблица фактов: csv (`;`, запятая, BOM, CRLF) | tsv | xlsx (лист «Факты») | json (по статьям)."""
    if fmt not in FLAT_FORMATS:
        raise ValueError(f"неизвестный формат выгрузки: {fmt}")
    rows = list(rows or [])
    if fmt == "json":
        return _flat_json(rows, options)
    head = flat_head(options)
    cells = [_flat_cells(r, options) for r in rows]
    if fmt == "csv":
        return _dsv(head, cells, ";", bom=True)
    if fmt == "tsv":
        return _dsv(head, cells, "\t", bom=False)
    return _xlsx_bytes([("Факты", head, [[_year_cell(c) if i == 1 else c for i, c in enumerate(r)]
                                         for r in cells])])


def preview(rows: list[dict], options: dict) -> dict[str, Any]:
    """Живой предпросмотр (POST /exports/preview): первые 50 строк строками с запятой, счётчики по всем."""
    rows = list(rows or [])
    head = flat_head(options)
    out = [[_text_cell(c) for c in _flat_cells(r, options)] for r in rows[:PREVIEW_LIMIT]]
    papers = {str(r.get("paper_id")) for r in rows}
    return {"head": head, "rows": out, "facts": len(rows), "papers": len(papers)}


# --- relational ----------------------------------------------------------------------------------

def _keep_paper(b: PaperBundle, options: dict | None) -> bool:
    if _opt(options, "include_out_of_scope", True):
        return True
    return b.publication.in_scope is not False


def _relational_rows(bundles: list[PaperBundle], options: dict | None) -> dict[str, list[tuple[Any, ...]]]:
    """Типизированные строки 7 таблиц (значения моделей), без отклонённых результатов; при `only_accepted` -
    только принятые результаты."""
    only_accepted = bool(_opt(options, "only_accepted", False))
    out: dict[str, list[tuple[Any, ...]]] = {t: [] for t in TABLES}
    for b in sorted(bundles, key=lambda x: x.publication.publication_id):
        if not _keep_paper(b, options):
            continue
        dropped: set[str] = set()
        for t, recs in b.iter_tables():
            for rec in sorted(recs, key=lambda r: r.meta.ml_order):
                if t == "results":
                    st = rec.meta.review_status
                    if st == ReviewStatus.rejected or (only_accepted and st != ReviewStatus.accepted):
                        dropped.add(rec.result_id)
                        continue
                    if rec.meta.corrected_value is not None:
                        rec = rec.model_copy(update={"normalized_value": rec.meta.corrected_value,
                                                     "metric_value": rec.meta.corrected_value})
                if t == "issues" and getattr(rec, "status", None) == "ok":
                    continue
                vals = []
                for c in TABLES[t]:
                    v = getattr(rec, c, None)
                    if t == "issues" and c == "record_ids":
                        v = [x for x in v if x not in dropped]
                    vals.append(v)
                out[t].append(tuple(vals))
    return out


def _rel_csv_cell(table: str, col: str, v: Any) -> str:
    if v is None:
        return ""
    key = (table, col)
    if key in BOOL_FIELDS and isinstance(v, bool):
        return "true" if v else "false"
    if key in LIST_FIELDS:
        return LIST_FIELDS[key].join(str(x) for x in (v or []))
    if key in NUMERIC_FIELDS:
        d = _to_decimal(v)
        return _num_text(d) if d is not None else _safe_text(str(v))
    if col == "publication_id" or col == ID_COLUMN.get(table):
        return str(v)
    return _safe_text(str(v))


def _rel_json_value(table: str, col: str, v: Any) -> Any:
    key = (table, col)
    if key in LIST_FIELDS:
        return list(v or [])
    if key in NUMERIC_FIELDS:
        d = _to_decimal(v)
        if d is not None:
            return int(d) if key in INTEGER_FIELDS and d == d.to_integral_value() else _num_json(d)
        return v
    if isinstance(v, Decimal):
        return _num_json(v)
    return v


def export_relational(bundles: list[PaperBundle], fmt: str, options: dict) -> bytes:
    """Связанные таблицы ТЗ: csv - ZIP из CSV по таблице (`;`, запятая); xlsx - лист на таблицу;
    json - объект `faktura-dataset` с массивами (spec/04 §4.16)."""
    if fmt not in RELATIONAL_FORMATS:
        raise ValueError(f"неизвестный формат выгрузки: {fmt}")
    data = _relational_rows(list(bundles or []), options)
    if fmt == "json":
        collection = _opt(options, "collection", "solar")
        doc: dict[str, Any] = {
            "format": "faktura-dataset", "version": "1.0", "collection": collection,
            "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "passport_url": f"/api/v1/passport?collection={collection}",
        }
        for t, cols in TABLES.items():
            doc[t] = [{c: _rel_json_value(t, c, v) for c, v in zip(cols, row)} for row in data[t]]
        doc.setdefault("claims", [])
        doc.setdefault("references", [])
        return json.dumps(doc, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    if fmt == "csv":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for t, cols in TABLES.items():
                s = io.StringIO(newline="")
                w = csv.writer(s, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
                w.writerow(cols)
                for row in data[t]:
                    w.writerow([_rel_csv_cell(t, c, v) for c, v in zip(cols, row)])
                z.writestr(f"{t}.csv", s.getvalue().encode("utf-8-sig"))
        return buf.getvalue()
    sheets = []
    for t, cols in TABLES.items():
        rows = []
        for row in data[t]:
            cells = []
            for c, v in zip(cols, row):
                key = (t, c)
                if key in LIST_FIELDS:
                    cells.append(LIST_FIELDS[key].join(str(x) for x in (v or [])) or None)
                elif key in BOOL_FIELDS and isinstance(v, bool):
                    cells.append("true" if v else "false")
                elif key in NUMERIC_FIELDS:
                    d = _to_decimal(v)
                    cells.append((int(d) if key in INTEGER_FIELDS and d == d.to_integral_value() else d)
                                 if d is not None else v)
                else:
                    cells.append(v)
            rows.append(cells)
        sheets.append((t, list(cols), rows))
    return _xlsx_bytes(sheets)
