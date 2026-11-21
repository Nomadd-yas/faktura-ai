"""Корпус и приём файлов (план 5.6; REQ-024, REQ-072…REQ-074; api_contract №100, №106, У-30).

Таблица случаев `add_inputs`:

| Случай | mode="api" | mode="cli" |
|---|---|---|
| отказ `inspect_pdf` (битый, зашифрованный, большой, много страниц, не PDF) | `rejected`, файла нет | файл `failed` с кодом; в сдаче строка publications только с id |
| `duplicate_name` | `rejected` | `rejected` (строка publications уже есть у первого файла) |
| тот же sha256 в корпусе | `skipped_duplicate`, `duplicate_of` | не пропускается: своя статья; `duplicate_of`/`dup_kind=file` (без `dedupe` - без пометок) |
| тот же sha256 в другом корпусе (`reuse_existing`) | файл `done`, связан с готовой статьёй, ML не зовётся | не применяется |

Смысловые дубли (DOI, title_key) отмечаются после разбора (`processor.detect_semantic_duplicate`).
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..domain.entities import Corpus, CorpusFile, CorpusFileStatus, DupKind, PaperStatus
from ..domain.errors import AppError, PdfError
from ..domain.services import Services
from ..pdfio.inputs import CollectResult, InputFile, Rejected, dedupe_names, detect_scan, inspect_pdf
from ._pdf import PDF_LOCK

log = logging.getLogger(__name__)

MANUAL_CORPUS_NAME = "Загруженные вручную"


class AddFilesResult(BaseModel):
    added: list[CorpusFile] = Field(default_factory=list)
    rejected: list[Rejected] = Field(default_factory=list)
    skipped_duplicates: list[CorpusFile] = Field(default_factory=list)


def pdf_storage_key(sha256: str) -> str:
    """PDF хранится по содержимому: один файл на sha256 в хранилище."""
    return f"pdf/{sha256[:2]}/{sha256}.pdf"


class CorpusService:
    def __init__(self, services: Services) -> None:
        self.services = services

    # --- корпуса ---------------------------------------------------------------------------------

    def create(self, workspace_id: str, user_id: str, name: str, collection: str, *,
               kind: Literal["regular", "manual", "extract"] = "regular", profile_id: str | None = None,
               project_id: str | None = None) -> Corpus:
        if not name or not str(name).strip():
            raise AppError("validation_error", "Название корпуса обязательно", field="name")
        with self.services.db.uow() as uow:
            return uow.corpora.create(workspace_id=workspace_id, name=str(name).strip(), collection=collection,
                                      kind=kind, profile_id=profile_id, project_id=project_id,
                                      created_by=user_id)

    def manual_corpus(self, workspace_id: str) -> Corpus:
        """Служебный корпус «Загруженные вручную», kind='manual' (один на рабочее пространство)."""
        with self.services.db.uow() as uow:
            found = uow.corpora.list(workspace_id, kind="manual")
            if found:
                return found[0]
            return uow.corpora.create(workspace_id=workspace_id, name=MANUAL_CORPUS_NAME, collection="solar",
                                      kind="manual", created_by=None)

    # --- приём файлов ----------------------------------------------------------------------------

    def add_inputs(self, corpus_id: str, inputs: CollectResult, *, mode: Literal["api", "cli"],
                   dedupe: bool = True, reuse_existing: bool = True, allow_same_names: bool = False
                   ) -> AddFilesResult:
        """Таблица случаев api/cli - в docstring модуля (отказы inspect_pdf, duplicate_name, тот же sha256)."""
        if mode not in ("api", "cli"):
            raise ValueError(f"mode: api | cli, получено {mode!r}")
        s = self.services.settings
        with self.services.db.uow() as uow:
            corpus = uow.corpora.get(corpus_id)
            if corpus is None or corpus.deleted_at is not None:
                raise AppError("not_found", f"Корпус {corpus_id} не найден")
            existing_names = {f.name for f in uow.files.list(corpus_id)}
        result = AddFilesResult(rejected=list(inputs.rejected))
        if allow_same_names:  # API: одинаковые имена допустимы (id вида P042-2), отклоняется только тот же sha256
            files = list(inputs.files)
        else:
            dd = dedupe_names(list(inputs.files), existing_names=existing_names)
            result.rejected.extend(dd.rejected)
            files = dd.files
        for f in files:
            try:
                with PDF_LOCK:
                    info = inspect_pdf(f.path, max_mb=s.max_file_mb, max_pages=s.max_pages)
                    if corpus.collection != "solar":  # у solar сканов нет; для rag признак скана - статус «ocr»
                        info.is_scan = detect_scan(f.path)
            except PdfError as e:
                if mode == "api":
                    result.rejected.append(Rejected(name=f.name, code=e.code, message=e.message, rel_path=f.rel_path))
                else:
                    result.added.append(self._add_failed(corpus, f, e))
                continue
            added, skipped = self._add_one(corpus, f, info, mode=mode, dedupe=dedupe,
                                           reuse_existing=reuse_existing and mode == "api")
            if skipped:
                result.skipped_duplicates.append(added)
            else:
                result.added.append(added)
        return result

    def _add_failed(self, corpus: Corpus, f: InputFile, e: PdfError) -> CorpusFile:
        """CLI: файл, отклонённый проверкой PDF, остаётся в корпусе со статусом failed (REQ-024)."""
        with self.services.db.uow() as uow:
            cf = uow.files.add(corpus_id=corpus.id, name=f.name, publication_id=f.publication_id,
                               status=CorpusFileStatus.failed, error_code=e.code, error_message=e.message,
                               source=f.rel_path)
            paper = uow.papers.create(workspace_id=corpus.workspace_id, corpus_id=corpus.id, file_id=cf.id,
                                      publication_id=f.publication_id, collection=corpus.collection,
                                      status=PaperStatus.failed, error_code=e.code)
            return uow.files.update(cf.id, paper_id=paper.id)

    def _add_one(self, corpus: Corpus, f: InputFile, info, *, mode: str, dedupe: bool,
                 reuse_existing: bool) -> tuple[CorpusFile, bool]:
        store = self.services.store
        key = pdf_storage_key(info.sha256)
        with self.services.db.uow() as uow:
            same = [x for x in uow.files.list(corpus.id)
                    if x.sha256 == info.sha256 and x.status != CorpusFileStatus.skipped_duplicate]
            common = dict(corpus_id=corpus.id, name=f.name, publication_id=f.publication_id, sha256=info.sha256,
                          size_bytes=info.size_bytes, page_count=info.page_count, source=f.rel_path,
                          is_scan=getattr(info, "is_scan", None))
            if same and mode == "api":
                orig = same[0]
                cf = uow.files.add(**common, status=CorpusFileStatus.skipped_duplicate, duplicate_of=orig.id,
                                   dup_kind=DupKind.file, paper_id=orig.paper_id, storage_key=orig.storage_key)
                return cf, True
            if reuse_existing:
                linked = self._reusable_paper(uow, corpus, info.sha256)
                if linked is not None:
                    cf = uow.files.add(**common, status=CorpusFileStatus.done, paper_id=linked.id,
                                       storage_key=key if store.exists(key) else None)
                    return cf, False
            if not store.exists(key):
                store.put_file(key, f.path, "application/pdf")
            marks: dict = {}
            if same and dedupe:
                marks = dict(duplicate_of=same[0].id, dup_kind=DupKind.file)
            cf = uow.files.add(**common, status=CorpusFileStatus.pending, storage_key=key, **marks)
            paper = uow.papers.create(workspace_id=corpus.workspace_id, corpus_id=corpus.id, file_id=cf.id,
                                      publication_id=f.publication_id, collection=corpus.collection,
                                      status=PaperStatus.queued, sha256=info.sha256, page_count=info.page_count)
            return uow.files.update(cf.id, paper_id=paper.id), False

    @staticmethod
    def _reusable_paper(uow, corpus: Corpus, sha256: str):
        """Готовая статья с тем же содержимым из другого корпуса рабочего пространства."""
        for p in uow.papers.find_by_sha(corpus.workspace_id, sha256):
            if p.corpus_id != corpus.id and p.status == PaperStatus.done and p.deleted_at is None:
                return p
        return None
