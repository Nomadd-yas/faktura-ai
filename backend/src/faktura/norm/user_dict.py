"""Свои синонимы рабочей области в нормализации: обёртка над Normalizer и суффикс версии словаря (CORE_API §4h).

Свои синонимы сопоставляются так же, как встроенные: без учёта регистра, пробелов и знаков (`_collapse`), а для
метрик ещё и целым словом внутри подписи. Меняют только метрики результатов, формулу материала и спектр;
остальное делегируется базовому нормализатору. Без своих синонимов `with_user_synonyms` возвращает его самого.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from .catalog import CONDITION_PREFIX, MATERIAL_PREFIX, condition_entries, material_entries, metric_of_param


def _collapse(text: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "").casefold())


def _words(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text or "").casefold().replace("_", "")).strip()


def version_suffix(synonyms: list[Any]) -> str:
    """'+u<8 hex>' от набора своих синонимов (не зависит от порядка и id); '' - своих синонимов нет."""
    if not synonyms:
        return ""
    lines = sorted(f"{s.collection}\x1f{s.param}\x1f{s.text.strip().lower()}" for s in synonyms)
    return "+u" + hashlib.sha1("\n".join(lines).encode("utf-8")).hexdigest()[:8]


class UserDictNormalizer:
    def __init__(self, base: Any, synonyms: list[Any]) -> None:
        self._base = base
        self.dictionary_version = f"{getattr(base, 'dictionary_version', '')}{version_suffix(synonyms)}"
        mats = {MATERIAL_PREFIX + e["id"]: e["target"] for e in material_entries()}
        conds = {CONDITION_PREFIX + e["id"]: e["target"] for e in condition_entries()}
        self._metrics: dict[str, str] = {}
        self._materials: dict[str, str] = {}
        self._spectra: dict[str, str] = {}
        phrases: list[tuple[str, str]] = []
        for s in synonyms:
            k = _collapse(s.text)
            if not k:
                continue
            if s.param in mats:
                self._materials.setdefault(k, mats[s.param])
            elif s.param in conds:
                self._spectra.setdefault(k, conds[s.param])
            else:
                metric = metric_of_param(s.collection, s.param)
                if metric:
                    self._metrics.setdefault(k, metric)
                    phrases.append((_words(s.text), metric))
        phrases.sort(key=lambda p: -len(p[0]))
        self._phrases = [(re.compile(r"(?<![\w])" + re.escape(p) + r"(?![\w])"), m) for p, m in phrases if p]

    def __getattr__(self, name: str) -> Any:  # всё остальное - базовый нормализатор
        return getattr(self._base, name)

    def match_metric(self, text: str) -> str | None:
        if text and text.strip():
            hit = self._metrics.get(_collapse(text))
            if hit:
                return hit
        found = self._base.match_metric(text)
        if found or not text or not text.strip():
            return found
        w = _words(text)
        return next((m for rx, m in self._phrases if rx.search(w)), None)

    def normalize(self, metric_name: str, raw_value: str | None, raw_unit: str | None,
                  header_unit: str | None = None):
        name = self.match_metric(metric_name or "") or metric_name
        return self._base.normalize(name, raw_value, raw_unit, header_unit)

    def canon_material(self, text: str) -> str | None:
        hit = self._materials.get(_collapse(text)) if text and text.strip() else None
        return hit or self._base.canon_material(text)

    def normalize_spectrum(self, text: str | None) -> str | None:
        hit = self._spectra.get(_collapse(text)) if text and str(text).strip() else None
        return hit or self._base.normalize_spectrum(text)


def with_user_synonyms(base: Any, synonyms: list[Any]) -> Any:
    return UserDictNormalizer(base, synonyms) if synonyms else base


def rag_metric_map(synonyms: list[Any]) -> dict[str, str]:
    """Для rag: написание (в виде _collapse) -> metric_name параметра коллекции rag."""
    out: dict[str, str] = {}
    for s in synonyms:
        if s.collection != "rag":
            continue
        metric = metric_of_param("rag", s.param)
        k = _collapse(s.text)
        if metric and k:
            out.setdefault(k, metric)
    return out
