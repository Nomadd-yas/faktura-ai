"""LocalNormalizer.match_metric: синонимы показателей (spec/06 §6.3). REQ-044."""
from __future__ import annotations

import pytest

from faktura.domain import EVAL_METRICS


@pytest.mark.parametrize("text,expected", [
    ("V_oc", "Voc"), ("VOC", "Voc"), ("Voc", "Voc"), ("open-circuit voltage", "Voc"),
    ("Open-Circuit Voltage", "Voc"), ("напряжение холостого хода", "Voc"),
    ("PCE", "PCE"), ("power conversion efficiency", "PCE"), ("η", "PCE"), ("КПД", "PCE"),
    ("efficiency", "PCE"),
    ("J_SC", "Jsc"), ("JSC", "Jsc"), ("short-circuit current density", "Jsc"),
    ("FF", "FF"), ("fill factor", "FF"), ("фактор заполнения", "FF"),
    ("Rs", "Rs"), ("R_s", "Rs"), ("series resistance", "Rs"),
    ("Rsh", "Rsh"), ("R_sh", "Rsh"), ("Rp", "Rsh"), ("shunt resistance", "Rsh"),
    ("HI", "HI"), ("hysteresis index", "HI"),
    ("integrated Jsc", "Jsc_EQE"), ("Jsc from EQE", "Jsc_EQE"), ("integrated current density", "Jsc_EQE"),
    ("stabilized PCE", "stabilized_PCE"), ("SPO", "stabilized_PCE"), ("steady-state efficiency", "stabilized_PCE"),
    ("MPP efficiency", "stabilized_PCE"),
    ("T80", "T80"), ("T₈₀", "T80"),
    ("retained PCE", "retained_PCE"), ("normalized PCE", "retained_PCE"),
])
def test_synonyms(nz, text, expected):
    assert nz.match_metric(text) == expected


@pytest.mark.parametrize("name", EVAL_METRICS)
def test_all_eval_metrics_returned_as_is(nz, name):
    assert nz.match_metric(name) == name


@pytest.mark.parametrize("name", ["Voc_deficit", "Jmpp", "Vmpp", "AVT", "CRI", "LUE"])
def test_extra_metrics_as_in_paper(nz, name):
    assert nz.match_metric(name) in (name, None)


def test_case_insensitive(nz):
    assert nz.match_metric("fill FACTOR") == "FF"
    assert nz.match_metric("pce") == "PCE"


@pytest.mark.parametrize("text", ["SPACE", "experience", "PCEs_total_xyz", "coffee", "stuff"])
def test_whole_word_only(nz, text):
    """PCE/FF внутри слова не срабатывают."""
    assert nz.match_metric(text) is None


def test_whitespace_stripped(nz):
    assert nz.match_metric("  Voc  ") == "Voc"


@pytest.mark.parametrize("text", ["", "   ", "thickness", "random words"])
def test_unmatched_returns_none(nz, text):
    assert nz.match_metric(text) is None


def test_phrase_with_unit_suffix(nz):
    assert nz.match_metric("fill factor (%)") == "FF"
