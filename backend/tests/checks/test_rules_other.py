"""run_backend_rules: voc_limit, range, eqe_consistency, champion_vs_mean, hysteresis_index, contradiction,
unit_inconsistency, voc_deficit_consistency, plausibility, figure_mismatch, dedup_trap (spec/05 §5.2)."""
from __future__ import annotations

import re

import pytest

from faktura.checks import BACKEND_RULES, run_backend_rules


def run(mk, bundle, name, **p):
    return mk.of(run_backend_rules(bundle, mk.rules_only(name, **p)), name)




# --- voc_limit ---------------------------------------------------------------------------------------




# --- range -------------------------------------------------------------------------------------------










# --- eqe_consistency ---------------------------------------------------------------------------------






# --- champion_vs_mean --------------------------------------------------------------------------------






# --- hysteresis_index --------------------------------------------------------------------------------
def _hi_bundle(mk, rev, fwd, hi=None):
    rs = [mk.result("R001", "PCE", rev, meas="MS1"), mk.result("R002", "PCE", fwd, meas="MS2")]
    if hi is not None:
        rs.append(mk.result("R003", "HI", hi, meas="MS1"))
    ms = [mk.measurement("MS1", scan="reverse"), mk.measurement("MS2", scan="forward")]
    return mk.bundle(rs, measurements=ms)








# --- contradiction (BE-часть) ------------------------------------------------------------------------
def test_contradiction_same_value_different_places(mk):
    rs = [mk.result("R002", "PCE", "3.85", source_table="Table 4"),
          mk.result("R003", "PCE", "5.25", source_figure="Fig. 10", page=13)]
    out = run(mk, mk.bundle(rs), "contradiction")
    assert len(out) == 1 and set(out[0].record_ids) == {"R002", "R003"} and out[0].severity == "error"
    assert "3.85" in out[0].description and "5.25" in out[0].description










# --- unit_inconsistency ------------------------------------------------------------------------------








# --- voc_deficit_consistency -------------------------------------------------------------------------




# --- plausibility ------------------------------------------------------------------------------------


# --- figure_mismatch ---------------------------------------------------------------------------------




# --- dedup_trap --------------------------------------------------------------------------------------
def _quad(mk, dev, start, vals=("1.0", "20", "75", "15")):
    return [mk.result(f"R{start + k:03d}", m, v, device=dev)
            for k, (m, v) in enumerate(zip(("Voc", "Jsc", "FF", "PCE"), vals))]








# --- общие свойства ----------------------------------------------------------------------------------


# --- ambiguity: диапазон вместо значения -------------------------------------------------------------
def _range_result(mk):
    r = mk.result("R001", "PCE", "18.5", "%")
    r.raw_value, r.normalized_value, r.metric_value = "18.2–19.0", None, None
    r.meta.norm_issue = "ambiguity"
    return r






# --- hysteresis_index: отрицательный HI из статьи ----------------------------------------------------


# --- issue_id правил бэка ----------------------------------------------------------------------------




