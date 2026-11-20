"""Нормализация (spec/06, план 5.1). Модуль `norm`: заглушки, тело реализует исполнитель модуля."""
from __future__ import annotations

from .adapter import FakturaNormAdapter, get_normalizer
from .local import DICTIONARY_VERSION, NORM_VERSION, LocalNormalizer

__all__ = ["NORM_VERSION", "DICTIONARY_VERSION", "LocalNormalizer", "FakturaNormAdapter", "get_normalizer"]
