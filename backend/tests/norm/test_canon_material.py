"""LocalNormalizer.canon_material: канонизация формул (spec/06 §6.3 «Материалы»). REQ-045."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("text,expected", [
    ("MAPbI3", "CH3NH3PbI3"),
    ("FAPbI₃", "HC(NH2)2PbI3"),
])
def test_perovskite_expansion(nz, text, expected):
    assert nz.canon_material(text) == expected














