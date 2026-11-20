"""assign_keys (К-6), link_duplicates (REQ-032), comparability_key (spec/05 §5.3, REQ-049/059)."""
from __future__ import annotations

import pytest

from faktura.checks import DuplicateLink, assign_keys, comparability_key, link_duplicates


# --- assign_keys -------------------------------------------------------------------------------------
def test_keys_non_empty_with_prefix_and_deterministic_across_runs(mk):
    def make():
        return [mk.issue("jv_consistency", ["R004", "R001", "R002", "R003"], by="backend"),
                mk.issue("contradiction", ["R002", "R003"])]
    k1 = [i.key for i in assign_keys(make(), mk.PID)]
    k2 = [i.key for i in assign_keys(make(), mk.PID)]
    assert k1 == k2 and all(k.startswith("I-") and len(k) > 2 for k in k1)


def test_key_independent_of_record_ids_order(mk):
    a = assign_keys([mk.issue("jv_consistency", ["R004", "R001", "R002", "R003"])], mk.PID)[0].key
    b = assign_keys([mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"])], mk.PID)[0].key
    assert a == b


def test_key_differs_by_check_name_and_by_record_set(mk):
    ks = [i.key for i in assign_keys([mk.issue("jv_consistency", ["R001"]), mk.issue("range", ["R001"]),
                                      mk.issue("range", ["R002"])], mk.PID)]
    assert len(set(ks)) == 3


def test_empty_record_ids_key_depends_on_parameters(mk):
    a, b = assign_keys([mk.issue("ambiguity", [], numbers={"p": 1}), mk.issue("ambiguity", [], numbers={"p": 2})],
                       mk.PID)
    assert a.key != b.key
    a2 = assign_keys([mk.issue("ambiguity", [], numbers={"p": 1})], mk.PID)[0]
    assert a2.key == a.key  # детерминирован от параметров


def test_keys_unique_within_paper_even_for_identical_issues(mk):
    out = assign_keys([mk.issue("typo", []), mk.issue("typo", []), mk.issue("typo", [])], mk.PID)
    assert len({i.key for i in out}) == 3


def test_assign_keys_empty_and_keeps_count_and_order(mk):
    assert assign_keys([], mk.PID) == []
    iss = [mk.issue("typo", []), mk.issue("range", ["R001"])]
    out = assign_keys(iss, mk.PID)
    assert [i.check_name for i in out] == ["typo", "range"]


def test_existing_key_of_unchanged_issue_is_stable_on_reassign(mk):
    first = assign_keys([mk.issue("range", ["R001"])], mk.PID)
    again = assign_keys(first, mk.PID)
    assert first[0].key == again[0].key


# --- link_duplicates ---------------------------------------------------------------------------------
def test_duplicate_same_value_other_place_is_linked_to_earlier(mk):
    rs = [mk.result("R001", "PCE", "15", source_table="Table 1"),
          mk.result("R002", "PCE", "15", source_figure="Fig. 2")]
    links = link_duplicates(mk.bundle(rs))
    assert links == [DuplicateLink(result_id="R002", duplicate_of="R001", note="связано бэком")]


def test_three_copies_all_point_to_first(mk):
    rs = [mk.result(f"R00{i}", "PCE", "15") for i in (1, 2, 3)]
    links = link_duplicates(mk.bundle(rs))
    assert {(l.result_id, l.duplicate_of) for l in links} == {("R002", "R001"), ("R003", "R001")}


def test_different_value_device_metric_measurement_or_aggregation_not_linked(mk):
    base = dict(device="DV1", meas="MS1", agg="champion")
    variants = [dict(value="16"), dict(metric="Voc"), dict(device="DV2"), dict(meas="MS2"), dict(agg="mean")]
    for v in variants:
        kw = {**base, **{k: x for k, x in v.items() if k in base}}
        r2 = mk.result("R002", v.get("metric", "PCE"), v.get("value", "15"), **kw)
        b = mk.bundle([mk.result("R001", "PCE", "15"), r2], devices=[mk.device("DV1"), mk.device("DV2")],
                      measurements=[mk.measurement("MS1"), mk.measurement("MS2")])
        assert link_duplicates(b) == [], v


def test_already_linked_and_rejected_rows_are_not_relinked(mk):
    r2 = mk.result("R002", "PCE", "15", duplicate_of="R001")
    r3 = mk.result("R003", "PCE", "15")
    r3.meta.review_status = "rejected"
    assert link_duplicates(mk.bundle([mk.result("R001", "PCE", "15"), r2, r3])) == []


def test_link_uses_effective_value(mk):
    r2 = mk.result("R002", "PCE", "16")
    r2.meta.corrected_value = mk.D("15")
    assert [l.result_id for l in link_duplicates(mk.bundle([mk.result("R001", "PCE", "15"), r2]))] == ["R002"]


def test_link_duplicates_does_not_mutate_bundle_and_empty_ok(mk):
    b = mk.bundle([mk.result("R001", "PCE", "15"), mk.result("R002", "PCE", "15")])
    snap = b.model_dump()
    link_duplicates(b)
    assert b.model_dump() == snap
    assert link_duplicates(mk.bundle([])) == []


# --- comparability_key -------------------------------------------------------------------------------
def _cmp(mk, *, area=0.5, agg="champion", scan="reverse", certified=False, protocol=None, spectrum="AM1.5G",
         intensity=100, dtype="cell", mclass="perovskite"):
    r = mk.result("R001", "PCE", 15, agg=agg)
    ms = mk.measurement(spectrum=spectrum, intensity=intensity, scan=scan, certified=certified,
                        stability_protocol=protocol)
    b = mk.bundle([r], devices=[mk.device(area=area, dtype=dtype)], materials=[mk.material(mclass=mclass)],
                  measurements=[ms])
    return comparability_key(b.results[0], b)


def test_full_result_gives_complete_key_with_components(mk):
    key, incomplete = _cmp(mk)
    assert isinstance(key, str) and incomplete is False
    for part in ("perovskite", "cell", "AM1.5G", "champion", "reverse"):
        assert part in key


def test_same_inputs_same_key_value_does_not_matter(mk):
    assert _cmp(mk) == _cmp(mk)


@pytest.mark.parametrize("a,b,same", [
    (0.05, 0.5, False), (0.5, 2.0, False), (0.05, 2.0, False),
    (0.1, 0.5, True), (1.0, 0.5, True), (0.2, 0.9, True),   # корзины: <0.1, 0.1-1, >1 cm2
    (0.0999, 0.1, False), (1.0, 1.01, False),
])
def test_area_bucket_boundaries(mk, a, b, same):
    assert (_cmp(mk, area=a)[0] == _cmp(mk, area=b)[0]) is same


@pytest.mark.parametrize("kw", [dict(agg="mean"), dict(scan="forward"), dict(certified=True),
                                dict(protocol="ISOS-D"), dict(dtype="module"), dict(mclass="CIGS"),
                                dict(spectrum="AM1.5G", intensity=50)])
def test_each_component_changes_key(mk, kw):
    assert _cmp(mk, **kw)[0] != _cmp(mk)[0]


def test_empty_field_marks_incomplete(mk):
    key, inc = _cmp(mk, scan=None)
    assert inc is True and isinstance(key, str)
    assert _cmp(mk, area=None)[1] is True


def test_missing_measurement_and_device_no_crash_incomplete(mk):
    r = mk.result("R001", "PCE", 15, device=None, meas=None)
    b = mk.bundle([r], devices=[], materials=[], measurements=[])
    key, inc = comparability_key(r, b)
    assert isinstance(key, str) and inc is True


def test_comparability_key_is_filter_only_no_issue_created(mk, solar_profile):
    from faktura.checks import run_backend_rules
    r = mk.result("R001", "PCE", 15)
    b = mk.bundle([r], measurements=[mk.measurement(spectrum="halogen lamp", intensity=None, unit=None)])
    comparability_key(r, b)
    assert mk.of(run_backend_rules(b, solar_profile.rules), "comparability") == []
