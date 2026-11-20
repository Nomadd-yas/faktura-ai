"""LocalNormalizer.normalize_field: bandgap (eV), thickness (nm), area (cm2) (spec/06 §6.1). REQ-043."""
from __future__ import annotations

from decimal import Decimal

import pytest

D = Decimal


@pytest.mark.parametrize("table,column,raw,unit,val,tunit,status", [
    ("materials", "bandgap_value", "1.55", "eV", "1.55", "eV", "ok"),
    ("layers", "layer_thickness", "100", "nm", "100", "nm", "ok"),
    ("layers", "layer_thickness", "0.5", "µm", "500", "nm", "converted"),
    ("layers", "layer_thickness", "0.5", "um", "500", "nm", "converted"),
    ("layers", "layer_thickness", "0.5", "μm", "500", "nm", "converted"),
    ("layers", "layer_thickness", "1000", "Å", "100", "nm", "converted"),
    ("layers", "layer_thickness", "1", "mm", "1000000", "nm", "converted"),
    ("devices", "active_area", "0.09", "cm2", "0.09", "cm2", "ok"),
    ("devices", "active_area", "9", "mm2", "0.09", "cm2", "converted"),
])
def test_field_conversions(nz, table, column, raw, unit, val, tunit, status):
    r = nz.normalize_field(table, column, raw, unit)
    assert r.normalized_value == D(val)
    assert r.normalized_unit == tunit
    assert r.norm_status == status


def test_bandgap_nm_not_converted(nz):
    """Край поглощения в nm не пересчитывается (1240/lambda - вывод)."""
    r = nz.normalize_field("materials", "bandgap_value", "775", "nm")
    assert r.norm_status == "failed"
    assert r.normalized_value is None


def test_thickness_no_unit_failed(nz):
    r = nz.normalize_field("layers", "layer_thickness", "100", None)
    assert r.normalized_value is None and r.norm_status == "failed"


def test_thickness_range_ambiguity(nz):
    r = nz.normalize_field("layers", "layer_thickness", "100-200", "nm")
    assert r.normalized_value is None and r.issue == "ambiguity"


def test_thickness_with_spread(nz):
    r = nz.normalize_field("layers", "layer_thickness", "100 ± 10", "nm")
    assert r.normalized_value == D("100") and r.spread_value == D("10")


@pytest.mark.parametrize("raw", [None, ""])
def test_field_empty_value(nz, raw):
    assert nz.normalize_field("devices", "active_area", raw, "cm2").normalized_value is None


def test_unknown_field_does_not_invent_value(nz):
    r = nz.normalize_field("devices", "no_such_column", "5", "cm2")
    assert r.norm_status in ("not_applicable", "failed")


def test_no_float_artifacts_area(nz):
    assert nz.normalize_field("devices", "active_area", "6", "mm2").normalized_value == D("0.06")
