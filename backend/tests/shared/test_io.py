"""domain.io: разбор и запись ячеек, roundtrip эталона."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.io import (bundles_from_tables, format_cell, parse_cell, read_tables, tables_from_bundles)
from faktura.domain.tables import TABLES






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








