"""format_display, preview, dictionary (spec/06 §6.2 п.7). REQ-048, REQ-028."""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

D = Decimal


def _plain(s: str) -> str:
    return s.replace(" ", " ").replace(" ", " ")


@pytest.mark.parametrize("value,unit,digits,expected", [
    (D("1.1"), "V", 2, "1,10 В"),
    (D("0.745"), "V", 2, "0,75 В"),          # «как в школе»: ROUND_HALF_UP
    (D("0.735"), "V", 2, "0,74 В"),
    (D("18.6"), "%", 1, "18,6 %"),
    (D("22.1"), "mA/cm2", 1, "22,1 мА/см²"),
    (D("3.5"), "Ohm*cm2", 1, "3,5 Ом·см²"),
    (D("1.55"), "eV", 2, "1,55 эВ"),
    (D("500"), "h", 0, "500 ч"),
    (D("100"), "nm", 0, "100 нм"),
    (D("0.09"), "cm2", 2, "0,09 см²"),
])
def test_format_display(nz, value, unit, digits, expected):
    assert _plain(nz.format_display(value, unit, digits=digits)) == expected


def test_digits_none_keeps_precision(nz):
    assert _plain(nz.format_display(D("1.197"), "V")) == "1,197 В"
    assert _plain(nz.format_display(D("71"), "%")) == "71 %"


def test_comma_not_dot(nz):
    assert "." not in nz.format_display(D("1.1"), "V", digits=2)


def test_none_value(nz):
    assert nz.format_display(None, "V") in ("", "—", "-")


def test_no_unit(nz):
    assert nz.format_display(D("89"), None) == "89"
    assert nz.format_display(D("0.6"), "") == "0,6"


def test_negative(nz):
    assert nz.format_display(D("-0.05"), "V", digits=2).startswith(("-0,05", "−0,05"))


# --- preview ---
def test_preview_voc_mv(nz):
    p = nz.preview("solar", "voc", "1100 мВ")
    assert p.ok is True
    assert p.value == D("1.1")
    assert p.unit == "V"
    assert p.text and "1,1" in p.text


def test_preview_kv_rejected(nz):
    p = nz.preview("solar", "voc", "1,1 кВ")
    assert p.ok is False
    assert p.value is None


@pytest.mark.parametrize("param,raw,val,unit", [
    ("pce", "18,6 %", "18.6", "%"),
    ("ff", "0.71", "71", "%"),
    ("jsc", "22.1 мА/см2", "22.1", "mA/cm2"),
    ("voc", "1.1 V", "1.1", "V"),
])
def test_preview_ok(nz, param, raw, val, unit):
    p = nz.preview("solar", param, raw)
    assert p.ok is True and p.value == D(val) and p.unit == unit


@pytest.mark.parametrize("raw", ["", "abc", "18.2-19.0"])
def test_preview_garbage_not_ok(nz, raw):
    assert nz.preview("solar", "pce", raw).ok is False


def test_preview_unknown_param_no_crash(nz):
    assert nz.preview("solar", "no_such_param", "5").ok in (True, False)


# --- dictionary / версии ---
def test_versions(nz):
    from faktura.norm import DICTIONARY_VERSION, NORM_VERSION
    assert isinstance(NORM_VERSION, str) and NORM_VERSION and NORM_VERSION != "0.0.0-skeleton"
    assert isinstance(DICTIONARY_VERSION, str) and DICTIONARY_VERSION
    assert nz.version == NORM_VERSION
    assert nz.dictionary_version == DICTIONARY_VERSION


def test_dictionary_sections(nz):
    d = nz.dictionary()
    assert d.version == nz.dictionary_version
    assert d.metrics and d.units and d.materials and d.layer_functions and d.spectra


def test_dictionary_metrics_cover_eval_metrics(nz):
    from faktura.domain import EVAL_METRICS
    names = {m.get("metric_name") or m.get("name") for m in nz.dictionary().metrics}
    assert set(EVAL_METRICS) <= names


def test_dictionary_json_serializable_and_stable(nz):
    a = nz.dictionary().model_dump(mode="json")
    json.dumps(a)
    assert a == nz.dictionary().model_dump(mode="json")


def test_data_files_shipped():
    import faktura.norm as m
    data = Path(m.__file__).parent / "data"
    assert data.is_dir() and list(data.glob("*.json"))
