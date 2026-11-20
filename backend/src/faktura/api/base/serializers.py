"""Единая форма контракта (api_contract.md §3): доменные сущности -> JSON-словари для фронтенда.

Правила (§1.1): даты - ISO 8601 UTC с ``Z``; числа - JSON-числа без «шума» (``Decimal('1.197')`` -> ``1.197``);
внутренние поля (``password_hash``, ``storage_key``, ``file_key``, ``workspace_id``) наружу не выходят.

У каждого сериализатора есть ``extra``: словарь полей, которые модуль-вызыватель знает лучше (счётчики, подписи,
вычисленные агрегаты). Он применяется последним и перекрывает значения по умолчанию.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from ...domain.entities import (Action, Anchor, Corpus, CorpusFile, CorpusFileStatus, Export, Fact, Issue, Paper,
                                PaperStats, PaperStatus, Run, RunStatus, User)
from ...domain.errors import ERROR_CATALOG
from ...domain.ports import Normalizer
from ...domain.tables import CHECK_CATALOG
from .collection_config import TEXT_PARAMS, param_label

__all__ = ["iso", "num", "user_out", "corpus_out", "corpus_file_out", "run_out", "paper_out", "fact_out",
           "anchor_out", "issue_out", "history_out", "export_out", "error_obj", "UI_LEVEL_BY_SEVERITY",
           "ENTITY_SINGULAR", "API_PREFIX_EXPORTS"]

UTC = timezone.utc
API_PREFIX_EXPORTS = "/api/v1/exports"
DEFAULT_COLOR = "graphite"
UI_LEVEL_BY_SEVERITY = {"error": "conflict", "warn": "doubt", "info": "warn"}
ENTITY_SINGULAR = {"results": "result", "materials": "material", "devices": "device", "layers": "layer",
                   "measurements": "measurement", "publications": "publication", "issues": "issue"}
ANCHOR_KIND = {"table": "cell", "figure": "figure", "quote": "text", "value": "text"}
EXPORT_EXT = {"submission": "zip", "csv": "csv", "xlsx": "xlsx", "json": "json", "tsv": "tsv"}
EXPORT_TTL = timedelta(hours=24)


# --- примитивы ---------------------------------------------------------------------------------

def iso(dt: datetime | None) -> str | None:
    """``2026-11-20T09:15:00Z``; naive считается UTC, другие пояса переводятся в UTC."""
    if dt is None:
        return None
    if isinstance(dt, str):
        return dt
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def num(v: Any) -> int | float | None:
    """Decimal/строка/число -> JSON-число без лишних знаков (целые - int)."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v
    try:
        d = v if isinstance(v, Decimal) else Decimal(str(v).strip().replace(",", "."))
    except Exception:  # noqa: BLE001 - нечисло наружу не отдаём
        return None
    if not d.is_finite():
        return None
    if d == d.to_integral_value() and abs(d) < Decimal(10) ** 15:
        return int(d)
    return float(d)


def _val(v: Any) -> Any:
    return v.value if isinstance(v, Enum) else v


def _get(obj: Any, name: str, default: Any = None) -> Any:
    v = getattr(obj, name, default)
    return default if v is None else v


def _apply(out: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    if extra:
        out.update(extra)
    return out


def error_obj(code: str | None, message: str | None = None, details: Any = None) -> dict[str, Any] | None:
    if not code:
        return None
    return {"code": code, "message": message or ERROR_CATALOG.get(code, (0, code))[1], **(
        {"details": details} if details is not None else {})}


def _int_page(v: Any) -> Any:
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return v


# --- пользователь ------------------------------------------------------------------------------

def user_out(u: User, *, admin: bool = False, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``User`` (§3.1); ``admin=True`` добавляет ``disabled``, ``created_at``, ``last_login_at`` (для /admin/users)."""
    out: dict[str, Any] = {
        "id": u.id, "user_id": u.id, "email": u.email, "name": u.name,
        "role": getattr(u, "role", None), "team": getattr(u, "team", None),
        "color": getattr(u, "color", None) or DEFAULT_COLOR, "focus": getattr(u, "focus", None),
        "is_admin": bool(u.is_admin),
    }
    if admin:
        out["disabled"] = bool(u.disabled)
        out["created_at"] = iso(u.created_at)
        out["last_login_at"] = iso(getattr(u, "last_login_at", None))
    return _apply(out, extra)


# --- корпус, файлы, прогоны --------------------------------------------------------------------

_CORPUS_COUNTERS = ("files_total", "papers_done", "papers_failed", "facts", "open_issues")


def corpus_out(c: Corpus, stats: dict[str, Any] | None = None, *, extra: dict[str, Any] | None = None
               ) -> dict[str, Any]:
    """``Corpus`` (§3.2). Счётчики - из ``stats`` (или одноимённых атрибутов), иначе 0."""
    stats = stats or {}
    out: dict[str, Any] = {
        "corpus_id": c.id, "name": c.name, "kind": c.kind, "collection": c.collection,
        "profile_id": c.profile_id, "project_id": c.project_id,
    }
    for k in _CORPUS_COUNTERS:
        out[k] = int(stats.get(k, _get(c, k, 0)) or 0)
    out["last_run_id"] = stats.get("last_run_id", getattr(c, "last_run_id", None))
    out["active_run"] = stats.get("active_run", getattr(c, "active_run", None))
    out["created_by"] = c.created_by
    out["created_at"] = iso(c.created_at)
    out["updated_at"] = iso(stats.get("updated_at") or getattr(c, "updated_at", None) or c.created_at)
    out["deleted_at"] = iso(c.deleted_at)
    return _apply(out, extra)


_FILE_FINAL = {CorpusFileStatus.done, CorpusFileStatus.failed, CorpusFileStatus.skipped_duplicate}


def corpus_file_out(f: CorpusFile, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``CorpusFile`` (§3.3). ``storage_key`` не отдаётся."""
    status = CorpusFileStatus(f.status)
    out: dict[str, Any] = {
        "file_id": f.id, "corpus_id": f.corpus_id, "name": f.name, "publication_id": f.publication_id,
        "source": f.source, "size": f.size_bytes, "page_count": f.page_count, "sha256": f.sha256,
        "status": status.value, "stage": getattr(f, "stage", None),
        "progress": 100 if status in _FILE_FINAL else int(_get(f, "progress", 0)),
        "paper_id": f.paper_id, "title": getattr(f, "title", None), "duplicate_of": f.duplicate_of,
        "dup_kind": _val(f.dup_kind), "facts": int(_get(f, "facts", 0)), "issues": int(_get(f, "issues", 0)),
        "error": error_obj(f.error_code, f.error_message) if status == CorpusFileStatus.failed or f.error_code
        else None,
        "attempts": int(_get(f, "attempts", 0)), "model_version": getattr(f, "model_version", None),
        "from_cache": bool(_get(f, "from_cache", False)),
        "created_at": iso(f.created_at), "updated_at": iso(getattr(f, "updated_at", None) or f.created_at),
    }
    if status == CorpusFileStatus.cancelled:
        out["error"] = None
    return _apply(out, extra)


def run_progress(r: Run) -> int:
    c = r.counts
    if not c.total:
        return 0
    finished = c.done + c.failed + c.skipped + c.cancelled
    return max(0, min(100, int(finished * 100 // c.total)))


def run_out(r: Run, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``Run`` (§3.4); ``progress`` 0…100 по числу завершённых файлов."""
    output = None
    if r.output is not None:
        o = r.output
        output = {"available": bool(o.available), "url": o.url, "csv_files": list(o.csv_files),
                  "check_status": o.check_status, "built_at": iso(o.built_at)}
    out: dict[str, Any] = {
        "run_id": r.id, "corpus_id": r.corpus_id, "kind": _val(r.kind), "status": RunStatus(r.status).value,
        "progress": run_progress(r), "paused": bool(r.paused), "pause_reason": r.pause_reason,
        "counts": r.counts.model_dump(), "facts_found": int(_get(r, "facts_found", 0)),
        "issues_found": int(_get(r, "issues_found", 0)), "concurrency": r.concurrency,
        "keep_review": bool(r.keep_review), "profile_id": r.profile_id,
        "profile_version": getattr(r, "profile_version", None), "model_version": r.model_version,
        "rules_version": getattr(r, "rules_version", None), "dictionary_version": getattr(r, "dictionary_version", None),
        "file_ids": r.file_ids, "eta_s": getattr(r, "eta_s", None), "created_at": iso(r.created_at),
        "started_at": iso(r.started_at), "finished_at": iso(r.finished_at), "created_by": r.started_by,
        "output": output,
    }
    return _apply(out, extra)


# --- статья ------------------------------------------------------------------------------------

def _strip_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip()
    low = d.lower()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi.org/",
              "doi:"):
        if low.startswith(p):
            d = d[len(p):].strip()
            break
    return d or None


_PROGRESS = {PaperStatus.queued: 0, PaperStatus.processing: 50, PaperStatus.done: 100, PaperStatus.failed: 100}


def paper_out(p: Paper, stats: PaperStats | None = None, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``Paper`` (§3.6). ``stats`` перекрывает ``p.stats``. Поля, которых нет в сущности (``authors``, ``journal``,
    ``file``, ``rows``…), можно передать в ``extra``."""
    st = stats if stats is not None else p.stats
    status = PaperStatus(p.status)
    details = dict(st.quality.details or {})
    quality = {
        "total": int(details.get("total", st.facts_count) or 0),
        "sourced_pct": num(details.get("sourced_pct", 0)) or 0,
        "normalized_pct": num(details.get("normalized_pct", 0)) or 0,
        "accepted": int(details.get("accepted", 0) or 0),
        "issues": int(details.get("issues", st.open_issues) or 0),
        "score": st.quality.score, "level": st.quality.level,
    }
    file_name = getattr(p, "file_name", None)
    title = p.title or file_name or p.publication_id
    authors = getattr(p, "authors", None) or []
    out: dict[str, Any] = {
        "id": p.id, "paper_id": p.id, "publication_id": p.publication_id, "corpus_id": p.corpus_id,
        "file_id": p.file_id, "title": title, "authors": list(authors),
        "authors_text": getattr(p, "authors_text", None) or ", ".join(authors), "year": p.year,
        "doi": _strip_doi(p.doi), "journal": getattr(p, "journal", None), "collection": p.collection,
        "keywords": list(getattr(p, "keywords", None) or []), "page_count": p.page_count,
        "main_page": None, "main_table": None, "main_figure": None,
        "in_scope": p.in_scope, "exclusion_reason": getattr(p, "exclusion_reason", None),
        "file": {"name": file_name or f"{p.publication_id}.pdf", "size": getattr(p, "file_size", None),
                 "sha256": p.sha256},
        "mine": bool(getattr(p, "mine", False)), "added_at": iso(p.created_at), "chat_id": None,
        "processing": {"status": status.value, "stage": getattr(p, "stage", None), "progress": _PROGRESS[status],
                       "error": error_obj(p.error_code) if status == PaperStatus.failed or p.error_code else None,
                       "started_at": iso(getattr(p, "started_at", None)), "finished_at": iso(p.processed_at)},
        "topic_by": "project", "dup_of": p.dup_of, "dup_kind": _val(p.dup_kind), "model_version": p.model_version,
        "needs_ocr": bool(p.needs_ocr), "rows": list(getattr(p, "rows", None) or []), "claims": [],
        "quality": quality, "coverage_summary": {"filled": st.coverage.filled, "total": st.coverage.total},
        "open_issues": int(st.open_issues), "top_level": st.top_level, "facts_count": int(st.facts_count),
        "created_at": iso(p.created_at), "processed_at": iso(p.processed_at), "deleted_at": iso(p.deleted_at),
    }
    return _apply(out, extra)


# --- факт --------------------------------------------------------------------------------------

_FACT_PASSTHROUGH = ("result_origin", "cited_publication_id", "aggregation", "n_devices", "time_point", "time_unit",
                     "measurement_id", "material_id", "device_id", "scan_direction", "source_section", "cross",
                     "note", "source_where")


def _display(normalizer: Normalizer | None, value: Decimal | None, unit: str | None) -> str | None:
    if value is None or normalizer is None:
        return None
    try:
        return normalizer.format_display(value, unit)
    except Exception:  # noqa: BLE001 - показ не должен ронять ответ
        return f"{num(value)} {unit or ''}".strip()


def fact_out(f: Fact, normalizer: Normalizer | None, *, issue_ids: list[str] | None = None,
             collection: str = "solar", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``Fact`` (§3.7). ``value`` = ``corrected_value ?? normalized_value``; ``display`` - через нормализатор."""
    target = dict(f.target or {})
    if "entity" in target:
        target["entity"] = ENTITY_SINGULAR.get(str(target["entity"]), target["entity"])
    value = f.value
    text_fact = f.param in TEXT_PARAMS
    normalized_text = (f.raw_value if f.raw_value is not None else None) if text_fact else None
    display = normalized_text if text_fact else _display(normalizer, value, f.normalized_unit)
    out: dict[str, Any] = {
        "fact_id": f.id, "paper_id": f.paper_id, "publication_id": f.publication_id,
        "row_id": f.row_id, "row_label": f.row_label, "param": f.param,
        "param_label": param_label(f.param, collection, f.metric_name), "metric_name": f.metric_name,
        "target": target, "raw_value": f.raw_value, "raw_unit": f.raw_unit,
        "normalized_value": num(f.normalized_value), "normalized_unit": f.normalized_unit,
        "metric_value": num(value), "metric_unit": f.normalized_unit, "normalized_text": normalized_text,
        "norm_status": _val(f.norm_status), "extraction_status": f.extraction_status,
        "review_status": _val(f.review_status), "corrected_value": num(f.corrected_value),
        "corrected_raw": f.corrected_raw, "value": num(value), "display": display,
        "confidence": None if f.confidence is None else float(f.confidence), "added_by": _val(f.added_by),
        "spread_value": num(getattr(f, "spread_value", None)),
        "duplicate_of": f.duplicate_of, "source_page": f.source_page, "source_table": f.source_table,
        "source_figure": f.source_figure, "source_fragment": f.source_fragment, "source_kind": _val(f.source_kind),
        "source_ref": f.source_table or f.source_figure, "quote_found": f.quote_found, "anchor_id": f.anchor_id,
        "exportable": bool(f.exportable),
        "issue_ids": list(issue_ids if issue_ids is not None else (getattr(f, "issue_ids", None) or [])),
        "model_version": f.model_version, "reviewed_by": f.reviewed_by, "reviewed_at": iso(f.reviewed_at),
        "created_at": iso(getattr(f, "created_at", None)), "updated_at": iso(getattr(f, "updated_at", None)
                                                                           or f.reviewed_at),
    }
    for k in _FACT_PASSTHROUGH:
        out.setdefault(k, getattr(f, k, None))
    return _apply(out, extra)


# --- якорь, замечание, журнал, выгрузка --------------------------------------------------------

def anchor_out(a: Anchor, *, issue_ids: list[str] | None = None, extra: dict[str, Any] | None = None
               ) -> dict[str, Any]:
    """``Anchor`` (§3.8): ``kind`` в словаре контракта (``cell|text|figure|manual|caption|reference``)."""
    out: dict[str, Any] = {
        "anchor_id": a.id, "paper_id": a.paper_id, "kind": ANCHOR_KIND.get(a.kind, "text"), "page": a.page,
        "rects": [{"x": r.x, "y": r.y, "w": r.w, "h": r.h} for r in a.rects], "text": a.text,
        "fact_ids": list(a.fact_ids),
        "issue_ids": list(issue_ids if issue_ids is not None else (getattr(a, "issue_ids", None) or [])),
    }
    return _apply(out, extra)


def _json_numbers(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        if isinstance(v, Decimal):
            out[k] = num(v)
        elif isinstance(v, float) and v != v:  # NaN
            out[k] = None
        else:
            out[k] = v
    return out


def issue_out(i: Issue, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``Issue`` (§3.9). ``issue_id`` - непрозрачный ``iss_…`` (не ``I1`` из CSV и не внутренний ключ);
    ``text`` = ``description`` (У-17); ``ui_level`` по ``severity``; ``plugin`` и ``title`` - из каталога."""
    rec = i.record
    info = CHECK_CATALOG.get(rec.check_name)
    severity = rec.severity or (info.severity if info else "info")
    record_ids = list(rec.record_ids)
    fact_ids = list(i.fact_ids)
    out: dict[str, Any] = {
        "issue_id": i.id, "check_name": rec.check_name, "plugin": info.plugin if info else None,
        "scope": "paper", "collection": getattr(i, "collection", None) or "solar",
        "paper_id": i.paper_id, "publication_id": rec.publication_id,
        "severity": severity, "ui_level": UI_LEVEL_BY_SEVERITY.get(severity, "warn"),
        "title": info.title if info else rec.check_name, "text": rec.description, "description": rec.description,
        "record_ids": record_ids, "row_id": None, "row_label": None, "param": None, "param_label": None,
        "value": None, "fact_ids": fact_ids, "main_fact_id": fact_ids[0] if fact_ids else None,
        "anchor_ids": list(i.anchor_ids), "related": [{"entity": "result", "id": r} for r in record_ids],
        "numbers": _json_numbers(rec.numbers), "source_page": _int_page(rec.source_page),
        "source_fragment": rec.source_fragment, "status": _val(i.status),
        "resolved_by": getattr(i, "resolved_by", None), "resolved_at": iso(getattr(i, "resolved_at", None)),
        "touched_fact_ids": list(getattr(i, "touched_fact_ids", None) or []), "computed_by": rec.computed_by,
        "merged": bool(rec.merged), "confidence": rec.confidence, "rule_version": i.rule_version,
        "stale": bool(i.stale), "created_at": iso(i.created_at),
        "updated_at": iso(getattr(i, "updated_at", None) or i.created_at),
    }
    return _apply(out, extra)


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        v = num(v)
    return v if isinstance(v, str) else str(v)


def history_out(a: Action, *, who: str | None = None, paper_title: str | None = None,
                param_label: str | None = None, row_label: str | None = None,
                extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``HistoryEntry`` (§3.10). ``from``/``to`` - у одиночной правки, ``count`` - у групповой."""
    changes = list(a.changes or [])
    single = changes[0] if len(changes) == 1 else None
    out: dict[str, Any] = {
        "action_id": a.id, "at": iso(a.created_at), "who": who, "user_id": a.user_id, "action": a.action,
        "label": a.label, "paper_id": a.paper_id, "paper_title": paper_title, "fact_id": a.fact_id,
        "param_label": param_label, "row_label": row_label,
        "from": _str_or_none(single.get("from")) if single else None,
        "to": _str_or_none(single.get("to")) if single else None,
        "count": len(changes) if (a.group_id or len(changes) > 1) else None,
        "group_id": a.group_id, "undone": bool(a.undone),
    }
    return _apply(out, extra)


def export_out(e: Export, *, check: dict[str, Any] | None = None, papers: int | None = None,
               facts: int | None = None, action_id: str | None = None, url: str | None = None,
               extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """``Export`` (§3.11). ``queued|running`` -> ``processing``; ``url`` - когда файл готов (и у ``failed`` сдачи
    с ``check_report.txt``); ``file_key`` не отдаётся. Подробности можно хранить в ``e.options`` (``check``,
    ``papers``, ``facts``, ``action_id``, ``error_code``, ``error_details``)."""
    opts = e.options or {}
    status = "processing" if e.status in ("queued", "running") else e.status
    ready = status in ("done", "failed") and bool(e.file_key)
    ext = EXPORT_EXT.get(e.format, "zip")
    scope_name = (e.scope or {}).get("corpus_id") or "papers"
    stamp = (e.created_at or e.finished_at)
    stamp_s = (stamp.astimezone(UTC) if stamp and stamp.tzinfo else stamp).strftime("%Y%m%d") if stamp else "export"
    error = None
    if status == "failed":
        error = {"code": opts.get("error_code") or "internal_error",
                 "message": e.error or ERROR_CATALOG.get(opts.get("error_code") or "internal_error", (0, ""))[1],
                 "details": opts.get("error_details")}
    out: dict[str, Any] = {
        "export_id": e.id, "format": e.format, "status": status, "scope": dict(e.scope or {}),
        "papers": papers if papers is not None else opts.get("papers"),
        "facts": facts if facts is not None else opts.get("facts"),
        "url": (url or f"{API_PREFIX_EXPORTS}/{e.id}/file") if ready else None,
        "filename": opts.get("filename") or f"{e.format}_{scope_name}_{stamp_s}.{ext}",
        "expires_at": iso(e.finished_at + EXPORT_TTL) if e.finished_at and ready else None,
        "check": check if check is not None else opts.get("check"),
        "error": error, "action_id": action_id or opts.get("action_id"),
        "created_at": iso(e.created_at), "finished_at": iso(e.finished_at),
    }
    return _apply(out, extra)
