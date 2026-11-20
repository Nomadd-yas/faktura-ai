"""apply_normalization: 8 шагов, только через протокол Normalizer (план 5.6)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.pipeline import apply_normalization


def norm(bundle, fake_norm, profile):
    return apply_normalization(bundle, fake_norm, profile)


def res(b, rid):
    return next(r for r in b.results if r.result_id == rid)


def lay(b, lid):
    return next(r for r in b.layers if r.layer_id == lid)


# --- шаг 1: match_metric ---------------------------------------------------------------------

def test_step1_metric_name_canonicalised_with_trace(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R1")
    assert r.metric_name == "Voc"
    assert ("V_oc", "Voc") in [(t.before, t.after) for t in r.meta.norm_trace]


def test_step1_canonical_name_untouched_no_trace(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert res(out, "R2").metric_name == "PCE"
    assert all(t.before != "PCE" for t in res(out, "R2").meta.norm_trace)


def test_step1_unknown_metric_name_kept(synth_bundle, fake_norm, profile):
    b = synth_bundle()
    res(b, "R2").metric_name = "Quantum Yield 9000"
    out = norm(b, fake_norm, profile)
    assert res(out, "R2").metric_name == "Quantum Yield 9000"


# --- шаг 2: normalize results ------------------------------------------------------------------

def test_step2_metric_fields_follow_normalized(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R1")
    assert r.normalized_value == Decimal("1.197") and r.normalized_unit == "V"
    assert r.metric_value == r.normalized_value and r.metric_unit == r.normalized_unit
    assert r.raw_value == "1197" and r.raw_unit == "mV"  # сырое не трогаем


def test_step2_rows_without_raw_value_skipped(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R4")
    assert r.metric_value is None and r.normalized_value is None
    assert not any(a[0] == "Jsc" for a in fake_norm.called("normalize"))


def test_step2_failed_norm_leaves_empty_and_marks_issue(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R5")
    assert r.normalized_value is None and r.metric_value is None
    assert r.meta.norm_issue == "unit_inconsistency"


def test_step2_normalizer_called_with_canonical_name(synth_bundle, fake_norm, profile):
    norm(synth_bundle(), fake_norm, profile)
    assert any(a[0] == "Voc" for a in fake_norm.called("normalize"))
    assert not any(a[0] == "V_oc" for a in fake_norm.called("normalize"))


# --- шаг 3: spread_value --------------------------------------------------------------------------

def test_step3_ml_spread_preserved(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert Decimal(str(res(out, "R2").spread_value)) == Decimal("0.4")


def test_step3_pm_fills_empty_spread(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    r = res(out, "R3")
    assert r.normalized_value == Decimal("18.4")
    assert Decimal(str(r.spread_value)) == Decimal("0.5")


def test_step3_conflict_keeps_ml_and_notes_discrepancy(synth_bundle, fake_norm, profile):
    b = synth_bundle()
    res(b, "R3").spread_value = "0.9"  # ML говорит 0.9, текст ±0.5
    out = norm(b, fake_norm, profile)
    r = res(out, "R3")
    assert Decimal(str(r.spread_value)) == Decimal("0.9")
    assert r.meta.notes_backend, "расхождение разброса должно попасть в notes_backend"


def test_step3_spread_units_not_recalculated(synth_bundle, fake_norm, profile):
    b = synth_bundle()
    res(b, "R1").spread_value = "5"  # Voc в мВ, разброс в единицах статьи
    out = norm(b, fake_norm, profile)
    assert Decimal(str(res(out, "R1").spread_value)) == Decimal("5")


# --- шаг 4: materials ------------------------------------------------------------------------------

def test_step4_formula_from_canon_material_when_empty(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert out.materials[0].formula_normalized == "CH3NH3PbI3"


def test_step4_ml_formula_not_overwritten(synth_bundle, fake_norm, profile):
    b = synth_bundle()
    b.materials[0].formula_normalized = "MAPbI3-custom"
    out = norm(b, fake_norm, profile)
    assert out.materials[0].formula_normalized == "MAPbI3-custom"


def test_step4_bandgap_goes_through_normalize_field(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert Decimal(str(out.materials[0].bandgap_value)) == Decimal("1.55")
    assert ("materials", "bandgap_value", "1.55", "eV") in fake_norm.called("normalize_field")


# --- шаг 5: layers ------------------------------------------------------------------------------------

def test_step5_buffer_becomes_ETL(synth_bundle, fake_norm, profile):
    assert lay(norm(synth_bundle(), fake_norm, profile), "L1").layer_function == "ETL"


def test_step5_Ag_grid_becomes_front_contact(synth_bundle, fake_norm, profile):
    assert lay(norm(synth_bundle(), fake_norm, profile), "L2").layer_function == "front_contact"


def test_step5_AR_becomes_interlayer_with_note(synth_bundle, fake_norm, profile):
    la = lay(norm(synth_bundle(), fake_norm, profile), "L3")
    assert la.layer_function == "interlayer"
    assert "исходно" in (la.note or "")


def test_step5_vocab_value_not_touched(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert lay(out, "L4").layer_function == "absorber"
    assert not any(a[0] == "absorber" for a in fake_norm.called("layer_function"))


def test_step5_unknown_function_not_invented(synth_bundle, fake_norm, profile):
    from faktura.domain.tables import VOCAB
    b = synth_bundle()
    lay(b, "L1").layer_function = "mystery layer"
    v = lay(norm(b, fake_norm, profile), "L1").layer_function
    assert v is None or v in VOCAB[("layers", "layer_function")]


# --- шаг 6: devices -------------------------------------------------------------------------------------

def test_step6_encapsulation_text_kept_as_is(synth_bundle, fake_norm, profile):
    # свободный текст, как в эталоне («unencapsulated»); в bool не приводится
    assert norm(synth_bundle(), fake_norm, profile).devices[0].encapsulation == "unencapsulated"


def test_step6_encapsulation_none_stays_none(synth_bundle, fake_norm, profile):
    assert norm(synth_bundle(), fake_norm, profile).devices[1].encapsulation is None


def test_step6_active_area_numeric(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert Decimal(str(out.devices[0].active_area)) == Decimal("0.16")


# --- шаг 7: measurements ------------------------------------------------------------------------------------

def test_step7_spectrum_normalised(synth_bundle, fake_norm, profile):
    assert norm(synth_bundle(), fake_norm, profile).measurements[0].spectrum == "AM1.5G"


def test_step7_light_intensity_number(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert Decimal(str(out.measurements[0].light_intensity)) == Decimal("100")


# --- шаг 8: единица только при значении -------------------------------------------------------------------------

def test_step8_thickness_unit_kept_without_value(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert lay(out, "L4").layer_thickness is None and lay(out, "L4").thickness_unit == "nm"  # как в эталоне
    assert lay(out, "L1").thickness_unit == "nm"  # со значением остаётся


def test_step8_area_and_bandgap_unit_kept_without_value(synth_bundle, fake_norm, profile):
    out = norm(synth_bundle(), fake_norm, profile)
    assert out.devices[1].active_area is None and out.devices[1].area_unit == "cm2"
    assert out.materials[1].bandgap_value is None and out.materials[1].bandgap_unit == "eV"


def test_step8_result_unit_cleared_when_value_failed(synth_bundle, fake_norm, profile):
    assert res(norm(synth_bundle(), fake_norm, profile), "R5").metric_unit is None


# --- общие свойства ------------------------------------------------------------------------------------------

def test_idempotent(synth_bundle, fake_norm, profile):
    once = norm(synth_bundle(), fake_norm, profile)
    twice = norm(once.model_copy(deep=True), fake_norm, profile)
    assert once.model_dump() == twice.model_dump()


def test_empty_bundle_ok(fake_norm, profile):
    from faktura.domain.records import PaperBundle, Publication
    out = norm(PaperBundle(publication=Publication(publication_id="E1")), fake_norm, profile)
    assert out.publication.publication_id == "E1" and out.results == []


def test_only_protocol_methods_used(synth_bundle, fake_norm, profile):
    allowed = {"parse_number", "normalize", "normalize_field", "match_metric", "canon_material", "layer_function",
               "normalize_encapsulation", "normalize_spectrum", "format_display", "preview", "dictionary"}

    class Strict:
        version = "s"
        dictionary_version = "s"

        def __getattr__(self, name):
            if name in allowed:
                return getattr(fake_norm, name)
            raise AttributeError(name)

    out = apply_normalization(synth_bundle(), Strict(), profile)
    assert out.results


# --- (data) шаги 4-8 на эталоне ничего не меняют ------------------------------------------------------------

@pytest.mark.data
def test_gold_bundles_steps_4_8_noop(gold_bundles, profile):
    """На бандлах dev_gold шаги 4-8 ничего не меняют (кроме К-7: unencapsulated -> False уже в чтении)."""
    from faktura.norm.local import LocalNormalizer
    n = LocalNormalizer()
    for b in gold_bundles:
        before = b.model_copy(deep=True)
        after = apply_normalization(b.model_copy(deep=True), n, profile)
        for table in ("materials", "layers", "devices", "measurements"):
            for x, y in zip(getattr(before, table), getattr(after, table)):
                dx, dy = x.model_dump(exclude={"meta"}), y.model_dump(exclude={"meta", "note"} if table == "layers"
                                                                    else {"meta"})
                if table == "layers":
                    dx.pop("note", None)
                if table == "devices" and dx["encapsulation"] is None:
                    dx.pop("encapsulation"), dy.pop("encapsulation")
                assert dx == dy, (b.publication_id, table)


@pytest.mark.data
def test_gold_spread_values_survive_normalization(gold_bundles, profile):
    from faktura.norm.local import LocalNormalizer
    n = LocalNormalizer()
    before = sum(1 for b in gold_bundles for r in b.results if r.spread_value not in (None, ""))
    after = sum(1 for b in gold_bundles
                for r in apply_normalization(b.model_copy(deep=True), n, profile).results
                if r.spread_value not in (None, ""))
    assert before == after == 49
