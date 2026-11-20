"""domain.io: разбор и запись ячеек, roundtrip эталона."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.io import (bundles_from_tables, format_cell, parse_cell, read_tables, tables_from_bundles)
from faktura.domain.tables import TABLES


def test_parse_cell_basic():
    assert parse_cell("results", "metric_value", "") is None
    assert parse_cell("results", "metric_value", "71.0") == Decimal("71.0")
    assert parse_cell("results", "source_page", "5") == 5
    assert parse_cell("publications", "in_scope", "true") is True
    assert parse_cell("devices", "encapsulation", "unencapsulated") is False  # К-7
    assert parse_cell("devices", "encapsulation", "encapsulated") is True
    assert parse_cell("issues", "record_ids", "R004;R001") == ["R004", "R001"]
    assert parse_cell("issues", "record_ids", "") == []
    assert parse_cell("publications", "authors", "A B.; C D.") == ["A B.", "C D."]
    assert parse_cell("results", "metric_value", "abc") == "abc"  # нечисло возвращается как есть
    assert parse_cell("results", "metric_name", "PCE") == "PCE"


def test_format_cell_basic():
    assert format_cell("results", "metric_value", Decimal("71.0")) == "71"
    assert format_cell("results", "metric_value", Decimal("1E+2")) == "100"
    assert format_cell("results", "metric_value", Decimal("0.000050")) == "0.00005"
    assert format_cell("results", "source_page", 5) == "5"
    assert format_cell("publications", "in_scope", False) == "false"
    assert format_cell("issues", "record_ids", ["R1", "R2"]) == "R1;R2"
    assert format_cell("publications", "authors", ["A", "B"]) == "A; B"
    assert format_cell("results", "note", None) == ""


def _cells(rows, table):
    return [{c: parse_cell(table, c, r.get(c, "")) for c in TABLES[table]} for r in rows]


def _roundtrip(tables):
    bundles = bundles_from_tables(tables)
    out = tables_from_bundles(bundles)
    assert list(out) == list(TABLES)
    for t in TABLES:
        assert len(out[t]) == len(tables[t]), t
        assert _cells(out[t], t) == _cells(tables[t], t), t
    return bundles


def test_mini_gold_roundtrip(mini_gold_dir):
    bundles = _roundtrip(read_tables(mini_gold_dir))
    assert [b.publication_id for b in bundles] == ["D001", "D003"]
    assert any(b.results for b in bundles)


@pytest.mark.data
def test_dev_gold_roundtrip(dev_gold_dir):
    bundles = _roundtrip(read_tables(dev_gold_dir))
    assert len(bundles) == 21
    assert sum(len(b.results) for b in bundles) == 659
    assert sum(1 for b in bundles for r in b.results if r.spread_value is not None) == 49
    assert sum(len(b.issues) for b in bundles) == 53


def test_ml_order_follows_file_order(mini_gold_dir):
    for b in bundles_from_tables(read_tables(mini_gold_dir)):
        orders = [r.meta.ml_order for r in b.results]
        assert orders == list(range(len(orders)))


def test_effective_value(mini_gold_dir):
    b = bundles_from_tables(read_tables(mini_gold_dir))[0]
    r = next(x for x in b.results if x.normalized_value is not None)
    assert b.effective_value(r) == r.normalized_value
    r.meta.corrected_value = Decimal("1.5")
    assert b.effective_value(r) == Decimal("1.5")


def test_failed_stub():
    from faktura.domain.records import PaperBundle

    b = PaperBundle.failed_stub("X1")
    out = tables_from_bundles([b])
    assert out["publications"] == [{**{c: "" for c in TABLES["publications"]}, "publication_id": "X1"}]
    assert all(out[t] == [] for t in TABLES if t != "publications")
