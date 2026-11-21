"""Адаптер ответа прототипа ML-1 («pdf-dataset», POST /extract) к конвейерному контракту §7.0.1 (временный патч)."""
from __future__ import annotations

import logging
from typing import Any

from ..domain.errors import ExtractError, extract_error

log = logging.getLogger(__name__)

TABLES = ("materials", "devices", "layers", "measurements", "results", "issues")
RENAMES: dict[str, dict[str, str]] = {
    "devices": {"area_value": "active_area"},
    "layers": {"thickness_value": "layer_thickness", "temperature_c": "annealing_temperature",
               "time_value": "annealing_time"},
}


def _rename(table: str, row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    out = dict(row)
    for old, new in RENAMES.get(table, {}).items():
        if old in out and out.get(new) is None:
            out[new] = out.pop(old)
    return out


def pdf_dataset_to_contract(body: dict, publication_id: str) -> dict:
    if not isinstance(body, dict):
        raise extract_error("ml_schema_mismatch", "Ответ ML не соответствует контракту прототипа: ответ не JSON-объект")
    ds = body.get("dataset")
    if not isinstance(ds, dict):
        if body.get("ok") is False:
            raise extract_error("ml_error", "ML-1: " + (str(body.get("extract_error") or "") or "извлечение не удалось"),
                               retryable=False)
        raise extract_error("ml_schema_mismatch", "Ответ ML не соответствует контракту прототипа: нет dataset")
    meta = body.get("meta")
    model = meta.get("model") if isinstance(meta, dict) else None
    pubs = ds.get("publications")
    pub = dict(pubs[0]) if isinstance(pubs, list) and pubs and isinstance(pubs[0], dict) else {}
    pub["publication_id"] = publication_id
    top = body.get("model_version")  # сервис отдаёт готовое «pdf-dataset/<model>»
    mv = top if isinstance(top, str) and top else (
        "pdf-dataset/" + model if isinstance(model, str) and model else "pdf-dataset")
    out: dict[str, Any] = {
        "model_version": mv,
        "publication": pub}
    for t in TABLES:
        rows = ds.get(t)
        out[t] = [_rename(t, r) for r in rows] if isinstance(rows, list) else []
    if body.get("ok") is False:
        failed = ", ".join(map(str, body.get("failed_tables") or [])) or "-"
        msg = f"прототип ML-1: извлечение частичное ({body.get('extract_error') or 'ошибка'}); не получены таблицы: {failed}"
        log.warning("%s: %s", publication_id, msg)
        out["warnings"] = [f"Модель разобрала статью частично; не получены таблицы: {failed}"]  # parse_extract_response переносит их в ExtractOutcome.warnings
    return out
