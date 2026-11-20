"""PDF: приём, проверка, разбор, якоря, проверка цитат (план 5.2, модуль `adapters`)."""
from __future__ import annotations

from .inputs import (CollectResult, InputFile, NameDedupeResult, PdfInfo, Rejected, ZipLimits, collect_inputs,
                     dedupe_names, inspect_pdf, safe_unzip)
from .parse import LocalParseAdapter, bbox_to_rects, image_mime, webp_available
from .quotes import PypdfQuoteIndex, is_checkable_fragment, title_key, validator_norm

__all__ = [
    "CollectResult", "InputFile", "NameDedupeResult", "PdfInfo", "Rejected", "ZipLimits", "collect_inputs",
    "dedupe_names", "inspect_pdf", "safe_unzip", "LocalParseAdapter", "bbox_to_rects", "PypdfQuoteIndex",
    "title_key", "validator_norm", "image_mime", "webp_available", "is_checkable_fragment",
]
