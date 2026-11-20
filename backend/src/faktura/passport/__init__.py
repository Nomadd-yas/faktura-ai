"""Паспорт набора данных (spec/04 §4.17; все 11 метрик, К-9)."""
from __future__ import annotations

from .build import METRIC_LABELS, PASSPORT_VERSION, SCHEMA_VERSION, build_passport, passport_markdown

__all__ = ["build_passport", "passport_markdown", "METRIC_LABELS", "PASSPORT_VERSION", "SCHEMA_VERSION"]
