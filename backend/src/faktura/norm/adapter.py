"""FakturaNormAdapter: обёртка над пакетом ML-2 `faktura_norm` и фабрика get_normalizer (план 5.1, К-13)."""
from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any

from ..config import Settings
from ..domain.ports import (DictionarySnapshot, LayerFunction, Normalizer, NormPreview, NormResult, ParsedNumber)
from .local import LocalNormalizer


def _coerce(model: Any, result: Any) -> Any:
    if isinstance(result, model) or result is None:
        return result
    if isinstance(result, dict):
        return model.model_validate(result)
    if hasattr(result, "model_dump"):
        return model.model_validate(result.model_dump())
    return model.model_validate(getattr(result, "__dict__", result))


class FakturaNormAdapter:
    """Если пакета faktura_norm нет - ImportError при создании.

    Функции, которых в пакете нет, делегируются LocalNormalizer (пакет ML-2 может поставляться не целиком).
    Пакет предоставляет функции уровня модуля с теми же именами и сигнатурами, что у протокола Normalizer.
    """
    version: str = ""
    dictionary_version: str = ""

    def __init__(self) -> None:
        self._pkg = importlib.import_module("faktura_norm")  # ImportError, если пакета нет
        if self._pkg is None:
            raise ImportError("faktura_norm")
        self._local = LocalNormalizer()
        self.version = str(getattr(self._pkg, "NORM_VERSION", self._local.version))
        self.dictionary_version = str(getattr(self._pkg, "DICTIONARY_VERSION", self._local.dictionary_version))

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        fn = getattr(self._pkg, name, None)
        if callable(fn):
            return fn(*args, **kwargs)
        return getattr(self._local, name)(*args, **kwargs)

    def parse_number(self, text: str) -> ParsedNumber:
        return _coerce(ParsedNumber, self._call("parse_number", text))

    def normalize(self, metric_name: str, raw_value: str | None, raw_unit: str | None,
                  header_unit: str | None = None) -> NormResult:
        return _coerce(NormResult, self._call("normalize", metric_name, raw_value, raw_unit, header_unit))

    def normalize_field(self, table: str, column: str, raw_value: str | None,
                        raw_unit: str | None) -> NormResult:
        return _coerce(NormResult, self._call("normalize_field", table, column, raw_value, raw_unit))

    def match_metric(self, text: str) -> str | None:
        return self._call("match_metric", text)

    def canon_material(self, text: str) -> str | None:
        return self._call("canon_material", text)

    def layer_function(self, text: str, position: int | None = None) -> LayerFunction:
        return _coerce(LayerFunction, self._call("layer_function", text, position))

    def normalize_encapsulation(self, value: object) -> tuple[bool | None, str | None]:
        return self._call("normalize_encapsulation", value)

    def normalize_spectrum(self, text: str | None) -> str | None:
        return self._call("normalize_spectrum", text)

    def format_display(self, value: Decimal | None, unit: str | None, *, digits: int | None = None) -> str:
        return self._call("format_display", value, unit, digits=digits)

    def preview(self, collection: str, param: str, raw: str) -> NormPreview:
        return _coerce(NormPreview, self._call("preview", collection, param, raw))

    def dictionary(self) -> DictionarySnapshot:
        return _coerce(DictionarySnapshot, self._call("dictionary"))


def get_normalizer(settings: Settings) -> Normalizer:
    """settings.norm_impl: local -> LocalNormalizer; faktura_norm -> FakturaNormAdapter."""
    if settings.norm_impl == "faktura_norm":
        return FakturaNormAdapter()  # type: ignore[return-value]
    return LocalNormalizer()
