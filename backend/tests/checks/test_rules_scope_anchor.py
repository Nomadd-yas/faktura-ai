"""missing_conditions (scope=paper), comparability (правило выключено по умолчанию), source_page/source_fragment
замечаний бэка из записи-якоря и запасной порядок (spec/05 §5.2, план 5.3)."""
from __future__ import annotations

import pytest

from faktura.checks import run_backend_rules


def run(mk, bundle, *names, quote=None, **p):
    return run_backend_rules(bundle, mk.rules_only(*names, **p), quote=quote)


# --- missing_conditions ------------------------------------------------------------------------------
def _ok_bundle(mk):
    return mk.bundle(mk.jv_triple(15))  # J-V c AM1.5G и 100 mW/cm2, у прибора есть active_area


def test_missing_conditions_not_fired_when_everything_present(mk):
    assert mk.of(run(mk, _ok_bundle(mk), "missing_conditions"), "missing_conditions") == []


@pytest.mark.parametrize("kw", [
    {"measurements": []},                                          # нет J-V вообще
    {"measurements": ["spectrum_none"]},                           # нет spectrum
    {"measurements": ["intensity_none_no_spectrum"]},              # нет light_intensity
])
def test_missing_conditions_fires_without_jv_conditions(mk, kw):
    ms = {"spectrum_none": [lambda: mk.measurement(spectrum=None)],
          "intensity_none_no_spectrum": [lambda: mk.measurement(intensity=None, unit=None)]}
    key = kw["measurements"][0] if kw["measurements"] else None
    measurements = [f() for f in ms[key]] if key else []
    out = mk.of(run(mk, mk.bundle(mk.jv_triple(15), measurements=measurements), "missing_conditions"),
                "missing_conditions")
    assert len(out) == 1 and out[0].record_ids == [] and out[0].severity == "warn"


def test_missing_conditions_fires_without_active_area(mk):
    b = mk.bundle(mk.jv_triple(15), devices=[mk.device(area=None)])
    out = mk.of(run(mk, b, "missing_conditions"), "missing_conditions")
    assert len(out) == 1 and "площад" in out[0].description.lower()


def test_missing_conditions_at_most_one_per_paper_even_if_all_missing(mk):
    b = mk.bundle(mk.jv_triple(15), devices=[mk.device("DV1", area=None), mk.device("DV2", area=None)],
                  measurements=[mk.measurement(spectrum=None, intensity=None, unit=None)])
    assert len(mk.of(run(mk, b, "missing_conditions"), "missing_conditions")) == 1


# --- comparability -----------------------------------------------------------------------------------
def test_comparability_not_created_with_default_profile(mk, solar_profile):
    ms = mk.measurement(spectrum="halogen lamp", intensity=None, unit=None)
    b = mk.bundle(mk.jv_triple(15), measurements=[ms])
    out = run_backend_rules(b, solar_profile.rules)
    assert mk.of(out, "comparability") == []


def test_comparability_created_for_nonstandard_light_when_rule_enabled(mk):
    ms = mk.measurement(spectrum="halogen lamp", intensity=None, unit=None)
    out = mk.of(run(mk, mk.bundle(mk.jv_triple(15), measurements=[ms]), "comparability"), "comparability")
    assert out and all(i.check_name == "comparability" for i in out)


def test_comparability_not_created_for_standard_article_when_enabled(mk):
    assert mk.of(run(mk, _ok_bundle(mk), "comparability"), "comparability") == []


# --- source_page / source_fragment из записи-якоря ---------------------------------------------------
def test_backend_issue_takes_page_and_fragment_from_anchor_result(mk):
    rs = mk.jv_triple("12.5")
    rs[3].source_page, rs[3].source_fragment = 7, "PCE of 12.5% was measured"
    rs[0].source_page, rs[0].source_fragment = 2, "other fragment"
    i = mk.of(run(mk, mk.bundle(rs), "jv_consistency"), "jv_consistency")[0]
    assert i.record_ids[0] == "R004"
    assert (i.source_page, i.source_fragment) == (7, "PCE of 12.5% was measured")


def test_anchor_is_first_of_ordered_record_ids_for_two_record_rules(mk):
    rs = [mk.result("R001", "Jsc", 20, page=3, fragment="Jsc 20"),
          mk.result("R002", "Jsc_EQE", 10, page=9, fragment="EQE 10")]
    i = mk.of(run(mk, mk.bundle(rs), "eqe_consistency"), "eqe_consistency")[0]
    anchor = mk.bundle(rs).by_id("results", i.record_ids[0])
    assert (i.source_page, i.source_fragment) == (anchor.source_page, anchor.source_fragment)


def test_anchor_without_fragment_falls_back_to_table_row_reference(mk):
    rs = mk.jv_triple("12.5")
    rs[3].source_fragment, rs[3].source_table = None, "Table 2"
    b = mk.bundle(rs, devices=[mk.device("DV1", label="B4")])
    i = mk.of(run(mk, b, "jv_consistency"), "jv_consistency")[0]
    assert i.source_fragment == "Table 2, строка B4"
    assert i.source_page == 5


def test_anchor_without_fragment_falls_back_to_figure_reference(mk):
    rs = mk.jv_triple("12.5")
    rs[3].source_fragment, rs[3].source_table, rs[3].source_figure = None, None, "Figure 3"
    i = mk.of(run(mk, mk.bundle(rs), "jv_consistency"), "jv_consistency")[0]
    assert "Figure" in i.source_fragment and "3" in i.source_fragment


def test_anchor_without_page_leaves_issue_page_empty_without_crash(mk):
    rs = mk.jv_triple("12.5")
    rs[3].source_page = None
    i = mk.of(run(mk, mk.bundle(rs), "jv_consistency"), "jv_consistency")[0]
    assert i.source_page in (None, "")


# --- запасной порядок для scope=paper ----------------------------------------------------------------
def test_paper_scope_anchor_is_first_jv_result(mk):
    rs = [mk.result("R001", "Rs", 5, meas="MS2", page=2, fragment="Rs 5"),
          mk.result("R002", "PCE", 15, meas="MS1", page=7, fragment="PCE 15 text")]
    b = mk.bundle(rs, devices=[mk.device(area=None)],
                  measurements=[mk.measurement("MS1", mtype="J-V"), mk.measurement("MS2", mtype="EQE")])
    i = mk.of(run(mk, b, "missing_conditions"), "missing_conditions")[0]
    assert i.record_ids == []
    assert (i.source_page, i.source_fragment) == (7, "PCE 15 text")


def test_paper_scope_without_results_uses_page_1_and_title_found_by_quote(mk):
    q = mk.FakeQuote(found={"Test paper"})
    b = mk.bundle([], devices=[mk.device(area=None)], title="Test paper")
    i = mk.of(run(mk, b, "missing_conditions", quote=q), "missing_conditions")[0]
    assert i.source_page == 1 and i.source_fragment == "Test paper"
    assert (1, "Test paper") in q.calls


def test_paper_scope_title_not_found_leaves_fragment_empty_and_notes(mk):
    q = mk.FakeQuote(found=set(), answer=False)
    b = mk.bundle([], devices=[mk.device(area=None)], title="Absent title")
    i = mk.of(run(mk, b, "missing_conditions", quote=q), "missing_conditions")[0]
    assert i.source_page == 1
    assert not i.source_fragment
    assert i.meta.notes_backend, "ненайденная цитата -> пометка в meta.notes_backend"


def test_paper_scope_title_quote_not_checked_keeps_title(mk):
    q = mk.FakeQuote(answer=None)  # None - не проверяется
    b = mk.bundle([], devices=[mk.device(area=None)], title="Some title")
    i = mk.of(run(mk, b, "missing_conditions", quote=q), "missing_conditions")[0]
    assert i.source_fragment == "Some title"


def test_paper_scope_without_quote_checker_uses_title(mk):
    b = mk.bundle([], devices=[mk.device(area=None)], title="Some title")
    i = mk.of(run(mk, b, "missing_conditions"), "missing_conditions")[0]
    assert (i.source_page, i.source_fragment) == (1, "Some title")
