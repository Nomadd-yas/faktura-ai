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


def test_flat_csv_decimal_comma_and_text_with_comma_unmangled(flat_rows):
    rows = _csv_rows(export_flat(flat_rows, "csv", OPTS))
    assert rows[1][4] == "1,08"
    assert rows[2][4] == "18,6"
    assert rows[1][0] == "Perovskite, with comma"


def test_flat_csv_with_source_columns(flat_rows):
    rows = _csv_rows(export_flat(flat_rows, "csv", OPTS))
    assert rows[0][7:9] == ["Страница", "Источник"]
    assert rows[1][7:9] == ["4", "Табл. 2, строка A"]


def test_flat_csv_without_source(flat_rows):
    rows = _csv_rows(export_flat(flat_rows, "csv", {**OPTS, "with_source": False}))
    assert rows[0] == HEAD_BASE


def test_flat_csv_with_quote_column(flat_rows):
    rows = _csv_rows(export_flat(flat_rows, "csv", {**OPTS, "with_quote": True}))
    assert rows[0][-1] == "Цитата"
    assert rows[1][-1] == "open-circuit voltage of 1.08 V"
    assert rows[2][-1] == ""  # цитаты нет - пусто, не «None»


def test_flat_csv_no_none_literals(flat_rows):
    assert "None" not in export_flat(flat_rows, "csv", {**OPTS, "with_quote": True}).decode("utf-8")


def test_flat_csv_status_labels_are_russian(flat_rows):
    rows = _csv_rows(export_flat(flat_rows, "csv", OPTS))
    assert rows[1][6] == "принят" and rows[2][6] == "на проверке"


def test_flat_csv_empty_rows_header_only():
    rows = _csv_rows(export_flat([], "csv", OPTS))
    assert len(rows) == 1 and rows[0][:7] == HEAD_BASE


def test_flat_csv_value_none_is_empty_cell(flat_rows):
    flat_rows[0]["value"] = None
    assert _csv_rows(export_flat(flat_rows, "csv", OPTS))[1][4] == ""


def test_flat_csv_formula_injection_is_not_executed(flat_rows):
    flat_rows[0]["paper_title"] = "=HYPERLINK(\"http://x\")"
    cell = _csv_rows(export_flat(flat_rows, "csv", OPTS))[1][0]
    assert not cell.startswith("=")


# --- TSV / JSON / XLSX -------------------------------------------------------------------------

def test_flat_tsv_uses_tabs(flat_rows):
    text = export_flat(flat_rows, "tsv", OPTS).decode("utf-8-sig")
    first = text.splitlines()[0].split("\t")
    assert first[:7] == HEAD_BASE


def test_flat_json_grouped_by_paper(flat_rows):
    data = json.loads(export_flat(flat_rows, "json", OPTS))
    assert [p["paper"]["id"] for p in data] == ["pap_a", "pap_b"]
    assert len(data[0]["facts"]) == 2 and len(data[1]["facts"]) == 1
    f = data[0]["facts"][0]
    assert f["sample"] == "A" and f["parameter"] == "Voc" and f["value"] == 1.08 and f["unit"] == "V"
    assert f["source"]["page"] == 4
    assert data[0]["paper"]["year"] == 2024


def test_flat_xlsx_opens_with_openpyxl_numbers_are_numbers(flat_rows):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(export_flat(flat_rows, "xlsx", OPTS)))
    assert wb.sheetnames == ["Факты"]
    ws = wb["Факты"]
    assert [c.value for c in ws[1]][:7] == HEAD_BASE
    assert ws.max_row == 1 + len(flat_rows)
    assert isinstance(ws.cell(2, 5).value, (int, float)) and abs(ws.cell(2, 5).value - 1.08) < 1e-9
    assert ws.cell(2, 7).value == "принят"


def test_flat_unknown_format_raises(flat_rows):
    with pytest.raises(ValueError):
        export_flat(flat_rows, "docx", OPTS)


# --- preview -----------------------------------------------------------------------------------

def test_preview_shape(flat_rows):
    p = preview(flat_rows, OPTS)
    assert p["head"][:7] == HEAD_BASE and p["head"][7:] == ["Страница", "Источник"]
    assert len(p["rows"]) == 3 and p["facts"] == 3 and p["papers"] == 2
    assert p["rows"][0][4] == "1,08" and p["rows"][0][1] == "2024"
    assert all(isinstance(c, str) for r in p["rows"] for c in r)


def test_preview_limited_to_50_rows_but_counts_all(flat_rows):
    rows = [dict(flat_rows[0], sample=str(i)) for i in range(120)]
    p = preview(rows, OPTS)
    assert len(p["rows"]) == 50 and p["facts"] == 120 and p["papers"] == 1


def test_preview_empty():
    p = preview([], OPTS)
    assert p["rows"] == [] and p["facts"] == 0 and p["papers"] == 0 and p["head"]


def test_preview_with_quote_adds_column(flat_rows):
    p = preview(flat_rows, {**OPTS, "with_quote": True})
    assert p["head"][-1] == "Цитата" and len(p["rows"][0]) == len(p["head"])


def test_preview_matches_csv_head(flat_rows):
    opts = {**OPTS, "with_quote": True}
    assert preview(flat_rows, opts)["head"] == _csv_rows(export_flat(flat_rows, "csv", opts))[0]


# --- export_relational -------------------------------------------------------------------------

def test_relational_csv_is_zip_with_file_per_table(mini_bundles):
    z = zipfile.ZipFile(io.BytesIO(export_relational(mini_bundles, "csv", OPTS)))
    names = set(z.namelist())
    assert {f"{t}.csv" for t in TABLES} <= names
    rows = list(csv.reader(io.StringIO(z.read("results.csv").decode("utf-8-sig"), newline=""), delimiter=";"))
    assert rows[0] == list(TABLES["results"])
    assert len(rows) == 1 + sum(len(b.results) for b in mini_bundles)


def test_relational_csv_uses_decimal_comma(mini_bundles):
    z = zipfile.ZipFile(io.BytesIO(export_relational(mini_bundles, "csv", OPTS)))
    text = z.read("results.csv").decode("utf-8-sig")
    assert "15,28" in text


def test_relational_json_structure(mini_bundles):
    d = json.loads(export_relational(mini_bundles, "json", OPTS))
    assert d["format"] == "faktura-dataset" and d["version"] == "1.0" and d["collection"] == "solar"
    for t in TABLES:
        assert isinstance(d[t], list)
    assert len(d["publications"]) == 2
    assert len(d["results"]) == sum(len(b.results) for b in mini_bundles)
    assert isinstance(d["publications"][0]["authors"], list)
    assert d["results"][0]["metric_value"] == 15.28  # числа числами


def test_relational_xlsx_sheet_per_table(mini_bundles):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(io.BytesIO(export_relational(mini_bundles, "xlsx", OPTS)))
    assert set(TABLES) <= set(wb.sheetnames)
    ws = wb["results"]
    assert [c.value for c in ws[1]] == list(TABLES["results"])
    assert ws.max_row == 1 + sum(len(b.results) for b in mini_bundles)


def test_relational_only_accepted_option_drops_new(make_bundle):
    from faktura.domain.records import ReviewStatus

    b = make_bundle()
    b.results[0].meta.review_status = ReviewStatus.accepted
    d = json.loads(export_relational([b], "json", {**OPTS, "only_accepted": True}))
    assert [r["result_id"] for r in d["results"]] == ["R001"]


def test_relational_empty_bundles_json(make_bundle):
    d = json.loads(export_relational([], "json", OPTS))
    assert d["results"] == [] and d["publications"] == []


def test_relational_unknown_format_raises(mini_bundles):
    with pytest.raises(ValueError):
        export_relational(mini_bundles, "pdf", OPTS)
