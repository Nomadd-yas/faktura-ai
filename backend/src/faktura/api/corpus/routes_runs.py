"""Разбор (Run): POST/GET /runs, GET /runs/{id}, /runs/{id}/summary, /runs/{id}/submission.zip (CORE_API §2, §4)."""
from __future__ import annotations

import io
import logging
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ...domain.entities import CorpusFileStatus, CurrentUser, RunStatus
from ...domain.errors import AppError
from ...domain.services import Services
from ...domain.registry import DOMAINS, get_domain
from ...pdfio.inputs import CollectResult, ZipLimits, collect_inputs
from ...storage import decode_cursor, encode_cursor
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_services, upload_rate_limit
from .passports import run_passport
from .views import (DOMAIN, CORPUS_KIND, RunData, corpus_id_of, default_title, get_corpus, run_id_of, run_summary,
                    run_view, user_corpora)

log = logging.getLogger("faktura.api")
TABLES = get_domain("solar").tables
router = APIRouter(tags=["runs"])

_REJECT_CODES = {"duplicate_name": "duplicate", "pdf_broken": "broken_pdf", "pdf_encrypted": "encrypted_pdf",
                 "pdf_no_text": "broken_pdf"}
_NOT_PDF_MSG = "Можно загрузить только PDF"
_MB = 1024 * 1024


def _safe_rel(name: str | None) -> str | None:
    """Относительный путь из имени загруженного файла (для папки - с подпапками); без `..`, дисков и пустых частей."""
    parts = [p for p in re.split(r"[\\/]+", name or "") if p not in ("", ".", "..") and ":" not in p]
    return "/".join(parts) if parts else None


def _reject_row(name: str, code: str, message: str) -> dict:
    return {"name": name, "code": _REJECT_CODES.get(code, code), "message": message}


def _save_uploads(files: list[UploadFile], root: Path, limit_bytes: int, limit_files: int):
    """Файлы -> папка root. Возвращает отклонённые заранее (пустые, повторы пути, мусорные имена)."""
    rejected: list[dict] = []
    total = 0
    if len(files) > limit_files:
        raise AppError("too_large", f"Слишком много файлов: не больше {limit_files} за раз")
    for up in files:
        rel = _safe_rel(up.filename)
        if rel is None:
            rejected.append(_reject_row(up.filename or "(без имени)", "unsupported_file", _NOT_PDF_MSG))
            continue
        dest = root / rel
        if dest.exists():
            rejected.append(_reject_row(rel, "duplicate", f"{rel}: файл с таким путём уже передан"))
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as out:
            shutil.copyfileobj(up.file, out, _MB)
        size = dest.stat().st_size
        total += size
        if total > limit_bytes:
            raise AppError("too_large", f"Загрузка больше {limit_bytes // _MB} МБ")
        if size == 0:
            dest.unlink()
            rejected.append(_reject_row(rel, "empty_file", f"{rel}: пустой файл"))
    return rejected


def _collect(services: Services, work: Path, files: list[UploadFile], archive: UploadFile | None
             ) -> tuple[CollectResult, list[dict], str | None]:
    s = services.settings
    pre: list[dict] = []
    limits = ZipLimits.from_settings(s)
    if archive is not None:
        name = _safe_rel(archive.filename) or "archive.zip"
        name = name.rsplit("/", 1)[-1]
        if not name.lower().endswith(".zip"):
            raise AppError("unsupported_media_type", "Архив должен быть в формате ZIP")
        zp = work / "up" / name
        pre = _save_uploads([archive], zp.parent, s.max_zip_mb * _MB, 1)
        if not zp.exists():
            raise AppError("no_files", "Архив пустой")
        res = collect_inputs(zp, limits=limits, work_dir=work / "unz")
        if not res.files:
            top = [r for r in res.rejected if r.name == name]
            if top:
                if top[0].code == "zip_bomb":
                    raise AppError("zip_bomb", top[0].message, http_status=422)
                if top[0].code == "zip_too_large":
                    raise AppError("too_large", top[0].message)
                raise AppError("unsupported_media_type", top[0].message)
        if files:  # ZIP и отдельные PDF в одном запросе: объединяем
            root = work / "up_files"
            root.mkdir(parents=True, exist_ok=True)
            pre += _save_uploads(files, root, s.max_upload_mb * _MB, s.max_upload_files)
            extra = collect_inputs(root, limits=limits, work_dir=work / "unz_files")
            res.files.extend(extra.files)
            res.rejected.extend(extra.rejected)
        return res, pre, name
    root = work / "up"
    root.mkdir(parents=True, exist_ok=True)
    pre = _save_uploads(files, root, s.max_upload_mb * _MB, s.max_upload_files)
    return collect_inputs(root, limits=limits, work_dir=work / "unz"), pre, None


@router.post("/runs", status_code=202, summary="Создать разбор (загрузка PDF/ZIP)")
def create_run(request: Request, file: list[UploadFile] | None = File(default=None),
               archive: UploadFile | None = File(default=None), domain: str = Form("solar"),
               title: str | None = Form(default=None), user: CurrentUser = Depends(upload_rate_limit),
               services: Services = Depends(get_services)):
    from ...pipeline import CorpusService, RunService

    if domain not in DOMAINS:
        raise AppError("domain_not_supported", field="domain")
    files = [f for f in (file or []) if f is not None and (f.filename or "")]
    if archive is not None and not (archive.filename or ""):
        archive = None
    if not files and archive is None:
        raise AppError("no_files")
    work = Path(tempfile.mkdtemp(prefix="faktura_up_"))
    try:
        collected, pre_rejected, zip_name = _collect(services, work, files, archive)
        for r in collected.rejected:
            if r.code == "unsupported_file":
                r.message = _NOT_PDF_MSG
        cs = CorpusService(services)
        now = datetime.now(timezone.utc)
        n_guess = len(collected.files)
        name = (title or "").strip() or zip_name or default_title(n_guess, now)
        corpus = cs.create(user.workspace_id, user.id, name, domain, kind=CORPUS_KIND)
        try:
            intake = cs.add_inputs(corpus.id, collected, mode="api", dedupe=True, reuse_existing=False,
                                  allow_same_names=True)
        except BaseException:
            _drop(services, corpus.id)
            raise
        rejected = _rejected_rows(pre_rejected, intake)
        if not intake.added:
            _drop(services, corpus.id)
            raise AppError("no_files", "Среди загруженного нет подходящих PDF", details={"rejected": rejected})
        if not (title or "").strip() and not zip_name:
            with services.db.uow() as uow:
                uow.corpora.update(corpus.id, name=default_title(len(intake.added), now))
        rs = RunService(services)
        run = rs.start(corpus.id, "full", user_id=user.id)
        rs.submit(run.id)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with services.db.uow() as uow:
        body = run_view(uow, uow.corpora.get(corpus.id), cap=_cap(services))
    body["rejected"] = rejected
    return JSONResponse(body, status_code=202, media_type=JSON_MEDIA_TYPE,
                        headers={"Location": f"/api/v1/runs/{run_id_of(corpus.id)}"})


def _rejected_rows(pre_rejected: list[dict], intake) -> list[dict]:
    """Общий формат `rejected` для POST /runs и POST /runs/{id}/files."""
    return [*pre_rejected,
            *[_reject_row(r.rel_path or r.name, _intake_code(r), r.message) for r in intake.rejected],
            *[_reject_row(f.source or f.name, "duplicate",
                          f"{f.source or f.name}: такой же файл (SHA-256) уже есть в этом разборе")
              for f in intake.skipped_duplicates]]


@router.post("/runs/{run_id}/files", status_code=202, summary="Догрузить файлы в существующий разбор")
def add_run_files(run_id: str, request: Request, file: list[UploadFile] | None = File(default=None),
                  archive: UploadFile | None = File(default=None), user: CurrentUser = Depends(upload_rate_limit),
                  services: Services = Depends(get_services)):
    """Те же поля, лимиты и `rejected`, что у POST /runs. Новые файлы встают в очередь: к идущему разбору они
    дописываются (разбор не перезапускается), завершённый запускается заново только для новых файлов."""
    from ...domain.entities import RunKind
    from ...pipeline import CorpusService, RunService

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        active = uow.runs.active_for_corpus(corpus.id)
        if active is not None and active.status == RunStatus.cancelling:
            raise AppError("run_in_progress", "Разбор останавливается — подождите завершения")
    files = [f for f in (file or []) if f is not None and (f.filename or "")]
    if archive is not None and not (archive.filename or ""):
        archive = None
    if not files and archive is None:
        raise AppError("no_files")
    work = Path(tempfile.mkdtemp(prefix="faktura_up_"))
    try:
        collected, pre_rejected, _ = _collect(services, work, files, archive)
        for r in collected.rejected:
            if r.code == "unsupported_file":
                r.message = _NOT_PDF_MSG
        intake = CorpusService(services).add_inputs(corpus.id, collected, mode="api", dedupe=True,
                                                    reuse_existing=False, allow_same_names=True)
        rejected = _rejected_rows(pre_rejected, intake)
        if not intake.added:
            raise AppError("no_files", "Среди загруженного нет подходящих PDF", details={"rejected": rejected})
        ids = [f.id for f in intake.added]
        rs = RunService(services)
        run_pk = None
        with services.db.uow() as uow:
            active = uow.runs.active_for_corpus(corpus.id)
            if active is not None and active.status in (RunStatus.queued, RunStatus.running):
                cur = list(active.file_ids or [])
                for i in ids:
                    if i not in cur:
                        cur.append(i)
                        uow.files.update_status(i, CorpusFileStatus.queued)
                from ...pipeline.runs import _counts  # noqa: PLC0415 - счётчики прогона по его файлам
                uow.runs.update(active.id, file_ids=cur, counts=_counts(uow.files.list(corpus.id, ids=cur)))
                run_pk = active.id
        if run_pk is None:  # разбор завершён: новый прогон только по новым файлам
            run_pk = rs.start(corpus.id, RunKind.selected, file_ids=ids, user_id=user.id).id
        rs.submit(run_pk)  # повторный submit уже идущего прогона безопасен
    finally:
        shutil.rmtree(work, ignore_errors=True)
    with services.db.uow() as uow:
        body = run_view(uow, uow.corpora.get(corpus.id), cap=_cap(services))
    body["rejected"] = rejected
    return JSONResponse(body, status_code=202, media_type=JSON_MEDIA_TYPE,
                        headers={"Location": f"/api/v1/runs/{run_id_of(corpus.id)}"})


def _cap(services: Services) -> int | None:
    """Предел параллелизма разбора для ETA: одновременных вызовов ML (мок не ограничен)."""
    st = services.settings
    return None if st.extractor == "mock" else max(1, int(st.extract_concurrency))


def _intake_code(r) -> str:
    """PDF по расширению, но содержимое не PDF - это битый файл, а не «не тот формат»."""
    if r.code == "unsupported_file" and r.name.lower().endswith(".pdf"):
        return "broken_pdf"
    return r.code


def _drop(services: Services, corpus_id: str) -> None:
    try:
        with services.db.uow() as uow:
            uow.corpora.soft_delete(corpus_id)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось убрать пустой разбор %s", corpus_id)


@router.get("/runs", summary="Список разборов")
def list_runs(limit: int = Query(20, ge=1, le=200), cursor: str | None = None, files: bool = True,
              user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    offset = 0
    if cursor:
        cur = decode_cursor(cursor)
        if len(cur) != 2 or cur[0] != "runs" or not isinstance(cur[1], int) or cur[1] < 0:
            raise AppError("validation_error", "Некорректный курсор", field="cursor")
        offset = cur[1]
    with services.db.uow() as uow:
        corpora = user_corpora(uow, user, pinned_first=True)
        page = corpora[offset:offset + limit]
        data = [run_view(uow, c, files="short" if files else "none", cap=_cap(services)) for c in page]
    nxt = encode_cursor(["runs", offset + limit]) if offset + limit < len(corpora) else None
    return {"data": data, "total": len(corpora), "next_cursor": nxt}


@router.get("/runs/{run_id}", summary="Разбор целиком")
def get_run(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        return run_view(uow, get_corpus(uow, user, run_id), cap=_cap(services))


@router.get("/runs/{run_id}/summary", summary="Сводка разбора")
def get_summary(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        return run_summary(uow, get_corpus(uow, user, run_id))


def _export_name(title: str | None) -> str:
    """«Набор для сдачи — {title}.zip» без символов, недопустимых в именах файлов."""
    stem = re.sub(r'[\\/:*?"<>|\r\n]+', "_", (title or "").strip()).strip(" .")
    return f"Набор для сдачи — {stem}.zip" if stem else "Набор для сдачи.zip"


def _run_check(services: Services, tables: dict[str, list[dict]], pdf_keys: list[tuple[str, str]],
               domain: str = "solar", ocr: bool = False):
    """check_submission.py области на выгруженных CSV и PDF разбора (во временной папке). Скрипт - по области
    (solar: settings.check_script, rag: settings.rag_check_script). rag: коды выхода 0/1/2, `--ocr` для сканов."""
    from ...config import domain_paths
    from ...export import run_check_script, run_rag_check, table_bytes

    spec = get_domain(domain)
    script = domain_paths(services.settings, domain)[2]
    if script is None or not Path(script).is_file():
        raise AppError("extractor_unavailable", "Проверка недоступна: скрипт проверки не настроен", http_status=503)
    tmp = Path(tempfile.mkdtemp(prefix="faktura_zipcheck_"))
    try:
        csv_dir, pdf_dir = tmp / "csv", tmp / "pdfs"
        csv_dir.mkdir()
        pdf_dir.mkdir()
        for t, cols in spec.tables.items():
            (csv_dir / f"{t}.csv").write_bytes(table_bytes(t, tables[t], cols))
        for pid, key in pdf_keys:
            try:
                services.store.copy_to(key, pdf_dir / f"{pid}.pdf")
            except Exception:  # noqa: BLE001
                log.warning("PDF %s не скопирован для проверки", pid)
        if domain == "solar":
            return run_check_script(Path(script), csv_dir, pdf_dir, timeout_s=services.settings.check_timeout_s)
        return run_rag_check(Path(script), csv_dir, pdf_dir, timeout_s=services.settings.check_timeout_s, ocr=ocr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@router.get("/runs/{run_id}/submission.zip", summary="Набор для сдачи: 7 CSV, PASSPORT.md, passport.json")
def submission_zip(run_id: str, partial: bool = False, check: bool = False,
                   user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    from ...export import check_status, table_bytes
    from ...passport import passport_json_bytes, passport_md_bytes

    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        d = RunData(uow, corpus)
        spec = get_domain(corpus.collection)
        TABLES = spec.tables
        if d.run is not None and d.run.status in (RunStatus.queued, RunStatus.running, RunStatus.cancelling) \
                and not partial:
            raise AppError("run_in_progress")
        tables: dict[str, list[dict]] = {t: [] for t in TABLES}
        any_scan = False
        seen: set[str] = set()
        papers_n = 0
        pdf_keys: list[tuple[str, str]] = []
        for f in d.files:
            if f.publication_id in seen:
                continue
            paper = d.papers.get(f.paper_id) if f.paper_id else None
            done = paper is not None and f.status == CorpusFileStatus.done and paper.status.value == "done"
            if not done:
                if partial and f.status in (CorpusFileStatus.pending, CorpusFileStatus.queued,
                                            CorpusFileStatus.processing, CorpusFileStatus.downloading):
                    continue
                if spec.name == "solar":
                    tables["publications"].append({"publication_id": f.publication_id})  # упавшая: только id
                    seen.add(f.publication_id)
                    papers_n += 1
                # rag: у упавшей статьи нет данных, строка без in_scope не прошла бы проверку - в выгрузку не входит
                continue
            seen.add(f.publication_id)
            papers_n += 1
            if f.storage_key:
                pdf_keys.append((f.publication_id, f.storage_key))
            any_scan = any_scan or bool(f.is_scan)
            from ...review.runs_v2 import export_paper_tables  # v2: правки и состояния замечаний (§9)
            for t, rows in export_paper_tables(uow, corpus.id, spec.name, paper.id).items():
                tables[t].extend(rows)
        title = corpus.name
        passport = run_passport(services, d)
    headers = {}
    report = None
    if check:
        import shutil as _sh

        report = _run_check(services, tables, pdf_keys, spec.name, ocr=any_scan and _sh.which("tesseract") is not None)
        status = check_status(report)
        if status == "failed":
            raise AppError("submission_invalid", "Набор не прошёл проверку check_submission.py", details={
                "errors": report.errors[:50], "errors_total": len(report.errors), "crashed": report.crashed,
                "warnings": len(report.warnings), "report": (report.raw_output or "")[-4000:]})
        headers["X-Check-Status"] = status or "ok"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for t, cols in TABLES.items():
            z.writestr(f"{t}.csv", table_bytes(t, tables[t], cols))
        z.writestr("PASSPORT.md", passport_md_bytes(passport))
        z.writestr("passport.json", passport_json_bytes(passport))
        if report is not None:
            z.writestr("check_report.txt", (report.raw_output or "").encode("utf-8"))
    filename = _export_name(title)
    return Response(buf.getvalue(), media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=\"submission.zip\"; filename*=UTF-8''{quote(filename, safe='')}",
        "X-Export-Tables": str(len(TABLES)), "X-Export-Results": str(len(tables["results"])),
        "X-Export-Papers": str(papers_n), "X-Export-Filename": quote(filename, safe=""), **headers,
    })
