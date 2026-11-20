"""LocalNormalizer.layer_function (spec/06 §6.3 «Слои»). REQ-046."""
from __future__ import annotations

import pytest

from faktura.domain import VOCAB

LAYER_VOCAB = sorted(VOCAB[("layers", "layer_function")])


@pytest.mark.parametrize("text,expected", [
    ("FTO", "front_contact"), ("ITO", "front_contact"), ("AZO", "front_contact"),
    ("TiO2", "ETL"), ("SnO2", "ETL"), ("ZnO", "ETL"), ("PCBM", "ETL"), ("C60", "ETL"), ("CdS", "ETL"),
    ("Spiro-OMeTAD", "HTL"), ("PTAA", "HTL"), ("NiOx", "HTL"), ("PEDOT:PSS", "HTL"),
    ("Au", "back_contact"), ("Ag", "back_contact"), ("Al", "back_contact"), ("Cu", "back_contact"),
    ("carbon", "back_contact"), ("углерод", "back_contact"),
    ("BCP", "interlayer"), ("LiF", "interlayer"), ("MoO3", "interlayer"),
    ("PEAI", "passivation"),
    ("recombination layer", "recombination"), ("рекомбинационный слой", "recombination"),
])
def test_known_materials(nz, text, expected):
    assert nz.layer_function(text).value == expected


def test_cds_is_etl(nz):
    assert nz.layer_function("CdS").value == "ETL"


@pytest.mark.parametrize("text", ["сетка", "metal grid", "Ag grid"])
def test_metal_grid_front_contact(nz, text):
    assert nz.layer_function(text).value == "front_contact"


@pytest.mark.parametrize("text", ["AR coating", "anti-reflection layer", "просветляющее покрытие", "MgF2 AR layer"])
def test_ar_layer_interlayer_with_note(nz, text):
    lf = nz.layer_function(text)
    assert lf.value == "interlayer"
    assert lf.note  # исходное название сохраняется


def test_note_keeps_original_name(nz):
    assert "просветляющее покрытие" in (nz.layer_function("просветляющее покрытие").note or "")


def test_mo_front_or_back_by_position(nz):
    """Mo в CIGS (substrate) - тыловой контакт, ITO первым слоем - фронтальный."""
    assert nz.layer_function("ITO", position=1).value == "front_contact"
    assert nz.layer_function("Mo", position=1).value == "back_contact"


def test_position_decides_metal_contact(nz):
    assert nz.layer_function("Au", position=7).value == "back_contact"


@pytest.mark.parametrize("value", LAYER_VOCAB)
def test_vocab_values_unchanged(nz, value):
    assert nz.layer_function(value).value == value


def test_perovskite_absorber(nz):
    assert nz.layer_function("perovskite absorber").value == "absorber"


@pytest.mark.parametrize("text", ["glass", "стекло", "polyimide", "foil", "полиимид"])
def test_substrate_not_a_layer(nz, text):
    """Подложка в layers не пишется."""
    assert nz.layer_function(text).value is None


@pytest.mark.parametrize("text", ["", "   ", "mystery layer xyz"])
def test_unknown_none_without_crash(nz, text):
    assert nz.layer_function(text).value is None


@pytest.mark.parametrize("text", ["FTO", "CdS", "BCP", "random", "Au", "сетка", "AR coating"])
def test_result_value_always_in_vocab(nz, text):
    v = nz.layer_function(text).value
    assert v is None or v in VOCAB[("layers", "layer_function")]


@pytest.mark.data
def test_dev_gold_layer_functions_unchanged(nz, gold_layers):
    """Все строки dev_gold/layers.csv: значение словаря не меняется."""
    bad = []
    for r in gold_layers:
        fn = r["layer_function"]
        if fn and nz.layer_function(fn).value != fn:
            bad.append((r["layer_id"], fn))
    assert not bad, bad[:10]


@pytest.mark.data
def test_dev_gold_layers_by_material_do_not_contradict(nz, gold_layers):
    """По названию материала функция либо совпадает с эталоном, либо None (не более 10% расхождений)."""
    wrong = []
    for r in gold_layers:
        pos = int(r["layer_order"]) if r["layer_order"].isdigit() else None
        got = nz.layer_function(r["layer_material"], position=pos).value
        if got is not None and got != r["layer_function"]:
            wrong.append((r["layer_id"], r["layer_material"], r["layer_function"], got))
    assert len(wrong) <= len(gold_layers) * 0.1, wrong[:15]
