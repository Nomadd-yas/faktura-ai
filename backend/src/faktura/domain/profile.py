"""Профиль извлечения и встроенный профиль solar (план 4.3)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .tables import CHECK_CATALOG, EVAL_METRICS, TARGET_UNITS

BUILTIN_SOLAR_ID = "prf_solar_tz_v1"
BUILTIN_SOLAR_ALIAS = "tz-solar-v1"


class ProfileField(BaseModel):
    key: str                      # metric_name (PCE) или колонка (bandgap_value)
    label: str | None = None
    table: str = "results"
    column: str | None = None     # None для метрик results
    unit: str | None = None       # целевая единица
    synonyms: list[str] = Field(default_factory=list)
    evaluated: bool = False       # участвует в оценке (11 метрик ТЗ)
    enabled: bool = True


class RuleConfig(BaseModel):
    check_name: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)


class ProfileFlags(BaseModel):
    export_absent: bool = False
    ml_issue_min_confidence: float = 0.5
    backend_rules: bool = True  # выключатель всех правил бэка (строгий roundtrip)


class ExtractionProfile(BaseModel):
    profile_id: str
    version: int = 1
    name: str
    collection: str = "solar"
    fields: list[ProfileField] = Field(default_factory=list)
    rules: dict[str, RuleConfig] = Field(default_factory=dict)
    builtin: bool = False
    flags: ProfileFlags = Field(default_factory=ProfileFlags)

    def metric_names(self) -> list[str]:
        return [f.key for f in self.fields if f.table == "results" and f.column is None]


_METRIC_SYNONYMS = {
    "PCE": ["efficiency", "power conversion efficiency", "кпд"],
    "Voc": ["V_oc", "open-circuit voltage", "open circuit voltage"],
    "Jsc": ["J_sc", "short-circuit current density"],
    "FF": ["fill factor"],
    "Rs": ["R_s", "series resistance"],
    "Rsh": ["R_sh", "shunt resistance"],
    "HI": ["hysteresis index"],
    "Jsc_EQE": ["Jsc (EQE)", "integrated Jsc"],
    "stabilized_PCE": ["SPO", "stabilized power output"],
    "T80": ["T_80"],
    "retained_PCE": ["retained efficiency"],
}


def builtin_solar_profile() -> ExtractionProfile:
    fields: list[ProfileField] = []
    for name in EVAL_METRICS:
        fields.append(ProfileField(key=name, label=name, unit=TARGET_UNITS.get(name),
                                   synonyms=_METRIC_SYNONYMS.get(name, []), evaluated=True))
    for extra in ("Voc_deficit", "Jmpp", "Vmpp", "AVT", "CRI", "LUE", "bifaciality_factor"):
        fields.append(ProfileField(key=extra, label=extra, unit=TARGET_UNITS.get(extra)))
    fields += [
        ProfileField(key="bandgap_value", table="materials", column="bandgap_value", unit="eV"),
        ProfileField(key="layer_thickness", table="layers", column="layer_thickness", unit="nm"),
        ProfileField(key="active_area", table="devices", column="active_area", unit="cm2"),
        ProfileField(key="light_intensity", table="measurements", column="light_intensity"),
    ]
    rules: dict[str, RuleConfig] = {}
    for name, info in CHECK_CATALOG.items():
        if info.computed_by in ("backend", "both") and info.exported:
            params: dict[str, Any] = {}
            if name == "jv_consistency":
                params["threshold_pct"] = 3
            elif name == "eqe_consistency":
                params["threshold_pct"] = 5
            rules[name] = RuleConfig(check_name=name, enabled=info.backend_default_enabled, params=params)
    return ExtractionProfile(profile_id=BUILTIN_SOLAR_ID, version=1, name="ТЗ ВИНИТИ: солнечные элементы",
                             fields=fields, rules=rules, builtin=True)


_PARAM_ALIASES = {"jsc_eqe": "Jsc_EQE", "stabilized_pce": "stabilized_PCE", "retained_pce": "retained_PCE",
                  "voc_deficit": "Voc_deficit"}


def param_to_metric(param: str) -> str | None:
    """'pce' -> 'PCE'; 'bandgap' -> None (не показатель results)."""
    low = param.strip().lower()
    if low in _PARAM_ALIASES:
        return _PARAM_ALIASES[low]
    for name in (*EVAL_METRICS, "Voc_deficit", "Jmpp", "Vmpp", "AVT", "CRI", "LUE", "bifaciality_factor"):
        if name.lower() == low:
            return name
    return None


def metric_to_param(metric: str) -> str:
    return metric.lower()

