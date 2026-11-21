"""Константы формата сдачи, 1:1 с data/caseB_kejs5/tools/check_submission.py (проверяется tests/shared)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_T = {
    "publications": "publication_id title year DOI authors in_scope exclusion_reason",
    "materials": "publication_id material_id material_name_raw formula_normalized material_class dimensionality additives bandgap_value bandgap_unit bandgap_method note",
    "devices": "publication_id device_id absorber_material_id device_label device_role device_type architecture substrate_type active_area area_unit area_definition encapsulation note",
    "layers": "publication_id layer_id device_id layer_order layer_function layer_material layer_thickness thickness_unit deposition_method annealing_temperature annealing_time atmosphere note",
    "measurements": "publication_id measurement_id device_id measurement_type spectrum light_intensity intensity_unit scan_direction scan_rate mask_used certified certification_lab temperature stability_protocol stress_conditions note",
    "results": "publication_id result_id device_id measurement_id material_id metric_name metric_value metric_unit aggregation spread_value n_devices time_point time_unit raw_value raw_unit normalized_value normalized_unit result_origin cited_publication_id source_page source_section source_table source_figure source_fragment extraction_status duplicate_of note",
    "issues": "publication_id issue_id record_ids check_name description source_page source_fragment",
}
TABLES: dict[str, tuple[str, ...]] = {k: tuple(v.split()) for k, v in _T.items()}
TABLE_NAMES: tuple[str, ...] = tuple(TABLES)

# первичный ключ внутри статьи для каждой таблицы
ID_COLUMN: dict[str, str] = {
    "publications": "publication_id",
    "materials": "material_id",
    "devices": "device_id",
    "layers": "layer_id",
    "measurements": "measurement_id",
    "results": "result_id",
    "issues": "issue_id",
}

_V = {
    ("materials", "material_class"): "perovskite|CIGS|CdTe|kesterite|Sb2Se3|thin-film Si|other",
    ("materials", "dimensionality"): "3D|2D|2D-3D",
    ("materials", "bandgap_method"): "Tauc|EQE|PL|cited",
    ("devices", "device_role"): "control|target|record",
    ("devices", "device_type"): "cell|minimodule|module",
    ("devices", "architecture"): "n-i-p|p-i-n|substrate|superstrate",
    ("devices", "substrate_type"): "rigid|flexible",
    ("devices", "area_definition"): "designated illumination|aperture|total",
    ("layers", "layer_function"): "front_contact|ETL|absorber|passivation|HTL|interlayer|recombination|back_contact",
    ("measurements", "measurement_type"): "J-V|MPP tracking|EQE|stability",
    ("measurements", "scan_direction"): "reverse|forward|average",
    ("measurements", "stability_protocol"): "ISOS-D|ISOS-L|ISOS-T|other",
    ("measurements", "mask_used"): "true|false",
    ("measurements", "certified"): "true|false",
    ("publications", "in_scope"): "true|false",
    ("results", "aggregation"): "champion|mean|median",
    ("results", "result_origin"): "primary|cited",
    ("results", "extraction_status"): "extracted|present_not_extractable|absent",
}
VOCAB: dict[tuple[str, str], frozenset[str]] = {k: frozenset(v.split("|")) for k, v in _V.items()}

# поля, которые проверяет check_submission.py (float())
NUMERIC_STRICT: frozenset[tuple[str, str]] = frozenset({
    ("results", "metric_value"), ("results", "normalized_value"), ("results", "source_page"),
    ("layers", "layer_order"), ("devices", "active_area"), ("materials", "bandgap_value"),
})
# скрипт их не проверяет, но там тоже только числа
NUMERIC_LOOSE: frozenset[tuple[str, str]] = frozenset({
    ("publications", "year"), ("results", "n_devices"), ("results", "spread_value"),
    ("measurements", "light_intensity"), ("layers", "layer_thickness"), ("results", "time_point"),
    ("issues", "source_page"),
})
NUMERIC_FIELDS: frozenset[tuple[str, str]] = NUMERIC_STRICT | NUMERIC_LOOSE
# целые числа (год, страница, порядок слоя, число приборов)
INTEGER_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("publications", "year"), ("results", "source_page"), ("layers", "layer_order"),
    ("results", "n_devices"), ("issues", "source_page"),
})

BOOL_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("publications", "in_scope"),
    ("measurements", "mask_used"), ("measurements", "certified"),
})
# разделитель списка в ячейке
LIST_FIELDS: dict[tuple[str, str], str] = {
    ("publications", "authors"): "; ",
    ("materials", "additives"): "; ",
    ("measurements", "device_id"): ";",
    ("issues", "record_ids"): ";",
}
# значение -> единица (REQ-037: единица без значения очищается)
UNIT_FOR_VALUE: dict[tuple[str, str], tuple[str, str]] = {
    ("materials", "bandgap_value"): ("materials", "bandgap_unit"),
    ("layers", "layer_thickness"): ("layers", "thickness_unit"),
    ("devices", "active_area"): ("devices", "area_unit"),
}

# 11 оцениваемых metric_name (spec/14 §14.7)
EVAL_METRICS: tuple[str, ...] = (
    "PCE", "Voc", "Jsc", "FF", "Rs", "Rsh", "HI", "Jsc_EQE", "stabilized_PCE", "T80", "retained_PCE",
)
# целевые единицы стандартных метрик (spec/06 §6.1)
TARGET_UNITS: dict[str, str] = {
    "PCE": "%", "stabilized_PCE": "%", "retained_PCE": "%", "HI": "%", "FF": "%",
    "Voc": "V", "Voc_deficit": "V", "Vmpp": "V",
    "Jsc": "mA/cm2", "Jsc_EQE": "mA/cm2", "Jmpp": "mA/cm2",
    "Rs": "Ohm*cm2", "Rsh": "Ohm*cm2",
    "T80": "h",
}
TARGET_UNITS_FIELDS: dict[tuple[str, str], str] = {
    ("materials", "bandgap_value"): "eV",
    ("layers", "layer_thickness"): "nm",
    ("devices", "active_area"): "cm2",
}


@dataclass(frozen=True)
class CheckInfo:
    severity: Literal["error", "warn", "info"]
    ui_level: Literal["conflict", "doubt", "warn"]
    plugin: str
    title: str
    computed_by: Literal["backend", "ml", "both"]
    scope: Literal["record", "paper"] = "record"
    backend_default_enabled: bool = False
    exported: bool = True


def _c(sev, ui, plug, title, by, scope="record", on=False, exp=True):
    return CheckInfo(sev, ui, plug, title, by, scope, on, exp)


# каталог spec/05 §5.2 (все имена из dev_gold + внутренние правила интерфейса)
CHECK_CATALOG: dict[str, CheckInfo] = {
    "jv_consistency": _c("warn", "warn", "solar", "КПД не согласуется с Voc·Jsc·FF/Pin", "backend", on=True),
    "voc_limit": _c("error", "conflict", "solar", "Voc выше Eg/q", "backend", on=True),
    "range": _c("error", "conflict", "solar", "Значение вне физических пределов", "backend", on=True),
    "eqe_consistency": _c("warn", "warn", "solar", "Jsc по EQE и по ВАХ расходятся", "backend", on=True),
    "champion_vs_mean": _c("error", "conflict", "solar", "Среднее больше лучшего значения", "backend", on=True),
    "hysteresis_index": _c("info", "warn", "solar", "Индекс гистерезиса", "backend", on=True),
    "contradiction": _c("error", "conflict", "solar", "Противоречие значений в статье", "both", on=True),
    "missing_conditions": _c("warn", "warn", "solar", "Не указаны условия измерения", "both", "paper", True),
    "measurement_conditions": _c("info", "warn", "solar", "Нестандартные условия измерения", "ml"),
    "comparability": _c("warn", "warn", "solar", "Результат несопоставим с остальными", "both", "record", False),
    "ambiguity": _c("info", "doubt", "solar", "Неоднозначность в тексте", "ml"),
    "unit_inconsistency": _c("warn", "warn", "solar", "Единица не согласуется со значением", "both", on=True),
    "derived_values": _c("info", "doubt", "solar", "Производные числа в тексте", "ml"),
    "typo": _c("info", "doubt", "solar", "Опечатка в статье", "ml"),
    "possible_swap": _c("warn", "doubt", "solar", "Значения, вероятно, перепутаны", "ml"),
    "figure_mismatch": _c("warn", "warn", "solar", "Рисунок не совпадает с таблицей", "both", on=True),
    "figure_only": _c("info", "doubt", "solar", "Результаты только на графиках", "ml"),
    "cited_in_table": _c("info", "doubt", "solar", "Чужие результаты в таблице", "ml"),
    "dedup_trap": _c("info", "doubt", "solar", "Ловушка дедупликации", "both", on=True),
    "rounding": _c("info", "doubt", "solar", "Расхождение из-за округления", "backend", on=True),
    "rounding_inconsistency": _c("info", "doubt", "solar", "Расхождение из-за округления", "ml"),
    "plausibility": _c("warn", "warn", "solar", "Значение правдоподобно с трудом", "both", on=True),
    "text_mix": _c("info", "doubt", "solar", "Фраза объединяет максимумы разных приборов", "ml"),
    "voc_deficit_consistency": _c("warn", "warn", "solar", "Дефицит Voc не равен Eg/q - Voc", "backend", on=True),
    # внутренние правила интерфейса, в issues.csv не выгружаются
    "unit_conversion": _c("info", "doubt", "core", "Единица пересчитана", "backend", exp=False),
    "low_confidence": _c("info", "doubt", "core", "Низкая уверенность модели", "backend", exp=False),
    "ocr_low_confidence": _c("info", "doubt", "core", "Низкая уверенность OCR", "backend", exp=False),
}
