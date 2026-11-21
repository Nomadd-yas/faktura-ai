"""Данные ядра (CORE_API §3): статья (7 таблиц), PDF, GET /results, GET /issues."""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response, StreamingResponse

from ...domain.entities import CorpusFileStatus, CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from ...domain.registry import get_domain
from ..base.deps import current_user, get_services
from ..corpus.views import RunData, file_status, get_corpus, run_id_of, user_corpora

log = logging.getLogger("faktura.api")
router = APIRouter(tags=["data"])

_SORTS = ("publication_id", "metric", "value", "page")
_NATIVE_SORT = {"publication_id": "publication", "metric": "metric", "value": "value", "page": "page"}
_SOLAR = get_domain("solar")
TABLES = _SOLAR.tables


# --- поиск статьи ------------------------------------------------------------------------------

def _find(uow, user: CurrentUser, pid: str, run: str | None):
    """(корпус, статья, файл корпуса) или 404. Без `run` - последний разбор пользователя, где есть статья."""
    corpora = [get_corpus(uow, user, run)] if run else user_corpora(uow, user)
    for c in corpora:
        paper = uow.papers.get_by_publication(c.id, pid)
        if paper is not None:
            f = next((x for x in uow.files.list(c.id) if x.publication_id == pid
                      and x.status != CorpusFileStatus.skipped_duplicate), None)
            return c, paper, f
    raise AppError("not_found", f"Статья {pid} не найдена")


def _extras(uow, paper) -> dict[str, Any]:
    """Подсказки интерфейсу по статье: подписи приборов, название, quote_found, замечания, повторы."""
    tables = uow.sub.paper_tables(paper.id)
    pub = tables["publications"][0] if tables["publications"] else {}
    quotes: dict[str, bool | None] = uow.sub.paper_quotes(paper.id)
    if tables["results"] and all(v is None for v in quotes.values()):
        try:  # старые данные (до sub_rows.quote_found): считаем по записям статьи
            bundle = uow.papers.load_bundle(paper.id, apply_review=False)
            quotes = {r.result_id: r.meta.quote_found for r in bundle.results}
        except Exception:  # noqa: BLE001 - подсказка необязательна
            log.debug("quote_found недоступен для %s", paper.id, exc_info=True)
    issue_ids: dict[str, list[str]] = {}
    for i in tables["issues"]:
        for rid in [x for x in (i.get("record_ids") or "").split(";") if x]:
            issue_ids.setdefault(rid, []).append(i["issue_id"])
    dups: dict[str, list[str]] = {}
    for r in tables["results"]:
        if r.get("duplicate_of"):
            dups.setdefault(r["duplicate_of"], []).append(r["result_id"])
    return {"tables": tables, "title": pub.get("title", ""),
            "labels": {d["device_id"]: d.get("device_label", "") for d in tables["devices"]},
            "quotes": quotes, "issue_ids": issue_ids, "dups": dups}


def _result_meta(x: dict, rid: str, with_dups: bool) -> dict[str, Any]:
    m: dict[str, Any] = {"quote_found": x["quotes"].get(rid), "issue_ids": x["issue_ids"].get(rid, [])}
    if with_dups:
        m["duplicates"] = x["dups"].get(rid, [])
    return m


@router.get("/papers/solar/{pid}", summary="Все 7 таблиц статьи")
def get_paper(pid: str, run: str | None = None, user: CurrentUser = Depends(current_user),
              services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus, paper, f = _find(uow, user, pid, run)
        d = RunData(uow, corpus)
        x = _extras(uow, paper)
        t = x["tables"]
        pub = t["publications"][0] if t["publications"] else {
            "publication_id": pid, "title": "", "year": "", "DOI": "", "authors": "", "in_scope": "",
            "exclusion_reason": ""}
        fstatus = "queued"
        if f is not None:
            fstatus, _ = file_status(f, paper, d.results_n.get(paper.id, 0), d.pub.get(paper.id))
        if fstatus == "out":
            status = "out"
            pub = {**pub, "in_scope": "false"}
            t = {k: ([pub] if k == "publications" else []) for k in TABLES}
        elif fstatus == "done":
            status = "has_data"
        else:
            status = "nodata"
        run_ids = [run_id_of(c.id) for c in user_corpora(uow, user)
                   if uow.papers.get_by_publication(c.id, pid) is not None]
        return {
            "publication": pub, "materials": t["materials"], "devices": t["devices"], "layers": t["layers"],
            "measurements": t["measurements"], "results": t["results"], "issues": t["issues"],
            "paper_status": status,
            "meta": {"file_status": fstatus, "run_ids": run_ids, "pdf_pages": paper.page_count,
                     "results": {r["result_id"]: _result_meta(x, r["result_id"], True) for r in t["results"]}},
        }


# --- PDF ---------------------------------------------------------------------------------------

def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    if not header:
        return None
    m = re.fullmatch(r"\s*bytes=(\d*)-(\d*)\s*", header)
    if not m or (not m.group(1) and not m.group(2)):
        return None
    if not m.group(1):  # последние N байт
        n = int(m.group(2))
        return (max(size - n, 0), size - 1) if n else None
    start = int(m.group(1))
    end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
    if start >= size or end < start:
        raise AppError("range_not_satisfiable", "Запрошенный диапазон вне файла", http_status=416)
    return start, end


def _iter_file(fh, start: int, length: int, chunk: int = 1 << 20):
    try:
        fh.seek(start)
        left = length
        while left > 0:
            data = fh.read(min(chunk, left))
            if not data:
                break
            left -= len(data)
            yield data
    finally:
        fh.close()


@router.get("/papers/solar/{pid}/pdf", summary="Исходный PDF (Range, ETag)")
def get_pdf(pid: str, request: Request, run: str | None = None, user: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        _, paper, f = _find(uow, user, pid, run)
        key = f.storage_key if f is not None else None
        sha = (f.sha256 if f is not None else None) or paper.sha256
    store = services.store
    if not key or not store.exists(key):
        raise AppError("not_found", "PDF-файла нет")
    with store.open(key) as probe:
        probe.seek(0, 2)
        size = probe.tell()
    etag = f'"{sha or key}"'
    base = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "private, max-age=0, must-revalidate",
            "Content-Disposition": f'inline; filename="{pid}.pdf"'}
    inm = request.headers.get("if-none-match")
    if inm and etag in [t.strip() for t in inm.split(",")]:
        return Response(status_code=304, headers=base)
    rng = _parse_range(request.headers.get("range"), size)
    ir = request.headers.get("if-range")
    if rng is not None and ir and ir.strip() != etag:
        rng = None
    if rng is None:
        return StreamingResponse(_iter_file(store.open(key), 0, size), media_type="application/pdf",
                                 headers={**base, "Content-Length": str(size)})
    start, end = rng
    return StreamingResponse(_iter_file(store.open(key), start, end - start + 1), status_code=206,
                             media_type="application/pdf",
                             headers={**base, "Content-Range": f"bytes {start}-{end}/{size}",
                                      "Content-Length": str(end - start + 1)})


# --- /results, /issues -------------------------------------------------------------------------

def _need_run(run: str | None) -> str:
    if not run:
        raise AppError("validation_error", "Параметр «run» обязателен", field="run")
    return run


@router.get("/results", summary="Строки results разбора")
def list_results(run: str | None = None, pid: list[str] = Query(default=[]), metric: list[str] = Query(default=[]),
                 scored: bool = False, device_id: str | None = None, measurement_id: str | None = None,
                 material_id: str | None = None, duplicates: str = Query("include", pattern="^(include|exclude)$"),
                 sort: str | None = None, dir: str = Query("asc", pattern="^(asc|desc)$"),
                 limit: int = Query(100, ge=1, le=1000), cursor: str | None = None,
                 user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    run = _need_run(run)
    if sort is not None and sort not in _SORTS:
        raise AppError("validation_error", f"sort: одно из {', '.join(_SORTS)}", field="sort")
    metrics: list[str] | None = list(metric) or None
    if scored:
        scored_set = _SOLAR.scored_metrics or ()
        metrics = [m for m in (metrics or scored_set) if m in scored_set] or ["\x00"]
    flt = dict(pids=pid or None, metrics=metrics, device_id=device_id, measurement_id=measurement_id,
               material_id=material_id, exclude_duplicates=duplicates == "exclude")
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run)
        native = _NATIVE_SORT.get(sort or "", "seq" if dir == "asc" else "publication")
        rows, total, nxt = uow.sub.list_results(corpus.id, **flt, sort=native, dir=dir, limit=limit, cursor=cursor)
        by_pid = {p.publication_id: p for p in uow.papers.list(corpus_id=corpus.id)}
        cache: dict[str, dict] = {}
        data = []
        for r in rows:
            p = by_pid.get(r["publication_id"])
            x = cache.get(r["publication_id"])
            if x is None and p is not None:
                x = cache[r["publication_id"]] = _extras(uow, p)
            x = x or {"labels": {}, "title": "", "quotes": {}, "issue_ids": {}, "dups": {}}
            data.append({"row": r, "device_label": x["labels"].get(r["device_id"], ""), "paper_title": x["title"],
                         "meta": _result_meta(x, r["result_id"], False)})
    return {"data": data, "total": total, "next_cursor": nxt}


@router.get("/issues", summary="Строки issues разбора")
def list_issues(run: str | None = None, pid: list[str] = Query(default=[]),
                check_name: list[str] = Query(default=[]), limit: int = Query(100, ge=1, le=1000),
                cursor: str | None = None, user: CurrentUser = Depends(current_user),
                services: Services = Depends(get_services)):
    run = _need_run(run)
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run)
        rows, total, nxt = uow.sub.list_issues(corpus.id, pids=pid or None, check_names=check_name or None,
                                               limit=limit, cursor=cursor)
        titles: dict[str, str] = {}
        for p in uow.papers.list(corpus_id=corpus.id):
            if p.publication_id in {r["publication_id"] for r in rows}:
                pubs = uow.sub.paper_tables(p.id)["publications"]
                titles[p.publication_id] = pubs[0]["title"] if pubs else ""
    return {"data": [{"row": r, "paper_title": titles.get(r["publication_id"], "")} for r in rows],
            "total": total, "next_cursor": nxt}
