"""Представления ядра API v1 (CORE_API.md §2-4): Run/RunFile, сводка, доступ к разбору.

Run из контракта = корпус kind="extract" с одним прогоном (`Run.id` = `run_` + хвост id корпуса).
Строки 7 таблиц - те, что лежат в `sub_rows` (колонки CSV шаблона), отдаются как есть.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from ...domain.entities import CorpusFile, CorpusFileStatus, CurrentUser, PaperStatus, Run, RunStatus
from ...domain.errors import AppError
from ...domain.registry import DOMAINS, get_domain
from ..base.serializers import iso

DOMAIN = "solar"
CORPUS_KIND = "extract"

_MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
_LABELS = {"queued": "в очереди", "running": "разбор", "done": "готово", "nodata": "нет результатов",
           "error": "ошибка", "ocr": "скан, распознано OCR"}
RAG_TOP_METRICS = 20  # coverage сводки rag: топ метрик по числу статей
_IN_PROGRESS = (RunStatus.queued, RunStatus.running, RunStatus.cancelling)
SHORT_FILE_KEYS = ("id", "name", "status", "has_data", "results", "issues", "reason")


# --- идентификаторы ----------------------------------------------------------------------------

def run_id_of(corpus_id: str) -> str:
    return "run_" + corpus_id.removeprefix("crp_")


def corpus_id_of(run_id: str) -> str:
    rid = (run_id or "").strip()
    if rid.startswith("run_"):
        return "crp_" + rid[4:]
    return rid


def _plural(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "статья"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "статьи"
    return "статей"


def default_title(n: int, when: datetime) -> str:
    return f"{n} {_plural(n)} · {when.day} {_MONTHS[when.month - 1]}"


# --- доступ ------------------------------------------------------------------------------------

def get_corpus(uow, user: CurrentUser, run_id: str):
    """Корпус разбора пользователя или 404 (чужие разборы не видны)."""
    c = uow.corpora.get(corpus_id_of(run_id))
    if (c is None or c.deleted_at is not None or c.workspace_id != user.workspace_id
            or c.created_by != user.id or c.collection not in DOMAINS or c.kind != CORPUS_KIND):
        raise AppError("not_found", "Разбор не найден")
    return c


def user_corpora(uow, user: CurrentUser) -> list:
    """Разборы пользователя, новые сверху."""
    items = [c for c in uow.corpora.list(user.workspace_id, kind=CORPUS_KIND)
             if c.created_by == user.id and c.collection in DOMAINS]
    return sorted(items, key=lambda c: (c.created_at or datetime.min.replace(tzinfo=None), c.id), reverse=True)


# --- данные разбора ----------------------------------------------------------------------------

class RunData:
    """Всё, что нужно для показа разбора: файлы, статьи, счётчики строк по статьям."""

    def __init__(self, uow, corpus) -> None:
        self.corpus = corpus
        self.files: list[CorpusFile] = [f for f in uow.files.list(corpus.id)
                                        if f.status != CorpusFileStatus.skipped_duplicate]
        self.papers = {p.id: p for p in uow.papers.list(corpus_id=corpus.id)}
        runs = uow.runs.list(corpus.id, limit=1)
        self.run: Run | None = runs[0] if runs else None
        self.results_n: dict[str, int] = uow.sub.counts_by_paper(corpus.id, "results")
        self.issues_n: dict[str, int] = uow.sub.counts_by_paper(corpus.id, "issues")
        self.pub: dict[str, tuple[str, str]] = uow.sub.publication_scopes(corpus.id)
        self.file_ids = _file_ids(self.files)

    def file_view(self, f: CorpusFile) -> dict[str, Any]:
        paper = self.papers.get(f.paper_id) if f.paper_id else None
        results = self.results_n.get(f.paper_id or "", 0)
        issues = self.issues_n.get(f.paper_id or "", 0)
        status, reason = file_status(f, paper, results, self.pub.get(f.paper_id or ""))
        label = reason if status == "out" and reason else _LABELS.get(status, status)
        if status == "out" and not reason:
            label = "не по теме"
        return {
            "id": self.file_ids[f.id], "name": f.source or f.name, "status": status, "status_label": label,
            "stage": (f.stage or "Извлечение") if status == "running" else None,
            "progress": _progress(f, status),
            "has_data": results > 0, "results": results, "issues": issues, "reason": reason or None,
            "size": f.size_bytes, "sha256": f.sha256,
        }

    def counts(self, views: list[dict]) -> dict[str, int]:
        out = {"files": len(views), "queued": 0, "running": 0, "done": 0, "out": 0, "error": 0, "nodata": 0}
        if self.corpus.collection != "solar":
            out["ocr"] = 0  # сканы бывают только в областях кроме solar
        for v in views:
            out[v["status"]] += 1
        return out

    def status(self, views: list[dict]) -> str:
        run = self.run
        if run is not None:
            if run.status in _IN_PROGRESS:
                return "queued" if run.status == RunStatus.queued else "running"
            # spec §4.1: done, когда все файлы в конечном статусе; error - только если упали все файлы
            if views and all(v["status"] == "error" for v in views):
                return "error"
            if run.status == RunStatus.failed and any(v["status"] in ("queued", "running") for v in views):
                return "error"  # прогон оборвался, часть файлов так и не разобрана
            return "done"
        if any(v["status"] == "running" for v in views):
            return "running"
        return "queued" if any(v["status"] == "queued" for v in views) else "done"

    def updated_at(self) -> datetime | None:
        r = self.run
        return (r.finished_at or r.started_at or r.created_at) if r else self.corpus.created_at


def _progress(f: CorpusFile, status: str) -> int:
    if status == "queued":
        return 0
    if status == "running":
        return max(0, min(int(f.progress if f.progress is not None else 10), 99))
    return 100


def _file_ids(files: list[CorpusFile]) -> dict[str, str]:
    """`id` файла в разборе = publication_id; при совпадении - `P042-2`, `P042-3`."""
    seen: dict[str, int] = defaultdict(int)
    out: dict[str, str] = {}
    for f in files:
        seen[f.publication_id] += 1
        n = seen[f.publication_id]
        out[f.id] = f.publication_id if n == 1 else f"{f.publication_id}-{n}"
    return out


def file_status(f: CorpusFile, paper, results: int, pub: tuple[str, str] | None) -> tuple[str, str]:
    """(статус контракта, причина)."""
    st = f.status
    if st in (CorpusFileStatus.pending, CorpusFileStatus.queued, CorpusFileStatus.downloading):
        return "queued", ""
    if st == CorpusFileStatus.processing:
        return "running", ""
    if st == CorpusFileStatus.failed:
        return "error", f.error_message or f.error_code or "Ошибка разбора"
    if st == CorpusFileStatus.cancelled:
        return "error", "Разбор отменён"
    if st == CorpusFileStatus.needs_ocr:
        return "error", "Нужен OCR: в файле нет текстового слоя"
    if paper is None or paper.status == PaperStatus.failed:
        return "error", (f.error_message or (paper.error_code if paper else None) or "Ошибка разбора")
    if paper.status != PaperStatus.done:
        return "running" if paper.status == PaperStatus.processing else "queued", ""
    in_scope, reason = pub if pub else ("", "")
    if in_scope == "false" or (not pub and paper.in_scope is False):
        return "out", reason
    if results > 0 and f.is_scan:
        return "ocr", ""
    return ("done", "") if results > 0 else ("nodata", "")


def run_view(uow, corpus, *, files: str = "full") -> dict[str, Any]:
    """files: full | short | none."""
    d = RunData(uow, corpus)
    views = [d.file_view(f) for f in d.files]
    n = len(views)
    out: dict[str, Any] = {
        "id": run_id_of(corpus.id),
        "title": corpus.name or default_title(n, corpus.created_at or datetime.now()),
        "domain": corpus.collection,
        "status": d.status(views),
        "created_at": iso(corpus.created_at),
        "updated_at": iso(d.updated_at()),
        "counts": d.counts(views),
        "results": sum(v["results"] for v in views),
        "issues": sum(v["issues"] for v in views),
    }
    if files == "full":
        out["files"] = views
    elif files == "short":
        out["files"] = [{k: v[k] for k in SHORT_FILE_KEYS} for v in views]
    return out


def run_summary(uow, corpus) -> dict[str, Any]:
    d = RunData(uow, corpus)
    views = [d.file_view(f) for f in d.files]
    by_status = d.counts(views)
    in_scope_pids = {f.paper_id for f, v in zip(d.files, views)
                     if v["status"] in ("done", "nodata", "ocr") and f.paper_id}
    per_metric: dict[str, int] = {}
    papers_with: dict[str, set[str]] = defaultdict(set)
    for paper_id, metric, n in uow.sub.metric_counts(corpus.id):
        per_metric[metric] = per_metric.get(metric, 0) + n
        if paper_id in in_scope_pids:
            papers_with[metric].add(paper_id)
    issues_by = [{"check_name": k, "total": n} for k, n in uow.sub.issue_counts_by_check(corpus.id)]
    total = len(in_scope_pids)
    spec = get_domain(corpus.collection)
    scored = spec.scored_metrics
    if scored is None:  # rag: оцениваются все метрики - в coverage топ метрик по числу статей
        top = sorted(per_metric, key=lambda m: (-len(papers_with.get(m, ())), -per_metric[m], m))
        top = [m for m in top if m][:RAG_TOP_METRICS]
        coverage = [{"metric": m, "n": len(papers_with.get(m, ())), "total": total,
                     "results": per_metric.get(m, 0)} for m in top]
        scored_results = sum(v["results"] for v in views)
    else:
        coverage = [{"metric": m, "n": len(papers_with.get(m, ())), "total": total,
                     "results": per_metric.get(m, 0)} for m in scored]
        scored_results = sum(per_metric.get(m, 0) for m in scored)
    return {
        "files": by_status["files"],
        "papers_in_scope": by_status["done"] + by_status["nodata"] + by_status.get("ocr", 0),
        "papers_out": by_status["out"], "files_without_results": by_status["nodata"],
        "files_error": by_status["error"],
        "results": sum(v["results"] for v in views),
        "scored_results": scored_results,
        "issues_total": sum(v["issues"] for v in views),
        "coverage": coverage,
        "issues_by_check": issues_by,
    }
