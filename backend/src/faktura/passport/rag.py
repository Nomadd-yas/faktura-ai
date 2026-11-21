"""Паспорт набора данных области rag: поля таблиц, словари, правило нормализации, версии (docs/design/DOMAINS.md §4)."""
from __future__ import annotations

import json
from typing import Any, Iterable

from ..domain.registry import RAG_SCHEMA_PATH, DomainSpec
from .build import PASSPORT_VERSION

__all__ = ["build_rag_passport", "build_rag_versions"]

_NUMBERS = {
    "publications": {"year"}, "configurations": {"chunk_size", "chunk_overlap", "top_k"},
    "experiments": {"latency_ms", "tokens_per_query", "sample_size"},
    "results": {"metric_k", "metric_value", "baseline_value", "sample_size", "normalized_value", "source_page"},
    "issues": {"source_page"},
}
_SERVICE_NOTE = {
    "in_scope": "Относится ли статья к области: true или false",
    "exclusion_reason": "Причина исключения (только при in_scope=false)",
    "duplicate_of": "result_id основной записи, если строка повторяет значение",
    "issue_id": "Идентификатор замечания, уникален в статье",
    "record_ids": "result_id строк, к которым относится замечание, через «;»",
    "check_name": "Название проверки (свободный текст)",
    "description": "Описание замечания",
    "note": "Пояснение к строке",
}
NORMALIZATION_RULE = ("Доли и проценты не пересчитываются: metric_value и raw_value приводятся к виду источника, "
                      "metric_unit фиксирует шкалу (fraction, %, …). normalized_value заполняется только при "
                      "однозначном числе и известной единице и равен metric_value; значения с графиков помечаются "
                      "в raw_value/note.")


def build_rag_versions(model_versions: Iterable[str] | None = None) -> dict[str, Any]:
    try:
        schema = json.loads(RAG_SCHEMA_PATH.read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        schema = None
    return {"model": sorted({str(v) for v in (model_versions or []) if v}), "template": schema,
            "passport": PASSPORT_VERSION}


def build_rag_passport(spec: DomainSpec, versions: dict[str, Any] | None = None) -> dict:
    tables = []
    for t, cols in spec.tables.items():
        fields = []
        for c in cols:
            q = f"{t}.{c}"
            vals = list(spec.closed_enums.get(q) or spec.recommended_enums.get(q) or [])
            f: dict[str, Any] = {
                "name": c, "type": "number" if c in _NUMBERS.get(t, ()) else "string",
                "required": c in ("publication_id", spec.keys.get(t)), "unit": None,
                "description": _SERVICE_NOTE.get(c, ""), "source": "модель"}
            if vals:
                f["values"] = vals
                f["vocabulary"] = "closed" if q in spec.closed_enums else "recommended"
            if q in spec.list_fields:
                f["separator"] = ";"
            fields.append(f)
        tables.append({"name": t, "tz_name": t, "row": "", "key": spec.keys[t], "file": f"{t}.csv",
                       "fields": fields,
                       "links": [{"field": k.split(".")[1], "to": f"{v[0]}.{v[1]}"} for k, v in spec.refs.items()
                                 if k.startswith(t + ".")]})
    return {
        "collection": spec.name, "title": spec.title, "version": PASSPORT_VERSION,
        "schema_version": (versions or {}).get("template"),
        "scope": spec.title,
        "format": {"encoding": "UTF-8 с BOM", "delimiter": ",", "decimal": ".", "quoting": "QUOTE_MINIMAL",
                   "newline": "CRLF", "boolean": ["true", "false"], "list_separator": ";",
                   "empty": "пустая ячейка — в статье этого нет"},
        "tables": tables,
        "vocabularies": {"closed": {k: list(v) for k, v in spec.closed_enums.items()},
                         "recommended": {k: list(v) for k, v in spec.recommended_enums.items()}},
        "normalization": NORMALIZATION_RULE,
        "versions": versions or build_rag_versions(),
    }
