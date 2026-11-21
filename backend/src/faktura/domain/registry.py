"""Реестр областей (solar, rag): состав таблиц сдачи, ключи, словари, ссылки (docs/design/DOMAINS.md §1)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AppError
from .tables import EVAL_METRICS, ID_COLUMN, LIST_FIELDS, TABLES, VOCAB

__all__ = ["DomainSpec", "DOMAINS", "get_domain"]

RAG_SCHEMA_PATH = Path(__file__).resolve().parent / "data" / "rag_schema.json"


@dataclass(frozen=True)
class DomainSpec:
    name: str
    title: str
    tables: dict[str, tuple[str, ...]]
    keys: dict[str, str]
    closed_enums: dict[str, tuple[str, ...]]
    recommended_enums: dict[str, tuple[str, ...]]
    refs: dict[str, tuple[str, str]]
    list_fields: frozenset[str]
    scored_metrics: tuple[str, ...] | None
    result_filters: tuple[str, ...]
    summary_by_default: bool = False
    check_exit_review_ok: bool = False
    table_names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "table_names", tuple(self.tables))


# те же связи, что export.sanitize.REFERENCES (совпадение проверяет tests/storage/test_sub_rows.py)
_SOLAR_REFS = {
    "devices.absorber_material_id": ("materials", "material_id"),
    "layers.device_id": ("devices", "device_id"),
    "measurements.device_id": ("devices", "device_id"),
    "results.device_id": ("devices", "device_id"),
    "results.measurement_id": ("measurements", "measurement_id"),
    "results.material_id": ("materials", "material_id"),
    "results.duplicate_of": ("results", "result_id"),
    "issues.record_ids": ("results", "result_id"),
}


def _solar() -> DomainSpec:
    return DomainSpec(
        name="solar", title="Тонкоплёночные солнечные элементы",
        tables=dict(TABLES), keys=dict(ID_COLUMN),
        closed_enums={f"{t}.{c}": tuple(sorted(v)) for (t, c), v in VOCAB.items()},
        recommended_enums={}, refs=dict(_SOLAR_REFS),
        list_fields=frozenset(f"{t}.{c}" for t, c in LIST_FIELDS),
        scored_metrics=tuple(EVAL_METRICS),
        result_filters=("metric_name", "device_id", "measurement_id", "material_id"),
    )


def _rag() -> DomainSpec:
    raw = json.loads(RAG_SCHEMA_PATH.read_text(encoding="utf-8"))
    tables = {k: tuple(v) for k, v in raw["tables"].items()}
    refs = {f"results.{c}": (t, c) for t, c in (
        ("experiments", "experiment_id"), ("configurations", "config_id"),
        ("datasets", "dataset_id"), ("tasks", "task_id"))}
    return DomainSpec(
        name="rag", title="RAG-системы", tables=tables, keys=dict(raw["keys"]),
        closed_enums={k: tuple(v) for k, v in raw["enums"].items()},
        recommended_enums={k: tuple(v) for k, v in raw.get("recommended_enums", {}).items()},
        refs=refs, list_fields=frozenset({"issues.record_ids"}), scored_metrics=None,
        result_filters=("metric_name", "config_id", "dataset_id", "experiment_id", "task_id"),
        summary_by_default=True, check_exit_review_ok=True,
    )


DOMAINS: dict[str, DomainSpec] = {d.name: d for d in (_solar(), _rag())}


def get_domain(name: str) -> DomainSpec:
    try:
        return DOMAINS[name]
    except KeyError:
        raise AppError("domain_not_supported", f"Область не поддерживается: {name}") from None
