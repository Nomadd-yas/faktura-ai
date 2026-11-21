"""Вход и выход адаптера извлечения ML-1 (spec/07 §7.0.1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from .profile import ProfileField
from .records import PaperBundle


class ParsedPage(BaseModel):
    n: int
    width_pt: float
    height_pt: float
    char_count: int = 0
    image_ratio: float = 0.0
    has_text_layer: bool = True
    needs_ocr: bool = False


class ParsedDocument(BaseModel):
    page_count: int
    pages: list[ParsedPage] = Field(default_factory=list)
    needs_ocr: bool = False
    text_by_page: list[str] = Field(default_factory=list)
    parser_version: str = ""


class ExtractRequest(BaseModel):
    publication_id: str
    pdf_path: Path
    sha256: str
    domain: str = "solar"
    fields: list[ProfileField] | None = None
    document: ParsedDocument | None = None
    model_version: str = "latest"
    request_id: str | None = None


class ExtractOutcome(BaseModel):
    bundle: PaperBundle
    model_version: str
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    from_cache: bool = False
    ocr_confidence: float | None = None  # 0..1: надёжность текста файла (ML meta.ocr_confidence)
    suggested_domain: str | None = None  # solar | rag: к какой области, вероятно, относится статья вне области


class MlHealth(BaseModel):
    status: Literal["ok", "degraded", "down"]
    model_version: str | None = None
    retry_after_s: int | None = None


class GenericOutcome(BaseModel):
    """Ответ модели для области без своих pydantic-моделей (rag): строки таблиц как словари."""
    tables: dict[str, list[dict[str, Any]]]
    model_version: str
    warnings: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    from_cache: bool = False
    ocr_confidence: float | None = None
    suggested_domain: str | None = None
