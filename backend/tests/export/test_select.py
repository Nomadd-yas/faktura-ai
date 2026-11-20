"""select_for_submission и materialize_manual_fact (spec/14 §14.5; REQ-002..REQ-010, К-14)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import ReviewStatus
from faktura.domain.records import AddedBy, IssueRecord, PaperBundle
from faktura.export import SelectResult, materialize_manual_fact, select_for_submission


def _rid(b: PaperBundle) -> list[str]:
    return [r.result_id for r in b.results]


def test_clean_bundle_passes_unchanged(make_bundle, profile):
    res = select_for_submission(make_bundle(), profile)
    assert isinstance(res, SelectResult)
    assert _rid(res.bundle) == ["R001", "R002"]
    assert [i.issue_id for i in res.bundle.issues] == ["I1"]
    assert res.bundle.publication.publication_id == "P001"
    assert res.events == []


def test_does_not_mutate_input(make_bundle, profile):
    b = make_bundle()
    b.results[0].meta.review_status = ReviewStatus.rejected
    b.results[1].meta.corrected_value = Decimal("19")
    before = b.model_dump()
    select_for_submission(b, profile)
    assert b.model_dump() == before


def test_rejected_result_row_dropped(make_bundle, profile):
    b = make_bundle()
    b.results[1].meta.review_status = ReviewStatus.rejected
    assert _rid(select_for_submission(b, profile).bundle) == ["R001"]


@pytest.mark.parametrize("status", [ReviewStatus.new, ReviewStatus.accepted])
def test_new_and_accepted_rows_kept(make_bundle, profile, status):
    b = make_bundle()
    for r in b.results:
        r.meta.review_status = status
    assert _rid(select_for_submission(b, profile).bundle) == ["R001", "R002"]


def test_corrected_value_replaces_normalized_and_metric_value(make_bundle, profile):
    b = make_bundle()
    b.results[0].meta.corrected_value = Decimal("19.4")
    r = select_for_submission(b, profile).bundle.results[0]
    assert r.normalized_value == Decimal("19.4")
    assert r.metric_value == Decimal("19.4")
    assert r.raw_value == "20.1"  # исходник из статьи остаётся


def test_corrected_value_zero_is_not_ignored(make_bundle, profile):
    b = make_bundle()
    b.results[0].meta.corrected_value = Decimal("0")
    r = select_for_submission(b, profile).bundle.results[0]
    assert r.normalized_value == Decimal("0")


def test_absent_rows_dropped_by_default(make_bundle, profile):
    b = make_bundle()
    b.results[1].extraction_status = "absent"
    b.results[1].metric_value = None
    assert _rid(select_for_submission(b, profile).bundle) == ["R001"]


def test_absent_rows_kept_when_flag_set(make_bundle, profile_export_absent):
    b = make_bundle()
    b.results[1].extraction_status = "absent"
    assert _rid(select_for_submission(b, profile_export_absent).bundle) == ["R001", "R002"]


def test_present_not_extractable_always_kept(make_bundle, profile):
    b = make_bundle()
    b.results[1].extraction_status = "present_not_extractable"
    assert "R002" in _rid(select_for_submission(b, profile).bundle)


def _issue(by="ml", conf=None, status="open", check="ambiguity"):
    return IssueRecord(publication_id="P001", issue_id="I9", record_ids=["R001"], check_name=check, description="x",
                       computed_by=by, confidence=conf, status=status)


@pytest.mark.parametrize("conf,kept", [(0.49, False), (0.5, True), (0.9, True), (None, True)])
def test_ml_issue_confidence_threshold(make_bundle, profile, conf, kept):
    b = make_bundle()
    b.issues.append(_issue(conf=conf))
    ids = [i.issue_id for i in select_for_submission(b, profile).bundle.issues]
    assert ("I9" in ids) is kept


def test_threshold_taken_from_profile(make_bundle, profile):
    p = profile.model_copy(deep=True)
    p.flags.ml_issue_min_confidence = 0.8
    b = make_bundle()
    b.issues.append(_issue(conf=0.7))
    assert "I9" not in [i.issue_id for i in select_for_submission(b, p).bundle.issues]


def test_low_confidence_backend_issue_is_not_filtered(make_bundle, profile):
    b = make_bundle()
    b.issues.append(_issue(by="backend", conf=0.1, check="jv_consistency"))
    assert "I9" in [i.issue_id for i in select_for_submission(b, profile).bundle.issues]


@pytest.mark.parametrize("status,kept", [("open", True), ("fixed", True), ("ok", False)])
def test_issue_status_filter(make_bundle, profile, status, kept):
    b = make_bundle()
    b.issues.append(_issue(by="backend", status=status, check="range"))
    ids = [i.issue_id for i in select_for_submission(b, profile).bundle.issues]
    assert ("I9" in ids) is kept


def test_out_of_scope_exports_only_publication(make_bundle, profile):
    b = make_bundle()
    b.publication.in_scope = False
    b.publication.exclusion_reason = "tandem"
    out = select_for_submission(b, profile).bundle
    assert out.publication.in_scope is False
    assert out.publication.exclusion_reason == "tandem"
    for t in ("materials", "devices", "layers", "measurements", "results", "issues"):
        assert getattr(out, t) == []


def test_in_scope_none_is_not_excluded(make_bundle, profile):
    b = make_bundle()
    b.publication.in_scope = None
    assert len(select_for_submission(b, profile).bundle.results) == 2


def test_failed_gives_stub_only_publication_id(make_bundle, profile):
    out = select_for_submission(make_bundle("P007"), profile, failed=True).bundle
    assert out == PaperBundle.failed_stub("P007")
    assert out.publication.title is None and out.results == []


def test_rejected_field_fact_clears_value_and_unit(make_bundle, make_fact, profile):
    f = make_fact(target={"entity": "layers", "id": "DV1-L1", "field": "layer_thickness"}, param="layer_thickness",
                  metric_name=None, review_status=ReviewStatus.rejected, added_by=AddedBy.auto)
    layer = select_for_submission(make_bundle(), profile, manual_facts=[f]).bundle.layers[0]
    assert layer.layer_thickness is None
    assert layer.thickness_unit is None  # REQ-037
    assert layer.layer_function == "absorber"  # остальное нетронуто


def test_rejected_bandgap_clears_unit_too(make_bundle, make_fact, profile):
    f = make_fact(target={"entity": "materials", "id": "M1", "field": "bandgap_value"}, param="bandgap",
                  metric_name=None, review_status=ReviewStatus.rejected, added_by=AddedBy.auto)
    m = select_for_submission(make_bundle(), profile, manual_facts=[f]).bundle.materials[0]
    assert m.bandgap_value is None and m.bandgap_unit is None


# --- ручные факты ------------------------------------------------------------------------------

def test_manual_fact_with_quote_becomes_rm1_row(make_bundle, make_fact, profile):
    res = select_for_submission(make_bundle(), profile, manual_facts=[make_fact()])
    new = [r for r in res.bundle.results if r.result_id.startswith("RM")]
    assert [r.result_id for r in new] == ["RM1"]
    r = new[0]
    assert r.metric_name == "PCE" and r.metric_value == Decimal("20.1") and r.extraction_status == "extracted"
    assert r.source_page == 3 and r.source_fragment


def test_two_manual_facts_get_sequential_ids(make_bundle, make_fact, profile):
    f1 = make_fact()
    f2 = make_fact(metric_name="Voc", param="voc", normalized_value=Decimal("1.1"), normalized_unit="V",
                   raw_value="1.1")
    res = select_for_submission(make_bundle(), profile, manual_facts=[f1, f2])
    assert sorted(r.result_id for r in res.bundle.results if r.result_id.startswith("RM")) == ["RM1", "RM2"]


def test_manual_fact_without_quote_not_exported_and_logged(make_bundle, make_fact, profile):
    f = make_fact(source_fragment=None, source_table=None, source_figure=None)
    res = select_for_submission(make_bundle(), profile, manual_facts=[f])
    assert not any(r.result_id.startswith("RM") for r in res.bundle.results)
    assert res.events


def test_manual_fact_ignored_for_failed_paper(make_bundle, make_fact, profile):
    res = select_for_submission(make_bundle(), profile, failed=True, manual_facts=[make_fact()])
    assert res.bundle.results == []


def test_manual_fact_ignored_for_out_of_scope(make_bundle, make_fact, profile):
    b = make_bundle()
    b.publication.in_scope = False
    assert select_for_submission(b, profile, manual_facts=[make_fact()]).bundle.results == []


def test_manual_fact_of_other_publication_not_added(make_bundle, make_fact, profile):
    res = select_for_submission(make_bundle(), profile, manual_facts=[make_fact(publication_id="P999")])
    assert not any(r.result_id.startswith("RM") for r in res.bundle.results)


# --- materialize_manual_fact -------------------------------------------------------------------

def test_materialize_full_row(make_bundle, make_fact, profile):
    r = materialize_manual_fact(make_fact(), make_bundle(), profile)
    assert r is not None
    assert r.result_id == "RM1"
    assert r.publication_id == "P001"
    assert r.metric_name == "PCE"
    assert r.metric_value == r.normalized_value == Decimal("20.1")
    assert r.metric_unit == r.normalized_unit == "%"
    assert r.raw_value == "20,1" and r.raw_unit == "%"
    assert r.source_page == 3
    assert r.extraction_status == "extracted"
    assert r.result_origin == "primary"
    assert r.device_id == "DV1"


def test_materialize_uses_corrected_value(make_bundle, make_fact, profile):
    r = materialize_manual_fact(make_fact(corrected_value=Decimal("20.5")), make_bundle(), profile)
    assert r.metric_value == Decimal("20.5") and r.normalized_value == Decimal("20.5")


def test_materialize_number_continues_after_existing_rm(make_bundle, make_fact, profile):
    b = make_bundle()
    b.results[1].result_id = "RM1"
    b.results[1].duplicate_of = None
    assert materialize_manual_fact(make_fact(), b, profile).result_id == "RM2"


@pytest.mark.parametrize("patch", [
    {"source_page": None},
    {"source_fragment": None, "source_table": None, "source_figure": None},
    {"source_fragment": "   ", "source_table": None, "source_figure": None},
])
def test_materialize_without_page_or_quote_returns_none(make_bundle, make_fact, profile, patch):
    assert materialize_manual_fact(make_fact(**patch), make_bundle(), profile) is None


def test_materialize_table_reference_instead_of_quote_is_ok(make_bundle, make_fact, profile):
    f = make_fact(source_fragment=None, source_table="Table 2")
    r = materialize_manual_fact(f, make_bundle(), profile)
    assert r is not None and r.source_table == "Table 2"


def test_materialize_non_metric_fact_returns_none(make_bundle, make_fact, profile):
    f = make_fact(metric_name=None, param="bandgap", target={"entity": "materials", "id": "M1",
                                                              "field": "bandgap_value"})
    assert materialize_manual_fact(f, make_bundle(), profile) is None


def test_materialize_rejected_returns_none(make_bundle, make_fact, profile):
    assert materialize_manual_fact(make_fact(review_status=ReviewStatus.rejected), make_bundle(), profile) is None


def test_materialize_value_missing_returns_none(make_bundle, make_fact, profile):
    assert materialize_manual_fact(make_fact(normalized_value=None), make_bundle(), profile) is None
