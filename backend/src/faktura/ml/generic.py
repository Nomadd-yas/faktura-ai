"""Ответ модели и мок для областей без своих pydantic-моделей (rag): строки таблиц как словари (docs/design/DOMAINS.md §3).

Сырой ответ (он же кешируется и отдаётся моком): `{"model_version", "domain", "tables": {таблица: [строка]}, "warnings"}`.
Прототип ML-1 (`pdf_dataset`) отдаёт `{ok, dataset: {таблица: [...]}, meta, model_version}` - `pdf_dataset_to_generic`
переводит его в этот вид без переименований колонок.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

from ..domain.errors import extract_error
from ..domain.extraction import GenericOutcome
from ..domain.registry import DomainSpec, get_domain
from .response import file_meta, page_texts

log = logging.getLogger(__name__)

__all__ = ["parse_generic_response", "pdf_dataset_to_generic", "GenericGoldExtractor"]


def _rows(table: str, value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise extract_error("ml_schema_mismatch", f"Ответ ML: таблица {table} - не список объектов")
    return [dict(r) for r in value]


def parse_generic_response(raw: Any, publication_id: str, spec: DomainSpec) -> GenericOutcome:
    """Сырой ответ -> GenericOutcome. Принимает `tables: {...}` или таблицы на верхнем уровне
    (+ одиночный `publication`). Строка publications есть всегда; её publication_id - из запроса."""
    if not isinstance(raw, dict):
        raise extract_error("ml_schema_mismatch", "Ответ ML не JSON-объект")
    src = raw.get("tables") if isinstance(raw.get("tables"), dict) else raw
    tables = {t: _rows(t, src.get(t)) for t in spec.tables}
    pub = raw.get("publication")
    if not tables["publications"] and isinstance(pub, dict):
        tables["publications"] = [dict(pub)]
    if not tables["publications"]:
        tables["publications"] = [{}]
    tables["publications"] = [{**tables["publications"][0], "publication_id": publication_id}]
    mv = raw.get("model_version")
    warns = raw.get("warnings")
    ocr_conf, suggested = file_meta(raw)
    extra = {k: v for k, v in (("ocr_confidence", ocr_conf), ("suggested_domain", suggested)) if v is not None}
    return GenericOutcome(
        tables=tables, model_version=str(mv) if isinstance(mv, (str, int, float)) and str(mv) else "unknown",
        warnings=[str(w) for w in warns] if isinstance(warns, list) else [],
        ocr_confidence=ocr_conf, suggested_domain=suggested, page_texts=page_texts(raw),
        raw={"model_version": raw.get("model_version"), "domain": spec.name, "tables": tables,
             "warnings": warns if isinstance(warns, list) else [], **extra,
             **({"page_texts": raw["page_texts"]} if page_texts(raw) else {})})


def pdf_dataset_to_generic(body: Any, publication_id: str, domain: str) -> dict:
    """Ответ прототипа pdf-dataset -> сырой ответ области (таблицы как есть, без переименований колонок)."""
    spec = get_domain(domain)
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
    top = body.get("model_version")
    mv = top if isinstance(top, str) and top else ("pdf-dataset/" + model if isinstance(model, str) and model
                                                    else "pdf-dataset")
    out: dict[str, Any] = {"model_version": mv, "domain": domain,
                           "tables": {t: (ds.get(t) if isinstance(ds.get(t), list) else []) for t in spec.tables}}
    if body.get("ok") is False:
        failed = ", ".join(map(str, body.get("failed_tables") or [])) or "-"
        log.warning("%s: прототип ML-1: извлечение частичное (%s); не получены таблицы: %s", publication_id,
                    body.get("extract_error") or "ошибка", failed)
        out["warnings"] = [f"Модель разобрала статью частично; не получены таблицы: {failed}"]
    out.update(_meta_fields(meta))
    return out


def _meta_fields(meta: Any) -> dict[str, Any]:
    """ocr_confidence / suggested_domain из meta ответа прототипа pdf-dataset - на верхний уровень сырого ответа."""
    conf, sug = file_meta(meta)
    pt = page_texts(meta)
    return {k: v for k, v in (("ocr_confidence", conf), ("suggested_domain", sug), ("page_texts", pt))
            if v is not None}


class GenericGoldExtractor:
    """Мок области по эталону CSV (`<gold_dir>/<таблица>.csv`): строки статьи как в эталоне; неизвестный
    publication_id - только строка publications. model_version = 'mock-gold-<sha8>'."""
    name = "mock"

    def __init__(self, gold_dir: Path | None, domain: str) -> None:
        self.spec = get_domain(domain)
        self.gold_dir = Path(gold_dir) if gold_dir is not None else None
        self._lock = threading.Lock()
        self._rows: dict[str, dict[str, list[dict[str, str]]]] | None = None
        self._version: str | None = None

    def _load(self) -> None:
        if self._rows is not None:
            return
        with self._lock:
            if self._rows is not None:
                return
            gd = self.gold_dir
            if gd is None or not gd.is_dir() or not (gd / "publications.csv").is_file():
                raise extract_error("ml_error", f"Нет эталона для мока {self.spec.name}: {gd}", retryable=False)
            csv.field_size_limit(1 << 28)
            h = hashlib.sha256()
            rows: dict[str, dict[str, list[dict[str, str]]]] = {}
            for t, cols in self.spec.tables.items():
                p = gd / f"{t}.csv"
                h.update(t.encode())
                if not p.is_file():
                    continue
                h.update(p.read_bytes())
                with p.open(encoding="utf-8-sig", newline="") as f:
                    for r in csv.DictReader(f):
                        pid = (r.get("publication_id") or "").strip()
                        if pid:
                            rows.setdefault(pid, {}).setdefault(t, []).append(
                                {c: (r.get(c) or "") for c in cols})
            self._version = f"mock-gold-{h.hexdigest()[:8]}"
            self._rows = rows

    @property
    def model_version(self) -> str:
        self._load()
        assert self._version is not None
        return self._version

    def publication_ids(self) -> list[str]:
        self._load()
        return list(self._rows or {})

    def build_raw(self, publication_id: str) -> dict[str, Any]:
        self._load()
        paper = (self._rows or {}).get(publication_id) or {}
        tables = {t: [dict(r) for r in paper.get(t, [])] for t in self.spec.tables}
        if not tables["publications"]:
            tables["publications"] = [{"publication_id": publication_id}]
        return {"model_version": self._version, "domain": self.spec.name, "tables": tables}

    def extract(self, req) -> GenericOutcome:
        return parse_generic_response(self.build_raw(req.publication_id), req.publication_id, self.spec)

    def health(self):
        from ..domain.errors import ExtractError
        from ..domain.extraction import MlHealth

        try:
            return MlHealth(status="ok", model_version=self.model_version)
        except (ExtractError, OSError):
            return MlHealth(status="down", model_version=None)
