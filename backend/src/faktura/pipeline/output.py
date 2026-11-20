"""Сборка выходной папки: одна функция для CLI, POST /exports и POST /extract (план 5.6; spec/13 §13.6; REQ-002…REQ-009).

1. Файлы корпуса в порядке приёма; статья `done` -> `load_bundle(apply_review=True)` + ручные факты; упавшая или
   не разобранная -> `failed_stub` (строка publications только с publication_id, К-14, REQ-024).
2. `select_for_submission` -> `sanitize` -> `number_issues` -> `write_submission` (ровно 7 CSV в out_dir).
3. PDF для проверки: `pdf_dir` или временная папка `<tmp>/pdfs/<publication_id>.pdf` из FileStore (удаляется).
4. `strict_validate`; `run_check_script`, если задан скрипт.
5. `_meta/`: manifest.json, passport.json, passport.md, files.csv, facts_ui.json, errors.log, check_report.txt.

`exit_code_hint`: 3 - валидатор сдачи нашёл ошибки или упал (без скрипта - ошибки строгого валидатора);
2 - есть упавшие, не разобранные или отклонённые при приёме файлы; иначе 0.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..domain.entities import CorpusFileStatus, FileReport, OutputResult, PaperStatus, Run
from ..domain.errors import AppError
from ..domain.io import tables_from_bundles
from ..domain.profile import ExtractionProfile
from ..domain.records import PaperBundle
from ..domain.services import Services

log = logging.getLogger(__name__)

_NOT_PROCESSED = (CorpusFileStatus.pending, CorpusFileStatus.queued, CorpusFileStatus.processing,
                  CorpusFileStatus.downloading, CorpusFileStatus.cancelled, CorpusFileStatus.needs_ocr)


def _rebrand(bundle: PaperBundle, publication_id: str) -> PaperBundle:
    """Статья, связанная с файлом другого имени (reuse_existing), выгружается под publication_id файла."""
    if bundle.publication_id == publication_id:
        return bundle
    b = bundle.model_copy(deep=True)
    for _, rows in b.iter_tables():
        for r in rows:
            r.publication_id = publication_id
    return b


def _facts_ui_entry(bundle: PaperBundle, paper) -> dict[str, Any]:
    records = []
    for table, rows in bundle.iter_tables():
        if table in ("publications", "issues"):
            continue
        from ..domain.tables import ID_COLUMN

        col = ID_COLUMN[table]
        for r in rows:
            m = r.meta
            if (m.confidence is None and m.bbox is None and m.page is None and m.quote_found is None
                    and m.norm_status is None and m.review_status.value == "new"):
                continue
            rec: dict[str, Any] = {"table": table, "id": getattr(r, col), "confidence": m.confidence,
                                   "page": m.page, "bbox": list(m.bbox) if m.bbox else None,
                                   "review_status": m.review_status.value}
            if table == "results":
                rec["norm_status"] = m.norm_status.value if m.norm_status is not None else None
                rec["quote_found"] = m.quote_found
            records.append(rec)
    issues = [{"key": i.key, "check_name": i.check_name, "computed_by": i.computed_by, "confidence": i.confidence,
               "status": getattr(i.status, "value", i.status)} for i in bundle.issues]
    return {"publication_id": bundle.publication_id, "paper_id": paper.id if paper else None,
            "model_version": paper.model_version if paper else None, "records": records, "issues": issues}


def build_output(services: Services, corpus_id: str, out_dir: Path, *, profile: ExtractionProfile,
                 check_script: Path | None, pdf_dir: Path | None = None, include_meta: bool = True,
                 run: Run | None = None, extra_files: list[FileReport] | None = None,
                 input_info: dict | None = None, formats: tuple[str, ...] = ("csv",)) -> OutputResult:
    """Статьи корпуса (включая failed -> failed_stub) -> load_bundle + manual facts -> select_for_submission ->
    sanitize -> number_issues -> write_submission (ровно 7 CSV) -> strict_validate -> run_check_script ->
    _meta/ (manifest.json, passport.json, passport.md, files.csv, facts_ui.json, errors.log, check_report.txt).

    `extra_files` - строки files.csv для файлов, отклонённых при приёме в CLI (duplicate_name и т. п.);
    `formats` - дополнительно json/xlsx (реляционная выгрузка для людей) в `_meta/`."""
    from ..export import (build_manifest, number_issues, run_check_script, sanitize, select_for_submission,
                          strict_validate, write_meta, write_submission)
    from ..passport import build_passport, passport_markdown

    started = datetime.now(timezone.utc)
    out_dir = Path(out_dir)
    s = services.settings
    errors: list[str] = []
    reports: list[FileReport] = []
    selected: list[PaperBundle] = []
    facts_ui: list[dict] = []
    pdf_sources: list[tuple[str, str]] = []  # (publication_id, storage_key)
    failed_n = 0
    model_versions: set[str] = set()
    any_cache = False
    versions: dict[str, str] = {}

    with services.db.uow() as uow:
        corpus = uow.corpora.get(corpus_id)
        if corpus is None:
            raise AppError("not_found", f"Корпус {corpus_id} не найден")
        files = uow.files.list(corpus_id)
        seen_pids: set[str] = set()
        by_file_id = {f.id: f for f in files}
        for f in files:
            if f.status == CorpusFileStatus.skipped_duplicate:
                orig = by_file_id.get(f.duplicate_of or "")
                reports.append(FileReport(file=f.name, publication_id=f.publication_id, status=f.status.value,
                                          sha256=f.sha256, pages=f.page_count, dup_of=orig.publication_id if orig
                                          else f.duplicate_of, dup_kind="file",
                                          message="тот же файл уже есть в корпусе"))
                continue
            paper = uow.papers.get(f.paper_id) if f.paper_id else None
            pid = f.publication_id
            done = (f.status == CorpusFileStatus.done and paper is not None and paper.status == PaperStatus.done
                    and paper.deleted_at is None)
            if pid in seen_pids:
                errors.append(f"{f.name}: publication_id {pid} уже выгружен другим файлом - строка пропущена")
                continue
            seen_pids.add(pid)
            facts_n = issues_n = 0
            if done:
                bundle = _rebrand(uow.papers.load_bundle(paper.id, apply_review=True), pid)
                manual = uow.facts.manual_for_paper(paper.id)
                if pid != paper.publication_id:
                    manual = [m.model_copy(update={"publication_id": pid}) for m in manual]
                sel = select_for_submission(bundle, profile, manual_facts=manual)
                errors.extend(sel.events)
                selected.append(sel.bundle)
                facts_ui.append(_facts_ui_entry(bundle, paper))
                facts_n = paper.stats.facts_count
                issues_n = len(sel.bundle.issues)
                if paper.model_version:
                    model_versions.add(paper.model_version)
                if (paper.versions or {}).get("from_cache") == "true":
                    any_cache = True
                for k in ("norm", "dictionary", "rules", "parser"):
                    v = (paper.versions or {}).get(k)
                    if v:
                        versions.setdefault(k, v)
            else:
                selected.append(select_for_submission(PaperBundle.failed_stub(pid), profile, failed=True).bundle)
                failed_n += 1
                code = f.error_code or (paper.error_code if paper else None)
                status = f.status.value
                if f.status in _NOT_PROCESSED:
                    code = code or f"not_processed:{status}"
                errors.append(f"{f.name} ({pid}): {status}: {code or 'ошибка'}"
                              + (f" - {f.error_message}" if f.error_message else ""))
            if f.storage_key:
                pdf_sources.append((pid, f.storage_key))
            dup_of, dup_kind = None, None
            if f.dup_kind is not None:
                orig = by_file_id.get(f.duplicate_of or "")
                dup_of, dup_kind = (orig.publication_id if orig else f.duplicate_of), f.dup_kind.value
            elif paper is not None and paper.dup_of and paper.dup_kind is not None:
                ref = uow.papers.get(paper.dup_of)
                dup_of, dup_kind = (ref.publication_id if ref else paper.dup_of), paper.dup_kind.value
            reports.append(FileReport(
                file=f.name, publication_id=pid,
                status="done" if done else ("failed" if f.status == CorpusFileStatus.done else f.status.value),
                error_code=None if done else (f.error_code or (paper.error_code if paper else None)),
                sha256=f.sha256, pages=f.page_count,
                from_cache=bool(paper is not None and (paper.versions or {}).get("from_cache") == "true"),
                dup_of=dup_of, dup_kind=dup_kind, facts=facts_n, issues=issues_n,
                message=None if done else f.error_message))

    for x in extra_files or []:
        reports.append(x)
        errors.append(f"{x.file} ({x.publication_id}): {x.status}: {x.error_code or ''}"
                      + (f" - {x.message}" if x.message else ""))

    san = sanitize(selected)
    errors.extend(f"sanitize: {e}" for e in san.events)
    bundles = [number_issues(b) for b in san.bundles]
    csv_paths = write_submission(bundles, out_dir)
    tables = tables_from_bundles(bundles)
    problems = strict_validate(tables)
    for p in problems:
        if p.level == "error":
            errors.append(f"strict: {p.table or ''}{'' if p.row is None else f'[{p.row}]'} "
                          f"{p.column or ''}: {p.message}".strip())

    check = None
    if check_script is not None:
        tmp_root: Path | None = None
        try:
            if pdf_dir is not None:
                pdfs = Path(pdf_dir)
            else:
                tmp_root = Path(tempfile.mkdtemp(prefix="faktura_check_"))
                pdfs = tmp_root / "pdfs"
                pdfs.mkdir(parents=True, exist_ok=True)
                for pid, key in pdf_sources:
                    try:
                        services.store.copy_to(key, pdfs / f"{pid}.pdf")
                    except Exception as e:  # noqa: BLE001
                        errors.append(f"{pid}: PDF не скопирован для проверки: {e}")
            check = run_check_script(Path(check_script), out_dir, pdfs, timeout_s=s.check_timeout_s)
        finally:
            if tmp_root is not None:
                shutil.rmtree(tmp_root, ignore_errors=True)

    strict_errors = [p for p in problems if p.level == "error"]
    if check is not None and (check.crashed or check.ok is False or check.errors):
        hint = 3
    elif check is None and strict_errors:
        hint = 3
    elif failed_n or extra_files:
        hint = 2
    else:
        hint = 0

    meta_dir = None
    if include_meta:
        result = {
            "publications": len(bundles),
            "in_scope": sum(1 for b in bundles if b.publication.in_scope is True),
            "results": sum(len(b.results) for b in bundles),
            "issues": sum(len(b.issues) for b in bundles),
            "failed": failed_n,
        }
        inp = {"files": len(reports), "accepted": sum(1 for r in reports if r.status not in ("rejected",)),
               "duplicates": sum(1 for r in reports if r.dup_kind == "file"),
               "rejected": sum(1 for r in reports if r.status == "rejected")}
        inp.update(input_info or {})
        mv = sorted(model_versions)
        manifest = build_manifest(
            started_at=run.started_at if run is not None and run.started_at else started,
            finished_at=datetime.now(timezone.utc), domain=corpus.collection, profile=profile,
            model_version=mv[0] if len(mv) == 1 else (", ".join(mv) if mv else None),
            parse_model=versions.get("parser"),
            norm_version=versions.get("norm") or str(getattr(services.normalizer, "version", "") or "") or None,
            rules_version=versions.get("rules"),
            dictionary_version=versions.get("dictionary")
            or str(getattr(services.normalizer, "dictionary_version", "") or "") or None,
            from_cache=any_cache, versions=versions or None, input=inp, result=result, check=check,
            run_id=run.id if run is not None else None, corpus_id=corpus_id)
        passport = build_passport(profile)
        write_meta(out_dir, manifest=manifest, files_rows=reports, facts_ui={"papers": facts_ui}, errors=errors,
                   passport=passport, passport_md=passport_markdown(passport), check_report=check)
        meta_dir = out_dir / "_meta"
        extra = [f for f in formats if f != "csv"]
        if extra:
            from ..export import export_relational

            for fmt in extra:
                try:
                    data = export_relational(bundles, fmt, {})
                    (meta_dir / f"submission.{fmt}").write_bytes(data)
                except Exception as e:  # noqa: BLE001
                    errors.append(f"формат {fmt} не записан: {e}")
                    log.warning("формат %s не записан: %s", fmt, e)
    return OutputResult(csv_paths=list(csv_paths), meta_dir=meta_dir, check=check, problems=problems,
                        exit_code_hint=hint, events=errors)
