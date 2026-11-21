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




def test_mv_to_v_exact_decimal(nz):
    assert str(n(nz, "Voc", "1197", "mV").normalized_value) == "1.197"






# --- разбор значения внутри normalize ---














# --- доли и проценты §6.5 ---
def test_ff_fraction_with_percent_unit_inconsistency(nz):
    """'FF 0.71 %': нормализуем по шкале (71 %) и ставим unit_inconsistency (D001)."""
    r = n(nz, "FF", "0.71", "%")
    assert r.normalized_value == D("71")
    assert r.normalized_unit == "%"
    assert r.issue == "unit_inconsistency"










# --- отказы: не восстанавливаем по догадке §6.4 ---










# --- passthrough безразмерных нестандартных метрик ---










