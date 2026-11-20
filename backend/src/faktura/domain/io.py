"""Общее чтение и запись ячеек 7 CSV и преобразование таблиц <-> PaperBundle (план 4.3).

Один код для мока, эталона, оценки и тестов. Запись файлов на диск - в export.write_submission.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .ids import issue_key
from .records import ROW_MODELS, PaperBundle, Publication, RecordMeta
from .tables import (BOOL_FIELDS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS, NUMERIC_FIELDS, TABLES)

_TRUE = {"true", "1", "yes", "да"}
_FALSE = {"false", "0", "no", "нет"}


def parse_cell(table: str, column: str, text: str | None) -> Decimal | int | bool | list[str] | str | None:
    """'' -> None; число -> Decimal (целые поля -> int); bool -> bool (unencapsulated -> False, К-7);
    список -> list[str]. Нераспознанное число/bool возвращается строкой как есть."""
    if text is None:
        return None
    s = text.strip()
    key = (table, column)
    if key in LIST_FIELDS:
        if not s:
            return []
        return [p.strip() for p in s.split(LIST_FIELDS[key].strip()) if p.strip()]
    if s == "":
        return None
    if key in BOOL_FIELDS:
        low = s.lower()
        if low in _TRUE or low == "encapsulated":
            return True
        if low in _FALSE or low == "unencapsulated":
            return False
        return s
    if key in NUMERIC_FIELDS:
        try:
            d = Decimal(s)
        except InvalidOperation:
            return s
        if not d.is_finite():
            return s
        if key in INTEGER_FIELDS and d == d.to_integral_value():
            return int(d)
        return d
    return text


def format_cell(table: str, column: str, value: Any) -> str:
    """Значение -> текст ячейки: Decimal без экспоненты и лишних нулей, true/false, списки через разделитель."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s
    if isinstance(value, (list, tuple)):
        return LIST_FIELDS.get((table, column), ";").join(str(v) for v in value)
    return str(value)


def read_tables(directory: Path) -> dict[str, list[dict[str, str]]]:
    """Читает 7 CSV (utf-8-sig); порядок строк сохраняется. Нет файла - пустой список."""
    out: dict[str, list[dict[str, str]]] = {}
    for t in TABLES:
        p = Path(directory) / f"{t}.csv"
        if not p.exists():
            out[t] = []
            continue
        with open(p, encoding="utf-8-sig", newline="") as f:
            out[t] = [dict(r) for r in csv.DictReader(f)]
    return out


def _row_to_record(table: str, row: dict[str, str], order: int):
    values: dict[str, Any] = {}
    notes: list[str] = []
    for col in TABLES[table]:
        v = parse_cell(table, col, row.get(col))
        key = (table, col)
        if isinstance(v, str) and (key in NUMERIC_FIELDS or key in BOOL_FIELDS):
            notes.append(f"{col}: не разобрано «{v}»")  # текст сохраняется как есть, чистит его sanitize
        values[col] = v
    extra = {k: v for k, v in row.items() if k not in TABLES[table] and k is not None}
    meta = RecordMeta(ml_order=order, notes_backend=notes, extra=extra)
    if table == "issues":
        rec = ROW_MODELS[table](**{k: (v if v is not None else _default(table, k)) for k, v in values.items()},
                                meta=meta)
        rec.key = issue_key(rec.check_name, rec.record_ids, {"description": rec.description or ""})
        return rec
    return ROW_MODELS[table](**{k: (v if v is not None else _default(table, k)) for k, v in values.items()},
                             meta=meta)


def _default(table: str, col: str):
    if (table, col) in LIST_FIELDS:
        return []
    if col in ("publication_id", "check_name") or col == ID_COLUMN[table]:
        return ""
    return None


def bundles_from_tables(tables: dict[str, list[dict[str, str]]]) -> list[PaperBundle]:
    """Группирует строки по publication_id. Порядок статей - как в publications, затем по первому появлению.
    ml_order = порядок строки в файле внутри статьи и таблицы."""
    pids: list[str] = []
    for t in TABLES:
        for r in tables.get(t, []):
            pid = (r.get("publication_id") or "").strip()
            if pid and pid not in pids:
                pids.append(pid)
    bundles: dict[str, PaperBundle] = {}
    counters: dict[tuple[str, str], int] = {}
    for pid in pids:
        bundles[pid] = PaperBundle(publication=Publication(publication_id=pid))
    seen_pub: set[str] = set()
    for t in TABLES:
        for row in tables.get(t, []):
            pid = (row.get("publication_id") or "").strip()
            if not pid:
                continue
            n = counters.get((pid, t), 0)
            counters[(pid, t)] = n + 1
            rec = _row_to_record(t, row, n)
            b = bundles[pid]
            if t == "publications":
                if pid in seen_pub:
                    continue
                seen_pub.add(pid)
                b.publication = rec
            else:
                getattr(b, t).append(rec)
    return [bundles[p] for p in pids]


def tables_from_bundles(bundles: list[PaperBundle]) -> dict[str, list[dict[str, str]]]:
    """Строки для записи CSV. Порядок: publication_id, затем ml_order (сортировка устойчивая)."""
    out: dict[str, list[dict[str, str]]] = {t: [] for t in TABLES}
    for b in sorted(bundles, key=lambda x: x.publication.publication_id):
        for t, rows in b.iter_tables():
            for rec in sorted(rows, key=lambda r: r.meta.ml_order):
                out[t].append({c: format_cell(t, c, getattr(rec, c, None)) for c in TABLES[t]})
    return out
