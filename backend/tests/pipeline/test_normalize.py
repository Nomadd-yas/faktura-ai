"""apply_normalization: 8 шагов, только через протокол Normalizer (план 5.6)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.pipeline import apply_normalization


def norm(bundle, fake_norm, profile):
    return apply_normalization(bundle, fake_norm, profile)


def res(b, rid):
    return next(r for r in b.results if r.result_id == rid)


def lay(b, lid):
    return next(r for r in b.layers if r.layer_id == lid)


# --- шаг 1: match_metric ---------------------------------------------------------------------

def test_step1_metric_name_canonicalised_with_trace(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R1")
    assert r.metric_name == "Voc"
    assert ("V_oc", "Voc") in [(t.before, t.after) for t in r.meta.norm_trace]






# --- шаг 2: normalize results ------------------------------------------------------------------









# --- шаг 3: spread_value --------------------------------------------------------------------------









# --- шаг 4: materials ------------------------------------------------------------------------------







# --- шаг 5: layers ------------------------------------------------------------------------------------











# --- шаг 6: devices -------------------------------------------------------------------------------------







# --- шаг 7: measurements ------------------------------------------------------------------------------------





# --- шаг 8: единица только при значении -------------------------------------------------------------------------







# --- общие свойства ------------------------------------------------------------------------------------------







# --- (data) шаги 4-8 на эталоне ничего не меняют ------------------------------------------------------------



