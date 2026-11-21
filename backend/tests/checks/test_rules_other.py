"""run_backend_rules: voc_limit, range, eqe_consistency, champion_vs_mean, hysteresis_index, contradiction,
unit_inconsistency, voc_deficit_consistency, plausibility, figure_mismatch, dedup_trap (spec/05 §5.2)."""
from __future__ import annotations

import re

import pytest

from faktura.checks import BACKEND_RULES, run_backend_rules


def run(mk, bundle, name, **p):
    return mk.of(run_backend_rules(bundle, mk.rules_only(name, **p)), name)


def test_backend_rules_cover_catalog_backend_names(mk):
    for name, info in mk.CHECK_CATALOG.items():
        if info.computed_by in ("backend", "both") and info.exported:
            assert name in BACKEND_RULES, name
    assert len(set(BACKEND_RULES)) == len(BACKEND_RULES)


# --- voc_limit ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("voc,eg,fires", [("1.2", "1.1", True), ("1.1", "1.1", True), ("1.09", "1.1", False)])
def test_voc_limit_boundary(mk, voc, eg, fires):
    b = mk.bundle([mk.result("R001", "Voc", voc)], materials=[mk.material(eg=eg)])
    out = run(mk, b, "voc_limit")
    assert bool(out) is fires
    if fires:
        assert out[0].severity == "error" and "R001" in out[0].record_ids
        assert voc in out[0].description and eg in out[0].description


def test_voc_limit_no_bandgap_or_no_material_skips(mk):
    assert run(mk, mk.bundle([mk.result("R001", "Voc", 5)], materials=[mk.material(eg=None)]), "voc_limit") == []
    assert run(mk, mk.bundle([mk.result("R001", "Voc", 5)], devices=[mk.device(material=None)]), "voc_limit") == []


# --- range -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("metric,value,fires", [
    ("PCE", "0", True), ("PCE", "-1", True), ("PCE", "100", True), ("PCE", "100.5", True), ("PCE", "99.9", False),
    ("PCE", "0.01", False),
    ("FF", "0", True), ("FF", "100", False), ("FF", "100.1", True), ("FF", "50", False),
    ("Jsc", "0", True), ("Jsc", "-3", True), ("Jsc", "50", False), ("Jsc", "50.1", True),
    ("Voc", "0", True), ("Voc", "3", False), ("Voc", "3.01", True),
    ("Rs", "0", True), ("Rs", "-1", True), ("Rs", "5", False),
    ("Rsh", "0", True), ("Rsh", "1000", False),
    ("retained_PCE", "150", False), ("retained_PCE", "150.1", True), ("retained_PCE", "80", False),
])
def test_range_boundaries(mk, metric, value, fires):
    out = run(mk, mk.bundle([mk.result("R001", metric, value)]), "range")
    assert bool(out) is fires
    if fires:
        assert out[0].severity == "error" and out[0].record_ids == ["R001"]
        assert value in out[0].description


def test_range_voc_over_3v_only_for_cells(mk):
    b = mk.bundle([mk.result("R001", "Voc", 5)], devices=[mk.device(dtype="minimodule")])
    assert run(mk, b, "range") == []
    assert run(mk, mk.bundle([mk.result("R001", "Voc", 5)], devices=[mk.device(dtype="cell")]), "range")


def test_range_uses_effective_value(mk):
    r = mk.result("R001", "PCE", 150)
    r.meta.corrected_value = mk.D(15)
    assert run(mk, mk.bundle([r]), "range") == []


def test_range_skips_rejected_and_duplicates(mk):
    r1 = mk.result("R001", "PCE", 150)
    r1.meta.review_status = "rejected"
    r2 = mk.result("R002", "PCE", 150, duplicate_of="R003")
    assert run(mk, mk.bundle([r1, r2]), "range") == []


def test_range_ignores_unparsed_string_value(mk):
    r = mk.result("R001", "PCE", 1)
    r.normalized_value = "n/a"
    assert run(mk, mk.bundle([r]), "range") == []


# --- eqe_consistency ---------------------------------------------------------------------------------
@pytest.mark.parametrize("jsc,eqe,fires", [
    ("20", "19", False),  # ровно 5 %
    ("20", "18.99", True), ("20", "21", False), ("20", "21.01", True),
    ("24.89", "23.5", True),  # пример из spec/05
])
def test_eqe_consistency_threshold(mk, jsc, eqe, fires):
    rs = [mk.result("R002", "Jsc", jsc), mk.result("R014", "Jsc_EQE", eqe)]
    out = run(mk, mk.bundle(rs), "eqe_consistency")
    assert bool(out) is fires
    if fires:
        assert set(out[0].record_ids) == {"R002", "R014"} and out[0].severity == "warn"
        assert jsc in out[0].description and eqe in out[0].description


def test_eqe_consistency_needs_both_and_same_device(mk):
    assert run(mk, mk.bundle([mk.result("R001", "Jsc", 20)]), "eqe_consistency") == []
    rs = [mk.result("R001", "Jsc", 20, device="DV1"), mk.result("R002", "Jsc_EQE", 10, device="DV2")]
    b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")])
    assert run(mk, b, "eqe_consistency") == []


def test_eqe_consistency_custom_threshold(mk):
    rs = [mk.result("R001", "Jsc", 20), mk.result("R002", "Jsc_EQE", 18)]
    assert run(mk, mk.bundle(rs), "eqe_consistency")
    assert run(mk, mk.bundle(rs), "eqe_consistency", threshold_pct=15) == []


# --- champion_vs_mean --------------------------------------------------------------------------------
@pytest.mark.parametrize("agg2", ["mean", "median"])
def test_champion_below_mean_fires(mk, agg2):
    rs = [mk.result("R032", "PCE", "13.66", agg="champion"), mk.result("R041", "PCE", "14.66", agg=agg2)]
    out = run(mk, mk.bundle(rs), "champion_vs_mean")
    assert len(out) == 1 and set(out[0].record_ids) == {"R032", "R041"} and out[0].severity == "error"
    assert "13.66" in out[0].description and "14.66" in out[0].description


@pytest.mark.parametrize("champ,mean", [("14.66", "14.66"), ("15", "14.66")])
def test_champion_not_below_mean_ok(mk, champ, mean):
    rs = [mk.result("R001", "PCE", champ, agg="champion"), mk.result("R002", "PCE", mean, agg="mean")]
    assert run(mk, mk.bundle(rs), "champion_vs_mean") == []


def test_champion_vs_mean_requires_same_device_measurement_and_metric(mk):
    for kw in ({"device": "DV2"}, {"meas": "MS2"}):
        rs = [mk.result("R001", "PCE", 10, agg="champion"), mk.result("R002", "PCE", 12, agg="mean", **kw)]
        b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")],
                      measurements=[mk.measurement("MS1"), mk.measurement("MS2")])
        assert run(mk, b, "champion_vs_mean") == []
    rs = [mk.result("R001", "PCE", 10, agg="champion"), mk.result("R002", "Voc", 12, agg="mean")]
    assert run(mk, mk.bundle(rs), "champion_vs_mean") == []


# --- hysteresis_index --------------------------------------------------------------------------------
def _hi_bundle(mk, rev, fwd, hi=None):
    rs = [mk.result("R001", "PCE", rev, meas="MS1"), mk.result("R002", "PCE", fwd, meas="MS2")]
    if hi is not None:
        rs.append(mk.result("R003", "HI", hi, meas="MS1"))
    ms = [mk.measurement("MS1", scan="reverse"), mk.measurement("MS2", scan="forward")]
    return mk.bundle(rs, measurements=ms)


@pytest.mark.parametrize("stated,fires", [("10", False), ("10.9", False), ("11", False), ("11.5", True),
                                          ("8.9", True)])
def test_hysteresis_index_gap_over_1pp(mk, stated, fires):  # HI = (20-18)/20 = 10 %
    assert bool(run(mk, _hi_bundle(mk, 20, 18, stated), "hysteresis_index")) is fires


def test_hysteresis_negative_hi_fires_info(mk):
    out = run(mk, _hi_bundle(mk, 18, 20), "hysteresis_index")
    assert len(out) == 1 and out[0].severity == "info"


def test_hysteresis_no_forward_no_issue(mk):
    rs = [mk.result("R001", "PCE", 20, meas="MS1")]
    assert run(mk, mk.bundle(rs, measurements=[mk.measurement("MS1", scan="reverse")]), "hysteresis_index") == []


# --- contradiction (BE-часть) ------------------------------------------------------------------------
def test_contradiction_same_value_different_places(mk):
    rs = [mk.result("R002", "PCE", "3.85", source_table="Table 4"),
          mk.result("R003", "PCE", "5.25", source_figure="Fig. 10", page=13)]
    out = run(mk, mk.bundle(rs), "contradiction")
    assert len(out) == 1 and set(out[0].record_ids) == {"R002", "R003"} and out[0].severity == "error"
    assert "3.85" in out[0].description and "5.25" in out[0].description


def test_contradiction_equal_values_none(mk):
    rs = [mk.result("R002", "PCE", "15", source_table="Table 4"),
          mk.result("R003", "PCE", "15", source_figure="Fig. 2")]
    assert run(mk, mk.bundle(rs), "contradiction") == []


def test_contradiction_compares_duplicate_of_rows(mk):
    rs = [mk.result("R001", "PCE", "15"), mk.result("R002", "PCE", "16", duplicate_of="R001")]
    out = run(mk, mk.bundle(rs), "contradiction")
    assert len(out) == 1 and set(out[0].record_ids) == {"R001", "R002"}


def test_contradiction_different_devices_or_aggregation_not_compared(mk):
    rs = [mk.result("R001", "PCE", "15", device="DV1"), mk.result("R002", "PCE", "16", device="DV2")]
    b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")])
    assert run(mk, b, "contradiction") == []
    rs = [mk.result("R001", "PCE", "15", agg="champion"), mk.result("R002", "PCE", "16", agg="mean")]
    assert run(mk, mk.bundle(rs), "contradiction") == []


def test_rounding_difference_is_rounding_not_contradiction(mk):  # 57.5 против 57.49
    rs = [mk.result("R001", "FF", "57.5", source_table="Table 1"),
          mk.result("R002", "FF", "57.49", source_figure="Fig. 1")]
    out = run_backend_rules(mk.bundle(rs), mk.rules_only("contradiction", "rounding"))
    assert mk.of(out, "contradiction") == []
    assert len(mk.of(out, "rounding")) == 1


# --- unit_inconsistency ------------------------------------------------------------------------------
def test_unit_inconsistency_ff_fraction_with_percent_sign(mk):
    out = run(mk, mk.bundle([mk.result("R001", "FF", "0.71", "%")]), "unit_inconsistency")
    assert len(out) == 1 and out[0].record_ids == ["R001"] and "0.71" in out[0].description


def test_unit_inconsistency_ff_percent_ok(mk):
    assert run(mk, mk.bundle([mk.result("R001", "FF", "71", "%")]), "unit_inconsistency") == []


def test_unit_inconsistency_pce_below_one_percent_is_valid(mk):  # D022: PCE 0.21 %
    assert run(mk, mk.bundle([mk.result("R001", "PCE", "0.21", "%")]), "unit_inconsistency") == []


def test_unit_inconsistency_when_normalization_failed(mk):
    r = mk.result("R001", "Voc", "1.1", "furlongs")
    r.meta.norm_status = "failed"
    r.meta.norm_issue = "unit_inconsistency"
    out = run(mk, mk.bundle([r]), "unit_inconsistency")
    assert len(out) == 1 and out[0].record_ids == ["R001"]


# --- voc_deficit_consistency -------------------------------------------------------------------------
@pytest.mark.parametrize("stated,fires", [("0.4", False), ("0.6", True)])
def test_voc_deficit_consistency(mk, stated, fires):  # Eg 1.5 - Voc 1.1 = 0.4
    rs = [mk.result("R001", "Voc", "1.1"), mk.result("R002", "Voc_deficit", stated)]
    out = run(mk, mk.bundle(rs, materials=[mk.material(eg="1.5")]), "voc_deficit_consistency")
    assert bool(out) is fires
    if fires:
        assert set(out[0].record_ids) == {"R001", "R002"} and stated in out[0].description


def test_voc_deficit_without_stated_deficit_or_bandgap_skips(mk):
    assert run(mk, mk.bundle([mk.result("R001", "Voc", "1.1")], materials=[mk.material(eg="1.5")]),
               "voc_deficit_consistency") == []
    rs = [mk.result("R001", "Voc", "1.1"), mk.result("R002", "Voc_deficit", "0.9")]
    assert run(mk, mk.bundle(rs, materials=[mk.material(eg=None)]), "voc_deficit_consistency") == []


# --- plausibility ------------------------------------------------------------------------------------
@pytest.mark.parametrize("metric,value,fires", [("FF", "87", True), ("FF", "75", False), ("PCE", "40", True),
                                                ("PCE", "15", False)])
def test_plausibility_record_levels_perovskite(mk, metric, value, fires):
    out = run(mk, mk.bundle([mk.result("R001", metric, value)]), "plausibility")
    assert bool(out) is fires
    if fires:
        assert out[0].record_ids == ["R001"] and out[0].severity == "warn" and value in out[0].description


# --- figure_mismatch ---------------------------------------------------------------------------------
@pytest.mark.parametrize("fig,fires", [("15.6", True), ("15.4", False), ("15.0", False)])
def test_figure_mismatch_threshold_3pct(mk, fig, fires):
    rs = [mk.result("R001", "PCE", "15.0", source_table="Table 1"),
          mk.result("R002", "PCE", fig, source_figure="Fig. 3", page=6)]
    out = run(mk, mk.bundle(rs), "figure_mismatch")
    assert bool(out) is fires
    if fires:
        assert set(out[0].record_ids) == {"R001", "R002"} and "15.0" in out[0].description


def test_figure_mismatch_needs_figure_values_of_same_device(mk):
    rs = [mk.result("R001", "PCE", "15", source_table="Table 1"),
          mk.result("R002", "PCE", "20", source_table="Table 2")]
    assert run(mk, mk.bundle(rs), "figure_mismatch") == []


# --- dedup_trap --------------------------------------------------------------------------------------
def _quad(mk, dev, start, vals=("1.0", "20", "75", "15")):
    return [mk.result(f"R{start + k:03d}", m, v, device=dev)
            for k, (m, v) in enumerate(zip(("Voc", "Jsc", "FF", "PCE"), vals))]


def test_dedup_trap_same_sets_different_devices(mk):
    rs = _quad(mk, "DV1", 1) + _quad(mk, "DV2", 5)
    b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")])
    out = run(mk, b, "dedup_trap")
    assert len(out) == 1 and out[0].severity == "info"
    assert {"R004", "R008"} <= set(out[0].record_ids)


def test_dedup_trap_different_sets_none(mk):
    rs = _quad(mk, "DV1", 1) + _quad(mk, "DV2", 5, vals=("1.0", "21", "75", "15.75"))
    b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")])
    assert run(mk, b, "dedup_trap") == []


def test_dedup_trap_same_device_not_a_trap(mk):
    assert run(mk, mk.bundle(_quad(mk, "DV1", 1) + _quad(mk, "DV1", 5)), "dedup_trap") == []


# --- общие свойства ----------------------------------------------------------------------------------
def test_all_backend_issues_have_russian_description_and_known_name(mk):
    rs = [*mk.jv_triple("12.5"), mk.result("R010", "PCE", 150, device="DV1", source_table="T")]
    out = run_backend_rules(mk.bundle(rs, materials=[mk.material(eg="0.5")]), mk.rules_only(*BACKEND_RULES))
    assert out
    for i in out:
        assert i.check_name in BACKEND_RULES and i.computed_by == "backend" and i.publication_id == mk.PID
        assert re.search(r"[а-яА-Я]{3,}", i.description or ""), i


# --- ambiguity: диапазон вместо значения -------------------------------------------------------------
def _range_result(mk):
    r = mk.result("R001", "PCE", "18.5", "%")
    r.raw_value, r.normalized_value, r.metric_value = "18.2–19.0", None, None
    r.meta.norm_issue = "ambiguity"
    return r


def test_ambiguity_for_range_value(mk):
    out = run(mk, mk.bundle([_range_result(mk)]), "ambiguity")
    assert len(out) == 1 and out[0].record_ids == ["R001"] and out[0].computed_by == "backend"
    assert "18.2–19.0" in out[0].description and "диапазон" in out[0].description


def test_ambiguity_skips_rejected_and_plain(mk):
    assert run(mk, mk.bundle([mk.result("R001", "PCE", "15")]), "ambiguity") == []
    r = _range_result(mk)
    r.duplicate_of = "R009"
    assert run(mk, mk.bundle([r]), "ambiguity") == []


# --- hysteresis_index: отрицательный HI из статьи ----------------------------------------------------
def test_hysteresis_reported_negative_hi_fires(mk):
    out = run(mk, _hi_bundle(mk, 20, 20.05, "-0.2"), "hysteresis_index")  # рассчитанный HI = -0.25 %, разница < 1 п.п.
    assert len(out) == 1 and "-0.2" in out[0].description
    out = run(mk, _hi_bundle(mk, 20, 19.9, "-0.2"), "hysteresis_index")  # расчёт +0.5 %, статья -0.2 -> отрицательный
    assert len(out) == 1 and "отрицательн" in out[0].description


# --- issue_id правил бэка ----------------------------------------------------------------------------
def test_backend_issue_id_format_and_stability(mk, solar_profile):
    from faktura.checks import check_paper

    def ids():
        b = mk.bundle([_range_result(mk), mk.result("R002", "PCE", 150)])
        return [(i.issue_id, i.key) for i in check_paper(b, solar_profile).issues]

    first, second = ids(), ids()
    assert first == second
    got = dict(first)
    assert "I-ambiguity-R001" in got and "I-range-R002" in got
    assert all(k.startswith("I-") for k in got.values())


def test_backend_issue_id_unique_per_paper(mk, solar_profile):
    from faktura.checks import check_paper

    rs = [mk.result("R001", "PCE", 150), mk.result("R002", "PCE", 160, device="DV1", meas="MS1")]
    out = check_paper(mk.bundle(rs), solar_profile).issues
    ids = [i.issue_id for i in out]
    assert len(set(ids)) == len(ids)


def test_issue_carries_quote_found_of_anchor(mk):
    b = mk.bundle([mk.result("R001", "PCE", 150, fragment="PCE = 150")])
    on = run_backend_rules(b, mk.rules_only("range"), quote=mk.FakeQuote({"PCE = 150"}))
    off = run_backend_rules(b, mk.rules_only("range"), quote=mk.FakeQuote())
    assert on[0].meta.quote_found is True and off[0].meta.quote_found is False
