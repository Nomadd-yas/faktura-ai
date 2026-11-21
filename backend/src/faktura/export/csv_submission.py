"""Запись 7 CSV сдачи (план 5.4; REQ-002, REQ-012, REQ-013, REQ-015, REQ-022).

Диалект сдачи: UTF-8 с BOM (как файлы шаблона; скрипт читает `utf-8-sig`), разделитель `,`, десятичная точка,
`csv.QUOTE_MINIMAL`, перевод строки `\\r\\n` (стандарт модуля csv). Значения ячеек готовит общий
`faktura.domain.io.tables_from_bundles` (`format_cell`). Это отдельный писатель: CSV «для людей» (`;`,
десятичная запятая) пишет `export.human` (REQ-015).
"""
from __future__ import annotations

import csv
import io
import os
from pathlib import Path

from ..domain.io import tables_from_bundles
from ..domain.records import PaperBundle
from ..domain.tables import TABLES

__all__ = ["write_submission", "write_tables", "table_bytes", "SUBMISSION_FILES"]

SUBMISSION_FILES: tuple[str, ...] = tuple(f"{t}.csv" for t in TABLES)


def table_bytes(table: str, rows: list[dict[str, str]], columns: tuple[str, ...] | None = None) -> bytes:
    """Байты одного CSV сдачи: BOM + заголовок 1:1 с TABLES (или `columns` другой области) + строки;
    лишние ключи строк отбрасываются."""
    cols = columns or TABLES[table]
    buf = io.StringIO(newline="")
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    w.writerow(cols)
    for r in rows:
        w.writerow(["" if r.get(c) is None else str(r.get(c)) for c in cols])
    return buf.getvalue().encode("utf-8-sig")


def write_tables(tables: dict[str, list[dict[str, str]]], out_dir: Path) -> list[Path]:
    """Пишет ровно 7 файлов (таблица без строк - только заголовок). Запись атомарная по файлу."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for t in TABLES:
        p = out / f"{t}.csv"
        tmp = out / f".{t}.csv.tmp"
        tmp.write_bytes(table_bytes(t, tables.get(t, [])))
        os.replace(tmp, p)
        paths.append(p)
    return paths


def write_submission(bundles: list[PaperBundle], out_dir: Path) -> list[Path]:
    """Через domain.io.tables_from_bundles; utf-8-sig, csv.writer (\\r\\n), QUOTE_MINIMAL; ровно 7 файлов.
    Порядок строк: publication_id, затем meta.ml_order. Возвращает пути в порядке TABLES."""
    return write_tables(tables_from_bundles(list(bundles)), Path(out_dir))
