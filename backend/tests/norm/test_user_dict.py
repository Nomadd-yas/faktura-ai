"""Свои синонимы в нормализации: обёртка UserDictNormalizer, версия словаря, rag-карта метрик."""
from __future__ import annotations

from types import SimpleNamespace as NS

from faktura.norm import LocalNormalizer
from faktura.norm.user_dict import rag_metric_map, version_suffix, with_user_synonyms


def syn(collection, param, text):
    return NS(collection=collection, param=param, text=text)


def test_no_synonyms_returns_base():
    base = LocalNormalizer()
    assert with_user_synonyms(base, []) is base and version_suffix([]) == ""


def test_metric_material_spectrum_and_version():
    base = LocalNormalizer()
    nz = with_user_synonyms(base, [syn("solar", "voc", "Напряжение на разомкнутой цепи"),
                                   syn("solar", "material:ch3nh3pbi3", "МАПИ"),
                                   syn("solar", "condition:am15g", "стандартное солнце")])
    assert base.match_metric("Напряжение на разомкнутой цепи") is None
    assert nz.match_metric("напряжение на  разомкнутой цепи") == "Voc"
    assert nz.match_metric("Open circuit voltage") == "Voc"  # встроенное работает
    assert nz.match_metric("измерено: Напряжение на разомкнутой цепи, В") == "Voc"  # целым словом в подписи
    r = nz.normalize("Напряжение на разомкнутой цепи", "950", "mV")
    assert str(r.normalized_value) == "0.95" and r.normalized_unit == "V"
    assert nz.canon_material("МАПИ") == "CH3NH3PbI3" and base.canon_material("МАПИ") != "CH3NH3PbI3"
    assert nz.normalize_spectrum("Стандартное солнце") == "AM1.5G"
    assert nz.version == base.version and nz.dictionary_version.startswith(base.dictionary_version + "+u")


def test_version_suffix_is_order_independent_and_changes():
    a, b = syn("solar", "pce", "x1"), syn("solar", "voc", "x2")
    assert version_suffix([a, b]) == version_suffix([b, a])
    assert version_suffix([a]) != version_suffix([a, b])


def test_non_metric_param_has_no_effect_and_rag_map():
    nz = with_user_synonyms(LocalNormalizer(), [syn("solar", "bandgap", "зона")])
    assert nz.match_metric("зона") is None
    m = rag_metric_map([syn("rag", "r5", "Полнота на 5"), syn("rag", "retriever", "метод"), syn("solar", "pce", "z")])
    assert m == {"полнотана5": "recall@5"}
