"""build_passport и passport_markdown (spec/04 §4.17; REQ-112..REQ-114, К-9)."""
from __future__ import annotations

import json

import pytest

from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, TABLES, TARGET_UNITS, VOCAB
from faktura.passport import build_passport, passport_markdown


@pytest.fixture
def passport(profile):
    return build_passport(profile)


def _table(p, name):
    return next(t for t in p["tables"] if t["name"] == name)






def test_seven_tables_in_order(passport):
    assert [t["name"] for t in passport["tables"]] == list(TABLES)
























# --- Markdown ----------------------------------------------------------------------------------











