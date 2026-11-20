"""LocalNormalizer.canon_material: канонизация формул (spec/06 §6.3 «Материалы»). REQ-045."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("text,expected", [
    ("MAPbI3", "CH3NH3PbI3"),
    ("MAPbI₃", "CH3NH3PbI3"),
    ("CH3NH3PbI3", "CH3NH3PbI3"),
    ("FAPbI3", "HC(NH2)2PbI3"),
    ("FAPbI₃", "HC(NH2)2PbI3"),
    ("HC(NH2)2PbI3", "HC(NH2)2PbI3"),
    ("CsPbI2Br", "CsPbI2Br"),
    ("CsPbI₂Br", "CsPbI2Br"),
])
def test_perovskite_expansion(nz, text, expected):
    assert nz.canon_material(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("CdTe", "CdTe"), ("Sb2Se3", "Sb2Se3"), ("Sb₂Se₃", "Sb2Se3"),
    ("CZTS", "Cu2ZnSnS4"), ("CIGS", "Cu(In,Ga)Se2"), ("Cu(In,Ga)Se2", "Cu(In,Ga)Se2"),
    ("Cu(In,Ga)Se₂", "Cu(In,Ga)Se2"),
])
def test_other_absorbers(nz, text, expected):
    assert nz.canon_material(text) == expected


def test_subscript_digits_to_ascii(nz):
    assert nz.canon_material("TiO₂") == "TiO2"
    assert nz.canon_material("SnO₂") == "SnO2"


def test_whitespace_tolerant(nz):
    assert nz.canon_material("  MAPbI3 ") == "CH3NH3PbI3"


@pytest.mark.parametrize("text", ["MAPbI3", "FAPbI3", "CsPbI2Br", "CdTe"])
def test_idempotent(nz, text):
    once = nz.canon_material(text)
    assert nz.canon_material(once) == once


def test_mixed_cation_not_mangled(nz):
    s = "Cs0.15FA0.85PbI2.85Br0.15"
    assert nz.canon_material(s) in (s, None)


@pytest.mark.parametrize("text", ["", "   ", "unobtainium blend"])
def test_unknown_none(nz, text):
    assert nz.canon_material(text) is None


@pytest.mark.data
def test_dev_gold_formulas_stable(nz, gold_materials):
    """Каноническая формула из эталона не меняется при повторной канонизации."""
    bad = []
    for r in gold_materials:
        f = r["formula_normalized"]
        if f:
            got = nz.canon_material(f)
            if got not in (None, f):
                bad.append((f, got))
    assert not bad, bad[:10]
