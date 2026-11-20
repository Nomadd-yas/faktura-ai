"""Фикстуры модуля checks: фабрика синтетических статей `mk` и заглушка QuoteChecker.

Синтетический «стандартный» прибор: Voc=1.0 В, Jsc=20 мА/см2, FF=75 %, PCE=15 % при Pin=100 мВт/см2.
Расчёт: 1.0 * 20 * 0.75 / 100 = 15 %.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from faktura.domain.profile import ExtractionProfile, ProfileField, RuleConfig, builtin_solar_profile
from faktura.domain.records import (Device, IssueRecord, Material, Measurement, PaperBundle, Publication, Result)
from faktura.domain.tables import CHECK_CATALOG

PID = "T001"
NUM = (Decimal, int, float, str)


def D(x: Any) -> Decimal | None:
    return None if x is None else Decimal(str(x))


class FakeQuote:
    """QuoteChecker: found - набор найденных фрагментов; всё остальное - False; None - не проверяется."""

    def __init__(self, found: set[str] | None = None, answer: bool | None = False):
        self.found = found or set()
        self.answer = answer
        self.calls: list[tuple[int | None, str | None]] = []

    def check(self, page, fragment):
        self.calls.append((page, fragment))
        if fragment in self.found:
            return True
        return self.answer


def result(rid: str, metric: str, value, unit: str | None = None, *, device: str | None = "DV1",
           meas: str | None = "MS1", agg: str | None = "champion", page: int | None = 5,
           fragment: str | None = None, order: int | None = None, **kw) -> Result:
    unit = unit or {"PCE": "%", "FF": "%", "Voc": "V", "Jsc": "mA/cm2", "Jsc_EQE": "mA/cm2", "HI": "%",
                    "Voc_deficit": "V", "retained_PCE": "%", "Rs": "Ohm*cm2", "Rsh": "Ohm*cm2"}.get(metric)
    r = Result(publication_id=PID, result_id=rid, device_id=device, measurement_id=meas, metric_name=metric,
               metric_value=D(value), metric_unit=unit, aggregation=agg, raw_value=str(value), raw_unit=unit,
               normalized_value=D(value), normalized_unit=unit, result_origin="primary",
               source_page=page, source_fragment=fragment if fragment is not None else f"{metric} = {value}",
               extraction_status="extracted", **kw)
    r.meta.ml_order = order if order is not None else int(rid[1:]) if rid[1:].isdigit() else 0
    return r


def device(did: str = "DV1", *, material: str | None = "MT1", label: str | None = None, area=0.09,
           dtype: str = "cell") -> Device:
    return Device(publication_id=PID, device_id=did, absorber_material_id=material, device_label=label or did,
                  device_type=dtype, active_area=D(area), area_unit="cm2" if area is not None else None)


def material(mid: str = "MT1", *, eg=None, mclass: str = "perovskite") -> Material:
    return Material(publication_id=PID, material_id=mid, material_class=mclass, bandgap_value=D(eg),
                    bandgap_unit="eV" if eg is not None else None)


def measurement(mid: str = "MS1", *, devices=("DV1",), mtype: str = "J-V", spectrum: str | None = "AM1.5G",
                intensity=100, unit: str | None = "mW/cm2", scan: str | None = "reverse", **kw) -> Measurement:
    return Measurement(publication_id=PID, measurement_id=mid, device_id=list(devices), measurement_type=mtype,
                       spectrum=spectrum, light_intensity=D(intensity), intensity_unit=unit if intensity is not None else None,
                       scan_direction=scan, **kw)


def bundle(results=(), devices=None, materials=None, measurements=None, issues=(), title="Test paper") -> PaperBundle:
    return PaperBundle(
        publication=Publication(publication_id=PID, title=title, year=2024),
        materials=list(materials if materials is not None else [material()]),
        devices=list(devices if devices is not None else [device()]),
        measurements=list(measurements if measurements is not None else [measurement()]),
        results=list(results), issues=list(issues))


def jv_triple(pce, voc=1.0, jsc=20, ff=75, *, agg: str | None = "champion", device_id="DV1", meas="MS1",
              start: int = 1, **kw) -> list[Result]:
    """R001 Voc, R002 Jsc, R003 FF, R004 PCE (PCE - якорь замечания jv_consistency)."""
    n = start
    return [result(f"R{n:03d}", "Voc", voc, agg=agg, device=device_id, meas=meas, page=4, **kw),
            result(f"R{n + 1:03d}", "Jsc", jsc, agg=agg, device=device_id, meas=meas, page=4, **kw),
            result(f"R{n + 2:03d}", "FF", ff, agg=agg, device=device_id, meas=meas, page=4, **kw),
            result(f"R{n + 3:03d}", "PCE", pce, agg=agg, device=device_id, meas=meas, page=5,
                   fragment=f"PCE of {pce}%", **kw)]


def issue(check: str, ids=(), *, desc: str = "описание", page=None, fragment=None, by: str = "ml",
          numbers: dict | None = None, severity=None, confidence=None) -> IssueRecord:
    return IssueRecord(publication_id=PID, check_name=check, record_ids=list(ids), description=desc,
                       source_page=page, source_fragment=fragment, computed_by=by, numbers=numbers or {},
                       severity=severity, confidence=confidence)


def rules_only(*names: str, **params) -> dict[str, RuleConfig]:
    """Правила профиля solar: включены только перечисленные (остальные enabled=False)."""
    prof = builtin_solar_profile()
    out: dict[str, RuleConfig] = {}
    for n, rc in prof.rules.items():
        out[n] = rc.model_copy(update={"enabled": n in names})
    for n in names:
        if n not in out:
            out[n] = RuleConfig(check_name=n, enabled=True)
        if params:
            out[n] = out[n].model_copy(update={"params": {**out[n].params, **params}})
    return out


def profile_all_rules() -> ExtractionProfile:
    prof = builtin_solar_profile()
    return prof


def small_profile(*keys: str) -> ExtractionProfile:
    """Профиль из метрик results (и колонок через 'table.column')."""
    fields = []
    for k in keys:
        if "." in k:
            t, c = k.split(".")
            fields.append(ProfileField(key=c, table=t, column=c))
        else:
            fields.append(ProfileField(key=k, label=k))
    return ExtractionProfile(profile_id="prf_test", name="test", fields=fields)


def of(issues: list[IssueRecord], check: str) -> list[IssueRecord]:
    return [i for i in issues if i.check_name == check]


def keyset(issues) -> set[tuple[str, str, frozenset[str]]]:
    return {(i.publication_id, i.check_name, frozenset(i.record_ids)) for i in issues}


@pytest.fixture
def mk() -> SimpleNamespace:
    return SimpleNamespace(PID=PID, D=D, FakeQuote=FakeQuote, result=result, device=device, material=material,
                           measurement=measurement, bundle=bundle, jv_triple=jv_triple, issue=issue,
                           rules_only=rules_only, small_profile=small_profile, of=of, keyset=keyset,
                           profile=builtin_solar_profile, CHECK_CATALOG=CHECK_CATALOG)


@pytest.fixture
def solar_profile() -> ExtractionProfile:
    return builtin_solar_profile()
