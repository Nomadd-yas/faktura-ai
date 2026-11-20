"""run_backend_rules: jv_consistency, rounding (spec/05 §5.2, К-21). Синтетика: Voc*Jsc*FF/Pin."""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from faktura.checks import run_backend_rules


def _jv(mk, bundle, **p):
    return mk.of(run_backend_rules(bundle, mk.rules_only("jv_consistency", **p)), "jv_consistency")


def test_consistent_triple_no_issue(mk):
    b = mk.bundle(mk.jv_triple(15))
    assert _jv(mk, b) == []


def test_inconsistent_triple_fires_with_four_ids_anchor_pce_first(mk):
    b = mk.bundle(mk.jv_triple("12.5"))
    out = _jv(mk, b)
    assert len(out) == 1
    i = out[0]
    assert set(i.record_ids) == {"R001", "R002", "R003", "R004"}
    assert i.record_ids[0] == "R004"  # якорь - PCE
    assert i.publication_id == mk.PID
    assert i.computed_by == "backend"
    assert i.severity == "warn"


def test_description_russian_with_numbers_of_both_sides(mk):
    i = _jv(mk, mk.bundle(mk.jv_triple("12.5")))[0]
    assert re.search(r"[а-яА-Я]{4,}", i.description)
    assert re.search(r"12\.5", i.description)  # PCE статьи
    assert re.search(r"(?<![\d.])15(\.0+)?(?![\d])", i.description)  # расчёт Voc*Jsc*FF/Pin
    assert "расхожд" in i.description.lower()


# граница порога 3 %: |calc - PCE| / PCE > 3 % строго. Jsc=10, FF=100, Voc=1.03 -> calc 10.3 % при PCE 10.
@pytest.mark.parametrize("voc,fires", [("1.03", False), ("1.031", True), ("1.029", False), ("0.969", True),
                                       ("0.97", False)])
def test_threshold_boundary_3_percent(mk, voc, fires):
    b = mk.bundle(mk.jv_triple(10, voc=voc, jsc=10, ff=100))
    assert bool(_jv(mk, b)) is fires


def test_custom_threshold_from_rule_params(mk):
    b = mk.bundle(mk.jv_triple("15.5"))  # расхождение ~3.2 %
    assert _jv(mk, b)  # порог 3 % по умолчанию
    assert _jv(mk, b, threshold_pct=5) == []


def test_pin_from_w_m2_is_multiplied_by_0_1(mk):
    # 1000 W/m2 = 100 mW/cm2 -> PCE 15 согласуется; без перевода Pin=1000 дал бы 1.5 %
    ms = mk.measurement(intensity=1000, unit="W/m2")
    assert _jv(mk, mk.bundle(mk.jv_triple(15), measurements=[ms])) == []
    assert _jv(mk, mk.bundle(mk.jv_triple(1.5), measurements=[ms]))  # 1.5 % - уже расхождение


def test_pin_in_mw_cm2_non_standard_intensity(mk):
    ms = mk.measurement(intensity=50, unit="mW/cm2")  # calc = 30 %
    assert _jv(mk, mk.bundle(mk.jv_triple(30), measurements=[ms])) == []
    assert _jv(mk, mk.bundle(mk.jv_triple(15), measurements=[ms]))


def test_am15g_without_intensity_uses_100_but_does_not_write_it(mk):
    ms = mk.measurement(spectrum="AM1.5G", intensity=None, unit=None)
    b = mk.bundle(mk.jv_triple(15), measurements=[ms])
    assert _jv(mk, b) == []
    assert _jv(mk, mk.bundle(mk.jv_triple(10), measurements=[ms]))  # 100 используется в расчёте
    run_backend_rules(b, mk.rules_only("jv_consistency"))
    assert b.measurements[0].light_intensity is None  # в данные не пишется


@pytest.mark.parametrize("pce", [15, 3, 100])
def test_zero_intensity_skips_rule_without_error(mk, pce):  # К-21
    ms = mk.measurement(intensity=0, unit="mW/cm2")
    assert _jv(mk, mk.bundle(mk.jv_triple(pce), measurements=[ms])) == []


def test_negative_intensity_skips_rule(mk):
    ms = mk.measurement(intensity=-100, unit="mW/cm2")
    assert _jv(mk, mk.bundle(mk.jv_triple(3), measurements=[ms])) == []


def test_missing_component_no_issue(mk):
    rs = [r for r in mk.jv_triple(3) if r.metric_name != "FF"]
    assert _jv(mk, mk.bundle(rs)) == []


def test_missing_measurement_row_no_crash(mk):
    rs = mk.jv_triple(3, meas=None)
    assert isinstance(_jv(mk, mk.bundle(rs, measurements=[])), list)


def test_only_same_aggregation_is_combined(mk):
    rs = [*mk.jv_triple(15, agg="mean")[:3], *mk.jv_triple(3, agg="champion", start=5)[3:]]
    assert _jv(mk, mk.bundle(rs)) == []  # PCE champion не сравнивается с Voc/Jsc/FF mean


def test_separate_devices_checked_separately(mk):
    rs = [*mk.jv_triple(15, device_id="DV1"), *mk.jv_triple(3, device_id="DV2", start=5)]
    b = mk.bundle(rs, devices=[mk.device("DV1"), mk.device("DV2")],
                  measurements=[mk.measurement(devices=("DV1", "DV2"))])
    out = _jv(mk, b)
    assert len(out) == 1 and set(out[0].record_ids) == {"R005", "R006", "R007", "R008"}


def test_uses_corrected_value_over_normalized(mk):
    rs = mk.jv_triple(10)  # расхождение
    rs[3].meta.corrected_value = Decimal("15")
    assert _jv(mk, mk.bundle(rs)) == []
    rs2 = mk.jv_triple(15)  # согласуется, но человек исправил на неверное
    rs2[3].meta.corrected_value = Decimal("10")
    assert _jv(mk, mk.bundle(rs2))


def test_rejected_result_skipped(mk):
    rs = mk.jv_triple(10)
    rs[3].meta.review_status = "rejected"
    assert _jv(mk, mk.bundle(rs)) == []


def test_duplicate_of_row_skipped(mk):
    rs = mk.jv_triple(15)
    dup = mk.result("R009", "PCE", 10, device="DV1", duplicate_of="R004", page=6)
    assert _jv(mk, mk.bundle([*rs, dup])) == []


def test_non_numeric_string_values_ignored(mk):
    rs = mk.jv_triple(15)
    rs[2].normalized_value = "7l"  # нераспознанный текст ячейки
    assert _jv(mk, mk.bundle(rs)) == []


def test_pce_zero_no_division_error(mk):
    assert isinstance(_jv(mk, mk.bundle(mk.jv_triple(0))), list)


def test_disabled_rule_does_not_run(mk):
    b = mk.bundle(mk.jv_triple(3))
    assert mk.of(run_backend_rules(b, mk.rules_only("voc_limit")), "jv_consistency") == []


def test_no_rules_returns_empty_list(mk):
    assert run_backend_rules(mk.bundle(mk.jv_triple(3)), {}) == []


def test_empty_bundle_returns_list(mk):
    from faktura.domain.records import PaperBundle
    assert run_backend_rules(PaperBundle.failed_stub("X1"), mk.rules_only("jv_consistency")) == []


def test_rule_is_deterministic_and_does_not_mutate_bundle(mk):
    b = mk.bundle(mk.jv_triple(3))
    before = b.model_dump()
    a = run_backend_rules(b, mk.rules_only("jv_consistency"))
    c = run_backend_rules(b, mk.rules_only("jv_consistency"))
    assert a, "правило должно сработать на PCE 3 при расчёте 15"
    assert [i.model_dump() for i in a] == [i.model_dump() for i in c]
    assert b.model_dump() == before


# rounding: расхождение объясняется округлением входов -> вместо jv_consistency ставится rounding
def test_rounding_instead_of_jv_when_voc_rounded_to_one_decimal(mk):
    rs = mk.jv_triple("9.0", voc="0.6", jsc=20, ff=70)  # calc 8.4; при Voc 0.643 было бы 9.0
    for r in rs:
        r.raw_value = {"Voc": "0.6"}.get(r.metric_name, r.raw_value)
    out = run_backend_rules(mk.bundle(rs), mk.rules_only("jv_consistency", "rounding"))
    assert mk.of(out, "jv_consistency") == []
    r = mk.of(out, "rounding")
    assert len(r) == 1 and r[0].severity == "info" and "R004" in r[0].record_ids


def test_precise_inputs_same_gap_stays_jv_consistency(mk):
    rs = mk.jv_triple("9.0", voc="0.600", jsc=20, ff=70)
    for r in rs:
        r.raw_value = {"Voc": "0.600"}.get(r.metric_name, r.raw_value)
    out = run_backend_rules(mk.bundle(rs), mk.rules_only("jv_consistency", "rounding"))
    assert mk.of(out, "jv_consistency") and mk.of(out, "rounding") == []
