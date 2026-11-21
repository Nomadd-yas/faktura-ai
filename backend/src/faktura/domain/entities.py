"""Сущности хранения и API: общие для всех модулей (план 4.3).

Это каркас: модули могут расширять модели необязательными полями, но не менять существующие.
Соответствие spec/03-data-model.md; формат ответов API - docs/wave1/api_contract.md.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from .records import (AddedBy, IssueRecord, IssueStatus, NormStatus, Rect, ReviewStatus, TraceStep)

__all__ = [
    "AddedBy", "IssueStatus", "NormStatus", "ReviewStatus",
    "CorpusFileStatus", "RunKind", "RunStatus", "PaperStatus", "ExtractionStatus", "SourceKind", "DupKind",
    "User", "CurrentUser", "Corpus", "CorpusFile", "RunCounts", "RunOutput", "Run", "Quality", "Coverage",
    "PaperStats", "Paper", "Fact", "Anchor", "Issue", "Action", "Export",
    "FactDraft", "AnchorDraft", "FactFilter", "SortKey", "Page", "FactRow", "IssuesDiff", "TransferResult",
    "FileReport", "OutputResult", "CheckReport", "ValidationProblem", "SaveResult", "ProcessOutcome",
]


class CorpusFileStatus(StrEnum):
    pending = "pending"
    downloading = "downloading"
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"
    skipped_duplicate = "skipped_duplicate"
    needs_ocr = "needs_ocr"
    cancelled = "cancelled"


class RunKind(StrEnum):
    full = "full"
    new_only = "new_only"
    selected = "selected"
    failed_only = "failed_only"
    renormalize = "renormalize"
    recheck = "recheck"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    done_with_errors = "done_with_errors"
    cancelling = "cancelling"
    cancelled = "cancelled"
    failed = "failed"


class PaperStatus(StrEnum):
    queued = "queued"
    processing = "processing"
    done = "done"
    failed = "failed"


class ExtractionStatus(StrEnum):
    extracted = "extracted"
    present_not_extractable = "present_not_extractable"
    absent = "absent"


class SourceKind(StrEnum):
    text = "text"
    table = "table"
    figure = "figure"
    manual = "manual"


class DupKind(StrEnum):
    file = "file"
    doi = "doi"
    title = "title"


# --- пользователи ---------------------------------------------------------------------------

class User(BaseModel):
    id: str
    workspace_id: str
    email: str
    name: str | None = None
    is_admin: bool = False
    disabled: bool = False
    password_hash: str | None = None
    created_at: datetime | None = None


class CurrentUser(BaseModel):
    id: str
    workspace_id: str
    email: str
    is_admin: bool = False


# --- корпус и прогоны -----------------------------------------------------------------------

class Corpus(BaseModel):
    id: str
    workspace_id: str
    name: str
    collection: str = "solar"
    kind: Literal["regular", "manual", "extract"] = "regular"
    profile_id: str | None = None
    project_id: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    deleted_at: datetime | None = None


class CorpusFile(BaseModel):
    id: str
    corpus_id: str
    name: str
    publication_id: str
    sha256: str | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    status: CorpusFileStatus = CorpusFileStatus.pending
    error_code: str | None = None
    error_message: str | None = None
    paper_id: str | None = None
    duplicate_of: str | None = None
    dup_kind: DupKind | None = None
    storage_key: str | None = None
    source: str | None = None
    stage: str | None = None  # этап разбора: «Извлечение» | «Нормализация» | «Проверка»
    progress: int | None = None  # 0..100
    is_scan: bool | None = None  # у PDF нет текстового слоя (определяется при приёме для rag)
    created_at: datetime | None = None
    started_at: datetime | None = None  # начало разбора файла
    stage_started_at: datetime | None = None  # начало текущего этапа


class RunCounts(BaseModel):
    total: int = 0
    queued: int = 0
    processing: int = 0
    done: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0


class RunOutput(BaseModel):
    available: bool = False
    url: str | None = None
    csv_files: list[str] = Field(default_factory=list)
    check_status: Literal["ok", "errors", "crashed", "not_run"] | None = None
    built_at: datetime | None = None


class Run(BaseModel):
    id: str
    corpus_id: str
    kind: RunKind = RunKind.full
    status: RunStatus = RunStatus.queued
    profile_id: str | None = None
    model_version: str = "latest"
    concurrency: int = 4
    keep_review: bool = True
    build_output: bool = False
    file_ids: list[str] | None = None
    counts: RunCounts = Field(default_factory=RunCounts)
    paused: bool = False
    pause_reason: str | None = None
    output: RunOutput | None = None
    started_by: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


# --- статьи, факты, замечания ---------------------------------------------------------------

class Quality(BaseModel):
    score: float | None = None
    level: Literal["good", "doubt", "bad"] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class Coverage(BaseModel):
    filled: int = 0
    total: int = 0
    ratio: float | None = None
    missing: list[str] = Field(default_factory=list)


class PaperStats(BaseModel):
    quality: Quality = Field(default_factory=Quality)
    coverage: Coverage = Field(default_factory=Coverage)
    open_issues: int = 0
    top_level: str | None = None
    facts_count: int = 0


class Paper(BaseModel):
    id: str
    workspace_id: str
    corpus_id: str | None = None
    file_id: str | None = None
    publication_id: str
    collection: str = "solar"
    title: str | None = None
    year: int | None = None
    doi: str | None = None
    in_scope: bool | None = None
    status: PaperStatus = PaperStatus.queued
    sha256: str | None = None
    page_count: int | None = None
    needs_ocr: bool = False
    model_version: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    dup_of: str | None = None
    dup_kind: DupKind | None = None
    stats: PaperStats = Field(default_factory=PaperStats)
    error_code: str | None = None
    created_at: datetime | None = None
    processed_at: datetime | None = None
    deleted_at: datetime | None = None


class Fact(BaseModel):
    id: str
    paper_id: str
    publication_id: str
    target: dict[str, Any]  # {"entity": "results", "id": "R004", "field": "metric_value"}
    param: str
    metric_name: str | None = None
    row_id: str | None = None
    row_label: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None
    norm_status: NormStatus | None = None
    extraction_status: str | None = None
    review_status: ReviewStatus = ReviewStatus.new
    corrected_value: Decimal | None = None
    corrected_raw: str | None = None
    added_by: AddedBy = AddedBy.auto
    confidence: float | None = None
    source_page: int | None = None
    source_fragment: str | None = None
    source_table: str | None = None
    source_figure: str | None = None
    source_kind: SourceKind | None = None
    quote_found: bool | None = None
    anchor_id: str | None = None
    duplicate_of: str | None = None
    model_version: str | None = None
    exportable: bool = True
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @property
    def value(self) -> Decimal | None:
        return self.corrected_value if self.corrected_value is not None else self.normalized_value


class Anchor(BaseModel):
    id: str
    paper_id: str
    kind: Literal["value", "quote", "table", "figure"] = "value"
    page: int
    rects: list[Rect] = Field(default_factory=list)
    text: str | None = None
    fact_ids: list[str] = Field(default_factory=list)


class Issue(BaseModel):
    id: str
    paper_id: str
    key: str
    record: IssueRecord
    fact_ids: list[str] = Field(default_factory=list)
    anchor_ids: list[str] = Field(default_factory=list)
    status: IssueStatus = IssueStatus.open
    stale: bool = False
    rule_version: str | None = None
    created_at: datetime | None = None


class Action(BaseModel):
    id: str
    user_id: str
    workspace_id: str | None = None
    paper_id: str | None = None
    fact_id: str | None = None
    group_id: str | None = None
    action: str
    label: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    undone: bool = False
    created_at: datetime | None = None


class Export(BaseModel):
    id: str
    workspace_id: str
    user_id: str | None = None
    format: str
    scope: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    status: Literal["queued", "running", "done", "failed"] = "queued"
    file_key: str | None = None
    error: str | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


# --- черновики конвейера --------------------------------------------------------------------

class FactDraft(BaseModel):
    """Факт без id БД: id назначает storage."""
    ref: str  # локальная ссылка внутри статьи (напр. result_id)
    target: dict[str, Any]
    param: str
    metric_name: str | None = None
    row_id: str | None = None
    row_label: str | None = None
    raw_value: str | None = None
    raw_unit: str | None = None
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None
    norm_status: NormStatus | None = None
    extraction_status: str | None = None
    confidence: float | None = None
    source_page: int | None = None
    source_fragment: str | None = None
    source_table: str | None = None
    source_figure: str | None = None
    source_kind: SourceKind | None = None
    quote_found: bool | None = None
    anchor_ref: str | None = None
    duplicate_of_ref: str | None = None
    added_by: AddedBy = AddedBy.auto
    model_version: str | None = None
    trace: list[TraceStep] = Field(default_factory=list)


class AnchorDraft(BaseModel):
    ref: str
    kind: Literal["value", "quote", "table", "figure"] = "value"
    page: int
    rects: list[Rect] = Field(default_factory=list)
    text: str | None = None
    fact_refs: list[str] = Field(default_factory=list)


# --- запросы и страницы ---------------------------------------------------------------------

class FactFilter(BaseModel):
    workspace_id: str | None = None
    corpus_id: str | None = None
    paper_ids: list[str] | None = None
    collection: str | None = None
    params: list[str] | None = None
    metric_names: list[str] | None = None
    review_status: list[ReviewStatus] | None = None
    extraction_status: list[str] | None = None
    has_open_issues: bool | None = None
    value_min: Decimal | None = None
    value_max: Decimal | None = None
    min_confidence: float | None = None
    max_confidence: float | None = None
    text: str | None = None
    include_duplicates: bool = True


class SortKey(BaseModel):
    field: str
    dir: Literal["asc", "desc"] = "asc"


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    data: list[T]
    total: int | None = None
    next_cursor: str | None = None


class FactRow(BaseModel):
    """Строка GET /facts/table."""
    fact: Fact
    paper_title: str | None = None
    paper_year: int | None = None
    open_issues: int = 0
    display: str | None = None


class IssuesDiff(BaseModel):
    opened: list[str] = Field(default_factory=list)
    closed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    stale: list[str] = Field(default_factory=list)


class TransferResult(BaseModel):
    transferred: list[str] = Field(default_factory=list)
    to_manual: list[str] = Field(default_factory=list)


class SaveResult(BaseModel):
    paper_id: str
    fact_ids: dict[str, str] = Field(default_factory=dict)  # FactDraft.ref -> fact id
    anchor_ids: dict[str, str] = Field(default_factory=dict)
    issues: IssuesDiff = Field(default_factory=IssuesDiff)


class ProcessOutcome(BaseModel):
    paper_id: str
    status: PaperStatus
    stats: PaperStats = Field(default_factory=PaperStats)
    from_cache: bool = False
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = Field(default_factory=list)


# --- выход конвейера ------------------------------------------------------------------------

class FileReport(BaseModel):
    """Строка _meta/files.csv."""
    file: str
    publication_id: str
    status: str
    error_code: str | None = None
    sha256: str | None = None
    pages: int | None = None
    from_cache: bool = False
    dup_of: str | None = None
    dup_kind: str | None = None
    facts: int = 0
    issues: int = 0
    message: str | None = None


class ValidationProblem(BaseModel):
    level: Literal["error", "warning"]
    code: str
    table: str | None = None
    row: int | None = None
    column: str | None = None
    message: str


class CheckReport(BaseModel):
    ran: bool = False
    ok: bool | None = None
    crashed: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    quotes_checked: int | None = None
    quotes_bad: int | None = None
    raw_output: str = ""
    exit_code: int | None = None


class OutputResult(BaseModel):
    csv_paths: list[Path] = Field(default_factory=list)
    meta_dir: Path | None = None
    check: CheckReport | None = None
    problems: list[ValidationProblem] = Field(default_factory=list)
    exit_code_hint: int = 0
    events: list[str] = Field(default_factory=list)
