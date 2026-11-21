"""run_backend_rules: jv_consistency, rounding (spec/05 §5.2, К-21). Синтетика: Voc*Jsc*FF/Pin."""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from faktura.checks import run_backend_rules


def _jv(mk, bundle, **p):
    return mk.of(run_backend_rules(bundle, mk.rules_only("jv_consistency", **p)), "jv_consistency")




def test_inconsistent_triple_fires_with_four_ids_anchor_pce_first(mk):
    b = mk.bundle(mk.jv_triple("12.5"))
    out = _jv(mk, b)
    assert len(out) == 1
    i = out[0]
    assert set(i.record_ids) == {"R001", "R002", "R003", "R004"}
    assert i.record_ids[0] == "R004"  # якорь - PCE
    assert i.publication_id == mk.PID
    assert i.computed_by == "backend"
    assert i.severity == "warn"




# граница порога 3 %: |calc - PCE| / PCE > 3 % строго. Jsc=10, FF=100, Voc=1.03 -> calc 10.3 % при PCE 10.








































# rounding: расхождение объясняется округлением входов -> вместо jv_consistency ставится rounding


