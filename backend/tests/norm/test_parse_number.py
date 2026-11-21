"""LocalNormalizer.parse_number: разбор числа (spec/06 §6.2 шаги 1-3). REQ-042."""
from __future__ import annotations

from decimal import Decimal

import pytest


@pytest.mark.parametrize("text,expected", [
    ("18.6", "18.6"),
    ("18,6", "18.6"),          # десятичная запятая
    ("−0.5", "-0.5"),     # Unicode-минус
])
def test_single_number(nz, text, expected):
    r = nz.parse_number(text)
    assert r.ok is True
    assert r.value == Decimal(expected)
    assert isinstance(r.value, Decimal)
    assert r.spread is None and r.is_range is False


















