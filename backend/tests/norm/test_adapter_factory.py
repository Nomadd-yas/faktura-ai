"""get_normalizer и FakturaNormAdapter (план 5.1, К-13)."""
from __future__ import annotations

import sys
import types
from decimal import Decimal

import pytest

from faktura.config import Settings
from faktura.domain.ports import Normalizer, ParsedNumber
from faktura.norm import FakturaNormAdapter, LocalNormalizer, get_normalizer

METHODS = ("parse_number", "normalize", "normalize_field", "match_metric", "canon_material", "layer_function",
           "normalize_encapsulation", "normalize_spectrum", "format_display", "preview", "dictionary")


def test_local_implements_protocol():
    assert isinstance(LocalNormalizer(), Normalizer)


def test_get_normalizer_default_local():
    s = Settings()
    assert s.norm_impl == "local"
    assert isinstance(get_normalizer(s), LocalNormalizer)


def test_get_normalizer_local_works():
    assert get_normalizer(Settings()).match_metric("V_oc") == "Voc"


def test_faktura_norm_missing_raises_importerror(monkeypatch):
    monkeypatch.setitem(sys.modules, "faktura_norm", None)  # имитация отсутствия пакета
    with pytest.raises(ImportError):
        get_normalizer(Settings(norm_impl="faktura_norm"))


def test_adapter_has_full_interface():
    for name in METHODS:
        assert callable(getattr(FakturaNormAdapter, name))


def test_adapter_delegates_to_package(monkeypatch):
    fake = types.ModuleType("faktura_norm")
    fake.NORM_VERSION = "9.9"
    fake.DICTIONARY_VERSION = "d9"
    fake.match_metric = lambda text: "Voc"
    fake.parse_number = lambda text: ParsedNumber(ok=True, value=Decimal("1"))
    monkeypatch.setitem(sys.modules, "faktura_norm", fake)
    a = FakturaNormAdapter()
    assert a.match_metric("whatever") == "Voc"
    assert a.parse_number("1").value == Decimal("1")
    assert a.version == "9.9"
    assert a.dictionary_version == "d9"
