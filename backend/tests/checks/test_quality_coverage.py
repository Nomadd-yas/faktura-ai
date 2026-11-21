"""paper_quality и paper_coverage (единственный владелец расчёта покрытия), spec/04 §4.7, spec/03 §3.4.

Допущение: Quality.details содержит ключи quality из spec/03 (total, sourced_pct, normalized_pct, accepted, issues);
score в [0; 1]; level good|doubt|bad. Coverage: total - число включённых полей профиля, filled - полей с хотя бы
одним действующим значением, missing - ключи полей без значения в порядке профиля, ratio = filled / total.
"""
from __future__ import annotations

import pytest

from faktura.checks import paper_coverage, paper_quality
from faktura.domain.entities import Coverage, Fact, FactDraft, Quality
from faktura.domain.profile import ProfileField

LEVELS = ["bad", "doubt", "good"]


def fact(i, *, sourced=True, norm="ok", review="new", **kw) -> Fact:
    return Fact(id=f"fct_{i}", paper_id="pap_1", publication_id="T001", target={"entity": "results", "id": f"R{i:03d}",
                                                                                  "field": "metric_value"},
                param="pce", norm_status=norm, review_status=review,
                source_page=3 if sourced else None, source_fragment="PCE = 15" if sourced else None, **kw)


def draft(i, *, sourced=True, norm="ok") -> FactDraft:
    return FactDraft(ref=f"R{i:03d}", target={"entity": "results", "id": f"R{i:03d}", "field": "metric_value"},
                     param="pce", norm_status=norm, source_page=3 if sourced else None,
                     source_fragment="PCE = 15" if sourced else None)


# --- paper_quality -----------------------------------------------------------------------------------














# --- paper_coverage ----------------------------------------------------------------------------------
def test_coverage_filled_total_ratio_missing_in_profile_order(mk):
    b = mk.bundle([mk.result("R001", "PCE", 15), mk.result("R002", "Voc", 1)])
    cov = paper_coverage(b, mk.small_profile("PCE", "Voc", "Jsc", "FF"))
    assert isinstance(cov, Coverage)
    assert (cov.filled, cov.total) == (2, 4)
    assert cov.ratio == pytest.approx(0.5)
    assert cov.missing == ["Jsc", "FF"]


















