"""export_flat, export_relational, preview: выгрузки для людей (spec/04 §4.16, P2)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from decimal import Decimal

import pytest

from faktura.domain.tables import TABLES
from faktura.export import export_flat, export_relational, preview

OPTS = {"with_source": True, "with_quote": False, "normalized": True}
HEAD_BASE = ["Статья", "Год", "Образец", "Параметр", "Значение", "Единица", "Статус"]


def _csv_rows(data: bytes) -> list[list[str]]:
    text = data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline=""), delimiter=";"))


# --- export_flat: CSV --------------------------------------------------------------------------

def test_flat_csv_bom_semicolon_crlf(flat_rows):
    data = export_flat(flat_rows, "csv", OPTS)
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    rows = _csv_rows(data)
    assert rows[0][:7] == HEAD_BASE
    assert len(rows) == 1 + len(flat_rows)




















# --- TSV / JSON / XLSX -------------------------------------------------------------------------









# --- preview -----------------------------------------------------------------------------------











# --- export_relational -------------------------------------------------------------------------

def test_relational_csv_is_zip_with_file_per_table(mini_bundles):
    z = zipfile.ZipFile(io.BytesIO(export_relational(mini_bundles, "csv", OPTS)))
    names = set(z.namelist())
    assert {f"{t}.csv" for t in TABLES} <= names
    rows = list(csv.reader(io.StringIO(z.read("results.csv").decode("utf-8-sig"), newline=""), delimiter=";"))
    assert rows[0] == list(TABLES["results"])
    assert len(rows) == 1 + sum(len(b.results) for b in mini_bundles)


def test_flat_csv_formula_injection_is_not_executed(flat_rows):
    flat_rows[0]["paper_title"] = "=HYPERLINK(\"http://x\")"
    cell = _csv_rows(export_flat(flat_rows, "csv", OPTS))[1][0]
    assert not cell.startswith("=")
