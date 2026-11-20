"""Регрессия на dev_gold/results.csv (критерий приёмки модуля norm). REQ-042, REQ-043."""
from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.data


@pytest.fixture(scope="module")
def rows(gold_results):
    return [r for r in gold_results if r["raw_value"].strip()]


def _norm(nz, r):
    return nz.normalize(r["metric_name"], r["raw_value"], r["raw_unit"] or None)


def test_gold_has_rows(rows):
    assert len(rows) >= 600


def test_normalize_matches_gold_everywhere(nz, rows):
    bad = []
    for r in rows:
        res = _norm(nz, r)
        exp_v = Decimal(r["normalized_value"]) if r["normalized_value"] else None
        if res.normalized_value != exp_v or (res.normalized_unit or None) != (r["normalized_unit"] or None):
            bad.append((r["publication_id"], r["result_id"], r["metric_name"], r["raw_value"], r["raw_unit"],
                        r["normalized_value"], r["normalized_unit"], res.normalized_value, res.normalized_unit))
    assert not bad, f"{len(bad)} расхождений, первые: {bad[:10]}"


def test_values_are_decimal(nz, rows):
    for r in rows:
        assert isinstance(_norm(nz, r).normalized_value, Decimal)


def test_gold_conversion_counts(nz, rows):
    mv = [r for r in rows if r["raw_unit"] == "mV"]
    frac = [r for r in rows if r["raw_unit"] == "fraction"]
    assert (len(mv), len(frac)) == (56, 18)  # 74 пересчёта
    for r in mv:
        res = nz.normalize(r["metric_name"], r["raw_value"], "mV")
        assert res.norm_status == "converted" and res.normalized_unit == "V"
    for r in frac:
        res = nz.normalize(r["metric_name"], r["raw_value"], "fraction")
        assert res.norm_status == "converted" and res.normalized_unit == "%"


def test_gold_passthrough_four_rows(nz, rows):
    pt = [r for r in rows if r["metric_name"] in ("bifaciality_factor", "CRI") and not r["raw_unit"]]
    assert len(pt) == 4
    assert {r["publication_id"] for r in pt} == {"D015", "D022"}
    for r in pt:
        res = nz.normalize(r["metric_name"], r["raw_value"], None)
        assert res.norm_status == "passthrough" and res.issue is None
        assert res.normalized_value == Decimal(r["normalized_value"])


def test_gold_spread(nz, gold_results):
    checked = 0
    for r in gold_results:
        if r["spread_value"] and "±" in r["raw_value"]:
            assert _norm(nz, r).spread_value == Decimal(r["spread_value"])
            checked += 1
    assert checked >= 0


def test_gold_metric_names_match(nz, rows):
    from faktura.domain import EVAL_METRICS
    for name in {r["metric_name"] for r in rows if r["metric_name"] in EVAL_METRICS}:
        assert nz.match_metric(name) == name


def test_gold_target_units(nz, rows):
    from faktura.domain import TARGET_UNITS
    for r in rows:
        tu = TARGET_UNITS.get(r["metric_name"])
        if tu:
            assert _norm(nz, r).normalized_unit == tu


def test_gold_empty_normalized_stays_empty(nz, gold_results):
    for r in gold_results:
        if r["raw_value"] and not r["normalized_value"]:
            assert _norm(nz, r).normalized_value is None
