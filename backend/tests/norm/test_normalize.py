"""LocalNormalizer.normalize: единицы и множители spec/06 §6.1, статусы, issue. REQ-042, REQ-043."""
from __future__ import annotations

from decimal import Decimal

import pytest

D = Decimal


def n(nz, metric, raw, unit, header=None):
    return nz.normalize(metric, raw, unit, header)


# (метрика, raw, raw_unit, значение, целевая единица, статус) - множители §6.1
MULTIPLIERS = [
    ("PCE", "18.6", "%", "18.6", "%", "ok"),
    ("PCE", "0.186", "fraction", "18.6", "%", "converted"),
    ("stabilized_PCE", "17.2", "%", "17.2", "%", "ok"),
    ("retained_PCE", "0.9", "fraction", "90", "%", "converted"),
    ("HI", "0.05", "fraction", "5", "%", "converted"),
    ("FF", "71", "%", "71", "%", "ok"),
    ("FF", "0.71", "fraction", "71", "%", "converted"),
    ("Voc", "1.1", "V", "1.1", "V", "ok"),
    ("Voc", "1197", "mV", "1.197", "V", "converted"),
    ("Voc_deficit", "350", "mV", "0.35", "V", "converted"),
    ("Vmpp", "950", "mV", "0.95", "V", "converted"),
    ("Jsc", "22.1", "mA/cm2", "22.1", "mA/cm2", "ok"),
    ("Jsc", "22.1", "mA cm-2", "22.1", "mA/cm2", "ok"),
    ("Jsc", "22.1", "mAcm-2", "22.1", "mA/cm2", "ok"),
    ("Jsc", "221", "A/m2", "22.1", "mA/cm2", "converted"),
    ("Jsc", "0.0221", "A/cm2", "22.1", "mA/cm2", "converted"),
    ("Jsc_EQE", "21.5", "mA/cm2", "21.5", "mA/cm2", "ok"),
    ("Jmpp", "20.5", "mA/cm2", "20.5", "mA/cm2", "ok"),
    ("Rs", "3.5", "Ohm*cm2", "3.5", "Ohm*cm2", "ok"),
    ("Rs", "3.5", "Ω cm2", "3.5", "Ohm*cm2", "ok"),
    ("Rs", "3.5", "Ω·cm²", "3.5", "Ohm*cm2", "ok"),
    ("Rsh", "1.2", "kOhm*cm2", "1200", "Ohm*cm2", "converted"),
    ("T80", "500", "h", "500", "h", "ok"),
    ("T80", "120", "min", "2", "h", "converted"),
    ("T80", "10", "day", "240", "h", "converted"),
    ("T80", "10", "d", "240", "h", "converted"),
]


@pytest.mark.parametrize("metric,raw,unit,val,tunit,status", MULTIPLIERS)
def test_multipliers(nz, metric, raw, unit, val, tunit, status):
    r = n(nz, metric, raw, unit)
    assert r.norm_status == status
    assert r.normalized_unit == tunit
    assert r.normalized_value == D(val)
    assert isinstance(r.normalized_value, D)
    assert r.issue is None


def test_mv_to_v_exact_decimal(nz):
    assert str(n(nz, "Voc", "1197", "mV").normalized_value) == "1.197"


@pytest.mark.parametrize("raw,expected", [("0.71", "71"), ("0.29", "29"), ("0.58", "58"), ("0.57", "57"),
                                          ("0.7", "70"), ("0.835", "83.5")])
def test_fraction_no_float_artifacts(nz, raw, expected):
    assert n(nz, "FF", raw, "fraction").normalized_value == D(expected)


@pytest.mark.parametrize("raw,expected", [("1.1", "0.0011"), ("305", "0.305"), ("1", "0.001")])
def test_mv_no_float_artifacts(nz, raw, expected):
    assert n(nz, "Voc", raw, "mV").normalized_value == D(expected)


# --- разбор значения внутри normalize ---
def test_plus_minus_spread_percent(nz):
    r = n(nz, "PCE", "18.6 ± 0.4", "%")
    assert r.normalized_value == D("18.6")
    assert r.spread_value == D("0.4")


def test_spread_in_paper_units_not_converted(nz):
    """spread после ± - в единицах статьи (mV), значение - в целевых (V)."""
    r = n(nz, "Voc", "305 ± 32", "mV")
    assert r.normalized_value == D("0.305")
    assert r.normalized_unit == "V"
    assert r.spread_value == D("32")
    assert r.norm_status == "converted"


def test_no_spread_is_none(nz):
    assert n(nz, "PCE", "18.6", "%").spread_value is None


@pytest.mark.parametrize("raw", ["1 197", " 1197 "])
def test_nbsp_and_spaces(nz, raw):
    assert n(nz, "Voc", raw, "mV").normalized_value == D("1.197")


def test_unicode_minus(nz):
    assert n(nz, "Voc_deficit", "−0.05", "V").normalized_value == D("-0.05")


def test_decimal_comma(nz):
    assert n(nz, "PCE", "18,6", "%").normalized_value == D("18.6")


@pytest.mark.parametrize("raw", ["18.2-19.0", "18.2–19.0"])
def test_range_failed_ambiguity(nz, raw):
    r = n(nz, "PCE", raw, "%")
    assert r.normalized_value is None
    assert r.norm_status == "failed"
    assert r.issue == "ambiguity"


# --- доли и проценты §6.5 ---
def test_ff_fraction_with_percent_unit_inconsistency(nz):
    """'FF 0.71 %': нормализуем по шкале (71 %) и ставим unit_inconsistency (D001)."""
    r = n(nz, "FF", "0.71", "%")
    assert r.normalized_value == D("71")
    assert r.normalized_unit == "%"
    assert r.issue == "unit_inconsistency"


def test_ff_without_unit_le_one_scaled(nz):
    r = n(nz, "FF", "0.71", None)
    assert r.normalized_value == D("71")
    assert r.normalized_unit == "%"


def test_ff_percent_normal_has_no_issue(nz):
    assert n(nz, "FF", "71", "%").issue is None


def test_header_unit_used_when_raw_unit_empty(nz):
    r = n(nz, "Voc", "1197", None, header="mV")
    assert r.normalized_value == D("1.197")
    assert r.normalized_unit == "V"


def test_raw_unit_wins_over_header(nz):
    assert n(nz, "Voc", "1.1", "V", header="mV").normalized_value == D("1.1")


# --- отказы: не восстанавливаем по догадке §6.4 ---
def test_voc_without_unit_failed(nz):
    r = n(nz, "Voc", "1.1", None)
    assert r.norm_status == "failed"
    assert r.normalized_value is None
    assert r.issue == "unit_inconsistency"


def test_voc_unknown_unit_failed(nz):
    r = n(nz, "Voc", "1.1", "kV")
    assert r.norm_status == "failed" and r.normalized_value is None and r.issue == "unit_inconsistency"


def test_rs_ohm_without_area_not_converted(nz):
    r = n(nz, "Rs", "5", "Ohm")
    assert r.norm_status == "failed"
    assert r.normalized_value is None
    assert r.issue == "unit_inconsistency"


@pytest.mark.parametrize("raw", ["", None, "abc", "n/a"])
def test_non_numeric_raw_not_normalized(nz, raw):
    r = n(nz, "PCE", raw, "%")
    assert r.normalized_value is None
    assert r.norm_status in ("failed", "not_applicable")


@pytest.mark.parametrize("raw", ["--", "1..2", "\x00"])
def test_garbage_does_not_raise(nz, raw):
    r = n(nz, "PCE", raw, "%")
    assert r.normalized_value is None


# --- passthrough безразмерных нестандартных метрик ---
@pytest.mark.parametrize("metric,raw", [("CRI", "89"), ("CRI", "74"), ("bifaciality_factor", "0.6")])
def test_passthrough_dimensionless(nz, metric, raw):
    r = n(nz, metric, raw, None)
    assert r.norm_status == "passthrough"
    assert r.issue is None
    assert r.normalized_value == D(raw)
    assert r.normalized_unit in (None, "")


def test_passthrough_empty_unit_string(nz):
    r = n(nz, "CRI", "89", "")
    assert r.norm_status == "passthrough" and r.issue is None


@pytest.mark.parametrize("metric,raw,val", [("AVT", "45", "45"), ("LUE", "3.5", "3.5")])
def test_extra_metrics_percent(nz, metric, raw, val):
    r = n(nz, metric, raw, "%")
    assert r.normalized_value == D(val) and r.normalized_unit == "%"


def test_standard_metric_without_unit_not_passthrough(nz):
    """Passthrough - только для нестандартных безразмерных метрик, не для Voc."""
    assert n(nz, "Voc", "1.1", None).norm_status != "passthrough"
    assert n(nz, "Jsc", "22", None).norm_status != "passthrough"


def test_trace_present_on_conversion(nz):
    assert len(n(nz, "Voc", "1197", "mV").trace) >= 1


def test_idempotent_on_normalized_output(nz):
    r1 = n(nz, "Voc", "1197", "mV")
    r2 = n(nz, "Voc", str(r1.normalized_value), r1.normalized_unit)
    assert r2.normalized_value == r1.normalized_value
    assert r2.norm_status == "ok"
