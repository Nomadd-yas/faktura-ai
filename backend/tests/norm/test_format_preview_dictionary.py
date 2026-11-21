"""format_display, preview, dictionary (spec/06 §6.2 п.7). REQ-048, REQ-028."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

D = Decimal


def _plain(s: str) -> str:
    return s.replace(" ", " ").replace(" ", " ")














# --- preview ---




@pytest.mark.parametrize("param,raw,val,unit", [
    ("pce", "18,6 %", "18.6", "%"),
    ("ff", "0.71", "71", "%"),
])
def test_preview_ok(nz, param, raw, val, unit):
    p = nz.preview("solar", param, raw)
    assert p.ok is True and p.value == D(val) and p.unit == unit






# --- dictionary / версии ---








