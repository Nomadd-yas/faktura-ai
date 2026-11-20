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
def test_quality_details_counts(mk):
    facts = [fact(1, review="accepted"), fact(2), fact(3, sourced=False, norm="failed"), fact(4, norm="converted")]
    issues = [mk.issue("range", ["R001"], by="backend"), mk.issue("typo", []),
              mk.issue("ambiguity", [])]
    issues[2].status = "ok"  # «не ошибка» не считается
    q = paper_quality(facts, issues)
    assert isinstance(q, Quality)
    d = q.details
    assert d["total"] == 4 and d["accepted"] == 1 and d["issues"] == 2
    assert d["sourced_pct"] == pytest.approx(75) and d["normalized_pct"] == pytest.approx(75)


def test_quality_accepts_factdrafts(mk):
    q = paper_quality([draft(1), draft(2, sourced=False)], [])
    assert q.details["total"] == 2 and q.details["sourced_pct"] == pytest.approx(50)
    assert q.details["accepted"] == 0


def test_quality_empty_facts_no_crash(mk):
    q = paper_quality([], [])
    assert q.details["total"] == 0 and q.details["issues"] == 0
    assert q.score is None or 0 <= q.score <= 1


def test_quality_perfect_is_good_and_score_in_range(mk):
    q = paper_quality([fact(i) for i in range(1, 6)], [])
    assert q.level == "good" and 0 <= q.score <= 1


def test_quality_degrades_with_unsourced_unnormalized_and_error_issues(mk):
    good = paper_quality([fact(i) for i in range(1, 6)], [])
    bad = paper_quality([fact(i, sourced=False, norm="failed") for i in range(1, 6)],
                        [mk.issue("range", [f"R00{i}"], by="backend", severity="error") for i in range(1, 6)])
    assert bad.score < good.score
    assert LEVELS.index(bad.level) < LEVELS.index(good.level)


def test_quality_more_open_issues_not_better(mk):
    facts = [fact(i) for i in range(1, 6)]
    a = paper_quality(facts, [mk.issue("range", ["R001"], by="backend", severity="error")])
    b = paper_quality(facts, [mk.issue("range", [f"R00{i}"], by="backend", severity="error") for i in range(1, 4)])
    assert b.score <= a.score


def test_quality_deterministic(mk):
    facts = [fact(1), fact(2, sourced=False)]
    assert paper_quality(facts, []).model_dump() == paper_quality(facts, []).model_dump()


# --- paper_coverage ----------------------------------------------------------------------------------
def test_coverage_filled_total_ratio_missing_in_profile_order(mk):
    b = mk.bundle([mk.result("R001", "PCE", 15), mk.result("R002", "Voc", 1)])
    cov = paper_coverage(b, mk.small_profile("PCE", "Voc", "Jsc", "FF"))
    assert isinstance(cov, Coverage)
    assert (cov.filled, cov.total) == (2, 4)
    assert cov.ratio == pytest.approx(0.5)
    assert cov.missing == ["Jsc", "FF"]


def test_coverage_full(mk):
    b = mk.bundle(mk.jv_triple(15))
    cov = paper_coverage(b, mk.small_profile("PCE", "Voc", "Jsc", "FF"))
    assert (cov.filled, cov.total, cov.ratio, cov.missing) == (4, 4, 1.0, [])


def test_coverage_rejected_and_absent_rows_do_not_count(mk):
    rej = mk.result("R001", "PCE", 15)
    rej.meta.review_status = "rejected"
    absent = mk.result("R002", "Voc", 1)
    absent.extraction_status = "absent"
    absent.normalized_value = None
    cov = paper_coverage(mk.bundle([rej, absent, mk.result("R003", "FF", 70)]), mk.small_profile("PCE", "Voc", "FF"))
    assert cov.filled == 1 and cov.missing == ["PCE", "Voc"]


def test_coverage_value_from_corrected_value_counts(mk):
    r = mk.result("R001", "PCE", 15)
    r.normalized_value = None
    r.meta.corrected_value = mk.D(15)
    assert paper_coverage(mk.bundle([r]), mk.small_profile("PCE")).filled == 1


def test_coverage_table_column_fields(mk):
    prof = mk.small_profile("materials.bandgap_value", "layers.layer_thickness", "devices.active_area")
    b = mk.bundle([], materials=[mk.material(eg="1.55")], devices=[mk.device(area=0.09)])
    cov = paper_coverage(b, prof)
    assert (cov.filled, cov.total) == (2, 3) and cov.missing == ["layer_thickness"]


def test_coverage_disabled_fields_excluded_from_total(mk):
    prof = mk.small_profile("PCE", "Voc")
    prof.fields[1] = ProfileField(key="Voc", enabled=False)
    cov = paper_coverage(mk.bundle([mk.result("R001", "PCE", 15)]), prof)
    assert (cov.filled, cov.total, cov.missing) == (1, 1, [])


def test_coverage_empty_profile_ratio_none(mk):
    cov = paper_coverage(mk.bundle([]), mk.small_profile())
    assert (cov.filled, cov.total, cov.ratio, cov.missing) == (0, 0, None, [])


def test_coverage_empty_bundle_all_missing(mk):
    from faktura.domain.records import PaperBundle
    cov = paper_coverage(PaperBundle.failed_stub("X1"), mk.small_profile("PCE", "Voc"))
    assert (cov.filled, cov.total, cov.ratio, cov.missing) == (0, 2, 0.0, ["PCE", "Voc"])


def test_coverage_builtin_profile_totals_match_profile(mk, solar_profile):
    cov = paper_coverage(mk.bundle(mk.jv_triple(15)), solar_profile)
    enabled = [f for f in solar_profile.fields if f.enabled]
    assert cov.total == len(enabled) and cov.filled >= 4
    assert cov.filled + len(cov.missing) == cov.total


def test_coverage_is_deterministic_and_pure(mk):
    b = mk.bundle(mk.jv_triple(15))
    snap = b.model_dump()
    p = mk.small_profile("PCE", "Rs")
    assert paper_coverage(b, p) == paper_coverage(b, p)
    assert b.model_dump() == snap
