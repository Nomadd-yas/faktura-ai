"""LocalNormalizer.parse_number: разбор числа (spec/06 §6.2 шаги 1-3). REQ-042."""
from __future__ import annotations

from decimal import Decimal

import pytest


@pytest.mark.parametrize("text,expected", [
    ("18.6", "18.6"),
    ("18,6", "18.6"),          # десятичная запятая
    ("  18.6  ", "18.6"),
    ("-0.5", "-0.5"),
    ("−0.5", "-0.5"),     # Unicode-минус
    ("1197", "1197"),
    ("0", "0"),
])
def test_single_number(nz, text, expected):
    r = nz.parse_number(text)
    assert r.ok is True
    assert r.value == Decimal(expected)
    assert isinstance(r.value, Decimal)
    assert r.spread is None and r.is_range is False


def test_nbsp_thousands_removed(nz):
    r = nz.parse_number("1 197")
    assert r.ok and r.value == Decimal("1197")


def test_no_float_artifacts(nz):
    assert str(nz.parse_number("0.1").value) == "0.1"
    assert str(nz.parse_number("0.71").value) == "0.71"


def test_trailing_zeros_kept(nz):
    """Не округляем сверх исходной точности."""
    assert str(nz.parse_number("1.10").value) == "1.10"


@pytest.mark.parametrize("text,val,spread", [
    ("18.6 ± 0.4", "18.6", "0.4"),
    ("18.6±0.4", "18.6", "0.4"),
    ("305 ± 32", "305", "32"),
    ("18,6 ± 0,4", "18.6", "0.4"),
])
def test_spread(nz, text, val, spread):
    r = nz.parse_number(text)
    assert r.ok and r.value == Decimal(val) and r.spread == Decimal(spread)


@pytest.mark.parametrize("text", ["18.2-19.0", "18.2–19.0", "18.2 – 19.0", "18.2—19.0"])
def test_range_is_not_a_number(nz, text):
    r = nz.parse_number(text)
    assert r.is_range is True
    assert r.ok is False
    assert r.value is None


def test_negative_number_is_not_range(nz):
    r = nz.parse_number("-18.2")
    assert r.is_range is False and r.value == Decimal("-18.2")


def test_percent_sign(nz):
    r = nz.parse_number("71 %")
    assert r.ok and r.value == Decimal("71") and r.is_percent is True


def test_rest_holds_unit_tail(nz):
    r = nz.parse_number("1197 mV")
    assert r.ok and r.value == Decimal("1197")
    assert (r.rest or "").strip() == "mV"


@pytest.mark.parametrize("text", ["", "   ", "abc", "n/a", "—", "±"])
def test_not_a_number(nz, text):
    r = nz.parse_number(text)
    assert r.ok is False
    assert r.value is None
