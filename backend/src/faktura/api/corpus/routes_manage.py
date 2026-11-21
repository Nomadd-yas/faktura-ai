"""Управление разбором (BACKEND_ONLY §7а.3, §7а.4, §7а.6): rerun-as, PATCH/DELETE/restart/stop, passport.json/md."""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.registry import DOMAINS
from ...domain.services import Services
from ...pdfio.inputs import ZipLimits, collect_inputs
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_services
from .passports import run_passport
from .routes_runs import _cap, _drop, _intake_code, _reject_row, _safe_rel
from .views import CORPUS_KIND, RunData, get_corpus, run_id_of, run_view

log = logging.getLogger("faktura.api")
router = APIRouter(tags=["runs"])

_SUFFIX = {"rag": "RAG", "solar": "солнечные"}
_MAX_TITLE = 300


class RerunBody(BaseModel):
    domain: str | None = None
    file_ids: list[str] = []
    title: str | None = None


class PatchBody(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class RestartBody(BaseModel):
    file_ids: list[str] | None = None


def _bad(message: str, field: str | None = None) -> AppError:
    return AppError("validation_error", message, http_status=422, field=field)


def _by_public_id(d: RunData, file_ids: list[str]):
    """RunFile.id -> CorpusFile (404, если такого файла нет в разборе); повторы id убираются."""
    pub = {v: k for k, v in d.file_ids.items()}
    internal = {f.id: f for f in d.files}
    out = []
    for fid in dict.fromkeys(file_ids):
        f = internal.get(pub.get(fid, ""))
        if f is None:
            raise AppError("not_found", f"Файл {fid} не найден в разборе", field="file_ids")
        out.append(f)
    return out


def _in_progress(d: RunData) -> bool:
    return d.status([d.file_view(f) for f in d.files]) in ("queued", "running")


def _started(services: Services, corpus_id: str, extra: dict | None = None) -> JSONResponse:
    with services.db.uow() as uow:
        body = run_view(uow, uow.corpora.get(corpus_id), cap=_cap(services))
    body.update(extra or {})
    return JSONResponse(body, status_code=202, media_type=JSON_MEDIA_TYPE,
                        headers={"Location": f"/api/v1/runs/{run_id_of(corpus_id)}"})


@router.post("/runs/{run_id}/rerun-as", status_code=202, summary="Разобрать файлы разбора заново в другой области")
def rerun_as(run_id: str, body: RerunBody, user: CurrentUser = Depends(current_user),
             services: Services = Depends(get_services)):
    from ...pipeline import CorpusService, RunService

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        d = RunData(uow, corpus)
        domain = (body.domain or "").strip()
        if not domain:
            raise _bad("Укажите область: solar или rag", "domain")
        if domain not in DOMAINS:
            raise AppError("domain_not_supported", field="domain")
        if domain == corpus.collection:
            raise _bad("Это та же область, что у исходного разбора", "domain")
        if not body.file_ids:
            raise _bad("Выберите хотя бы один файл", "file_ids")
        if _in_progress(d):
            raise AppError("run_in_progress", "Исходный разбор ещё идёт — дождитесь завершения")
        files = _by_public_id(d, body.file_ids)
        base_title = corpus.name or run_view(uow, corpus, files="none")["title"]
        keys = [(f, f.storage_key) for f in files]
    title = (body.title or "").strip() or f"{base_title} · {_SUFFIX[domain]}"
    if len(title) > _MAX_TITLE:
        raise _bad(f"Название длиннее {_MAX_TITLE} символов", "title")
    store = services.store
    work = Path(tempfile.mkdtemp(prefix="faktura_rerun_"))
    try:
        root = work / "up"
        root.mkdir()
        for f, key in keys:
            if not key or not store.exists(key):
                raise _bad(f"Файл {f.source or f.name} не был принят: PDF нет в хранилище", "file_ids")
            rel = _safe_rel(f.source or f.name) or f"{f.publication_id}.pdf"
            if not rel.lower().endswith(".pdf"):
                rel += ".pdf"
            dest = root / rel
            n = 1
            while dest.exists():  # одинаковые имена из разных папок исходного разбора
                n += 1
                dest = root / re.sub(r"(\.pdf)$", rf"-{n}\1", rel, flags=re.I)
            dest.parent.mkdir(parents=True, exist_ok=True)
            store.copy_to(key, dest)
        collected = collect_inputs(root, limits=ZipLimits.from_settings(services.settings), work_dir=work / "unz")
        cs = CorpusService(services)
        new = cs.create(user.workspace_id, user.id, title, domain, kind=CORPUS_KIND)
        try:
            intake = cs.add_inputs(new.id, collected, mode="api", dedupe=True, reuse_existing=False,
                                   allow_same_names=True)
        except BaseException:
            _drop(services, new.id)
            raise
        rejected = [*[_reject_row(r.rel_path or r.name, _intake_code(r), r.message) for r in intake.rejected],
                    *[_reject_row(f.source or f.name, "duplicate", f"{f.source or f.name}: такой же файл уже есть")
                      for f in intake.skipped_duplicates]]
        if not intake.added:
            _drop(services, new.id)
            raise AppError("no_files", "Среди выбранных нет подходящих PDF", details={"rejected": rejected})
        rs = RunService(services)
        run = rs.start(new.id, "full", user_id=user.id)
        rs.submit(run.id)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _started(services, new.id, {"rejected": rejected})


@router.patch("/runs/{run_id}", summary="Переименовать или закрепить разбор")
def patch_run(run_id: str, body: PatchBody, user: CurrentUser = Depends(current_user),
              services: Services = Depends(get_services)):
    fields: dict = {}
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise _bad("Название не может быть пустым", "title")
        if len(title) > _MAX_TITLE:
            raise _bad(f"Название длиннее {_MAX_TITLE} символов", "title")
        fields["name"] = title
    if body.pinned is not None:
        fields["pinned"] = body.pinned
    if not fields:
        raise _bad("Передайте title или pinned")
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        corpus = uow.corpora.update(corpus.id, **fields)
        return run_view(uow, corpus, cap=_cap(services))


@router.delete("/runs/{run_id}", status_code=204, summary="Удалить разбор (мягко)")
def delete_run(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    from ...pipeline import RunService

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        active = uow.runs.active_for_corpus(corpus.id)
    if active is not None:
        RunService(services).stop(active.id)  # идущий разбор сначала останавливается
    with services.db.uow() as uow:
        uow.corpora.soft_delete(corpus.id)
    return Response(status_code=204)


@router.post("/runs/{run_id}/restart", status_code=202, summary="Перезапустить разбор (все файлы или выбранные)")
def restart_run(run_id: str, body: RestartBody | None = None, user: CurrentUser = Depends(current_user),
                services: Services = Depends(get_services)):
    from ...pipeline import RunService

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        d = RunData(uow, corpus)
        ids = None
        if body is not None and body.file_ids is not None:
            if not body.file_ids:
                raise _bad("Список file_ids пуст: уберите поле, чтобы перезапустить всё", "file_ids")
            ids = [f.id for f in _by_public_id(d, body.file_ids)]
        if _in_progress(d):
            raise AppError("run_in_progress", "Разбор ещё идёт — сначала остановите его")
    rs = RunService(services)
    try:
        run = rs.start(corpus.id, "selected" if ids else "full", file_ids=ids, user_id=user.id)
    except AppError as e:
        if e.code == "conflict":
            raise AppError("run_in_progress", "Разбор ещё идёт — сначала остановите его") from e
        raise
    rs.submit(run.id)
    return _started(services, corpus.id)


@router.post("/runs/{run_id}/stop", summary="Остановить разбор")
def stop_run(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    from ...pipeline import RunService

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        run = RunData(uow, corpus).run
    if run is not None:
        RunService(services).stop(run.id)
    with services.db.uow() as uow:
        return run_view(uow, uow.corpora.get(corpus.id), cap=_cap(services))


@router.get("/runs/{run_id}/passport.json", summary="Паспорт набора (тот же passport.json, что в submission.zip)")
def passport_json(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    from ...passport import passport_json_bytes

    with services.db.uow() as uow:
        passport = run_passport(services, RunData(uow, get_corpus(uow, user, run_id)))
    return Response(passport_json_bytes(passport), media_type=JSON_MEDIA_TYPE)


@router.get("/runs/{run_id}/passport.md", summary="Паспорт набора в Markdown (тот же PASSPORT.md, что в submission.zip)")
def passport_md(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    from ...passport import passport_md_bytes

    with services.db.uow() as uow:
        passport = run_passport(services, RunData(uow, get_corpus(uow, user, run_id)))
    return Response(passport_md_bytes(passport), media_type="text/markdown; charset=utf-8")
