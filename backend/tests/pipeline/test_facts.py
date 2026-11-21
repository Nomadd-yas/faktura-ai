"""build_facts: факты и якоря из bundle; bbox -> доли страницы, запасной find_rects (план 5.6)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.pipeline import build_facts


def prepared(synth_bundle):
    """Bundle с уже посчитанными normalized_* (нормализация здесь не проверяется)."""
    b = synth_bundle()
    for r in b.results:
        if r.raw_value and r.raw_value.replace(".", "").isdigit():
            r.normalized_value = Decimal(r.raw_value)
            r.normalized_unit = r.raw_unit
            r.metric_value, r.metric_unit = r.normalized_value, r.normalized_unit
    return b


def result_facts(facts):
    return [f for f in facts if f.target.get("entity") == "results"]


def test_one_fact_per_result_row(synth_bundle, fake_parser, tmp_path):
    b = prepared(synth_bundle)
    facts, _ = build_facts(b, None, fake_parser, tmp_path / "a.pdf")
    rf = result_facts(facts)
    assert sorted(f.target["id"] for f in rf) == sorted(r.result_id for r in b.results)
    assert all(f.target["field"] == "metric_value" for f in rf)




























