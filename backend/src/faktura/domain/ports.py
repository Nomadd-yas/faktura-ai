"""Порты (протоколы) интеграционных точек: ML-1, ML-2, хранилище, события (план 4.3).

Конвейер и API работают только через эти протоколы из Services и не импортируют реализации.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .extraction import ExtractOutcome, ExtractRequest, MlHealth, ParsedDocument, ParsedPage
from .records import Rect, TraceStep

__all__ = [
    "ParsedPage", "ParsedDocument", "ParseAdapter", "ExtractAdapter", "ParsedNumber", "NormResult",
    "LayerFunction", "NormPreview", "DictionarySnapshot", "Normalizer", "QuoteChecker", "FileStore",
    "Subscription", "EventBus", "Executor", "EvalReport", "IssueEvalReport", "EvalAdapter", "CancelToken",
]


@runtime_checkable
class ParseAdapter(Protocol):
    def parse(self, pdf_path: Path) -> ParsedDocument: ...
    def render_page(self, pdf_path: Path, n: int, scale: float = 1.5, fmt: str = "webp") -> bytes: ...
    def find_rects(self, pdf_path: Path, page: int, needle: str) -> list[Rect]: ...
    def page_size(self, pdf_path: Path, page: int) -> tuple[float, float]: ...


@runtime_checkable
class ExtractAdapter(Protocol):
    name: str  # "mock" | "http" | "raw"

    def extract(self, req: ExtractRequest) -> ExtractOutcome: ...  # бросает ExtractError
    def health(self) -> MlHealth: ...


class ParsedNumber(BaseModel):
    ok: bool
    value: Decimal | None = None
    spread: Decimal | None = None
    is_range: bool = False
    is_percent: bool = False
    rest: str | None = None  # неразобранный хвост (единица)
    note: str | None = None


class NormResult(BaseModel):
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None
    norm_status: Literal["ok", "converted", "failed", "not_applicable", "passthrough"] = "not_applicable"
    spread_value: Decimal | None = None
    trace: list[TraceStep] = Field(default_factory=list)
    issue: Literal["unit_inconsistency", "ambiguity"] | None = None


class LayerFunction(BaseModel):
    value: str | None = None  # из VOCAB[layers.layer_function] | None
    note: str | None = None   # «исходно: …»


class NormPreview(BaseModel):
    ok: bool
    value: Decimal | None = None
    unit: str | None = None
    text: str | None = None  # display по-русски


class DictionarySnapshot(BaseModel):
    version: str
    metrics: list[dict[str, Any]] = Field(default_factory=list)
    units: list[dict[str, Any]] = Field(default_factory=list)
    materials: list[dict[str, Any]] = Field(default_factory=list)
    layer_functions: list[dict[str, Any]] = Field(default_factory=list)
    spectra: list[dict[str, Any]] = Field(default_factory=list)


@runtime_checkable
class Normalizer(Protocol):
    """Полный интерфейс faktura_norm (ML-2); LocalNormalizer и FakturaNormAdapter реализуют его."""
    version: str
    dictionary_version: str

    def parse_number(self, text: str) -> ParsedNumber: ...
    def normalize(self, metric_name: str, raw_value: str | None, raw_unit: str | None,
                  header_unit: str | None = None) -> NormResult: ...
    def normalize_field(self, table: str, column: str, raw_value: str | None, raw_unit: str | None) -> NormResult: ...
    def match_metric(self, text: str) -> str | None: ...
    def canon_material(self, text: str) -> str | None: ...
    def layer_function(self, text: str, position: int | None = None) -> LayerFunction: ...
    def normalize_encapsulation(self, value: object) -> tuple[bool | None, str | None]: ...
    def normalize_spectrum(self, text: str | None) -> str | None: ...
    def format_display(self, value: Decimal | None, unit: str | None, *, digits: int | None = None) -> str: ...
    def preview(self, collection: str, param: str, raw: str) -> NormPreview: ...
    def dictionary(self) -> DictionarySnapshot: ...


@runtime_checkable
class QuoteChecker(Protocol):
    def check(self, page: int | None, fragment: str | None) -> bool | None: ...  # None - не проверяется


@runtime_checkable
class FileStore(Protocol):
    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def put_file(self, key: str, path: Path, content_type: str | None = None) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def open(self, key: str) -> BinaryIO: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def copy_to(self, key: str, path: Path) -> None: ...
    def signed_url(self, key: str, ttl_s: int) -> str | None: ...


class Subscription(Protocol):
    def __iter__(self) -> Iterator[tuple[str, str, dict]]: ...  # (event_id, event, data)
    def close(self) -> None: ...


@runtime_checkable
class EventBus(Protocol):
    def publish(self, workspace_id: str, event: str, data: dict) -> str: ...
    def subscribe(self, workspace_id: str, last_event_id: str | None) -> Subscription: ...


@runtime_checkable
class Executor(Protocol):
    def submit(self, fn: Callable[[], None]) -> None: ...


class CancelToken(Protocol):
    def is_set(self) -> bool: ...


class EvalReport(BaseModel):
    """Отчёт оценки по 11 метрикам (spec/14 §14.7). Детали наполняет модуль evaluation."""
    per_metric: dict[str, dict[str, Any]] = Field(default_factory=dict)
    totals: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class IssueEvalReport(BaseModel):
    recall: float | None = None
    precision: float | None = None
    tp: int = 0
    fp: int = 0
    fn: int = 0
    by_check: dict[str, dict[str, Any]] = Field(default_factory=dict)


@runtime_checkable
class EvalAdapter(Protocol):
    def evaluate(self, gold_dir: Path, pred_dir: Path) -> EvalReport: ...
