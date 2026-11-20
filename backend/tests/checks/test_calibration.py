"""Калибровка правил на синтетике по мотивам dev_gold (D004, D021, D022, D010) и мелкие договорённости модуля."""
from __future__ import annotations

from faktura.checks import assign_keys, check_paper, merge_issues, run_backend_rules


def _jv(mk, b, *names):
    return run_backend_rules(b, mk.rules_only("jv_consistency", *names))


def _dev_rows(mk, dev, start, voc, jsc, ff, mean, champ):
    """Voc/Jsc/FF без агрегации, PCE mean и champion (как Table 1 в D004)."""
    n = start
    return [mk.result(f"R{n:03d}", "Voc", voc, agg=None, device=dev),
            mk.result(f"R{n + 1:03d}", "Jsc", jsc, agg=None, device=dev),
            mk.result(f"R{n + 2:03d}", "FF", ff, agg=None, device=dev),
            mk.result(f"R{n + 3:03d}", "PCE", mean, agg="mean", device=dev),
            mk.result(f"R{n + 4:03d}", "PCE", champ, agg="champion", device=dev)]


def test_triple_without_aggregation_uses_majority_aggregation_d004(mk):
    rs = (_dev_rows(mk, "DV1", 1, "0.818", "9.344", "57.49", "4.39", "5.23")
          + _dev_rows(mk, "DV2", 6, "1.009", "19.61", "47.79", "9.46", "10.21")
          + _dev_rows(mk, "DV3", 11, "0.943", "19.664", "56.07", "9.9", "10.32"))
    b = mk.bundle(rs, devices=[mk.device(d) for d in ("DV1", "DV2", "DV3")],
                  measurements=[mk.measurement(devices=("DV1", "DV2", "DV3"), spectrum=None, intensity=None)])
    out = mk.of(_jv(mk, b, "rounding"), "jv_consistency")
    assert [set(i.record_ids) for i in out] == [{"R011", "R012", "R013", "R014", "R015"}]
    assert out[0].record_ids[0] == "R014"  # якорь - PCE предпочтительной агрегации (mean)


def test_mean_triples_are_not_checked_by_default_d021(mk):
    rs = mk.jv_triple("0.9", voc="0.296", jsc="9.15", ff="37.2", agg="mean")  # расчёт 1.01 против 0.9
    b = mk.bundle(rs)
    assert mk.of(_jv(mk, b), "jv_consistency") == []
    assert mk.of(run_backend_rules(b, mk.rules_only("jv_consistency", check_mean=True)), "jv_consistency")


def test_small_values_rounding_does_not_swallow_real_gap_d022(mk):
    rs = mk.jv_triple("0.52", voc="0.46", jsc="3.5", ff="33.4", agg=None)  # расхождение 3.4 %
    out = _jv(mk, mk.bundle(rs), "rounding")
    assert mk.of(out, "jv_consistency") and not mk.of(out, "rounding")


def test_w_m2_without_unit_text_large_value_treated_as_w_m2(mk):
    ms = mk.measurement(intensity=1000, unit=None)
    ms.intensity_unit = None
    assert mk.of(_jv(mk, mk.bundle(mk.jv_triple(15), measurements=[ms])), "jv_consistency") == []


def test_table_and_figure_pairs_split_by_place(mk):
    """Значения таблицы и рисунка одного прибора: тройки сравниваются внутри своего места."""
    table = mk.jv_triple("3.85", voc="0.344", jsc="26.6", ff="42.2", source_table="Table 4")
    fig = [mk.result(f"R{10 + k}", m, v, source_figure="Figure 10", page=13)
           for k, (m, v) in enumerate((("Voc", "0.343"), ("Jsc", "31.4"), ("FF", "49.0"), ("PCE", "5.25")))]
    out = _jv(mk, mk.bundle([*table, *fig]))
    assert mk.of(out, "jv_consistency") == []


def test_duplicate_compared_only_with_its_main(mk):
    rs = [mk.result("R001", "PCE", "3.85", source_table="Table 4"),
          mk.result("R002", "PCE", "5.25", source_figure="Figure 10", page=13),
          mk.result("R003", "PCE", "5.25", duplicate_of="R002", page=1)]
    out = mk.of(run_backend_rules(mk.bundle(rs), mk.rules_only("contradiction")), "contradiction")
    assert len(out) == 1 and set(out[0].record_ids) == {"R001", "R002"}


def test_figure_reading_tolerance_no_contradiction(mk):
    rs = [mk.result("R001", "Voc", "0.344", source_table="Table 4"),
          mk.result("R002", "Voc", "0.343", source_figure="Figure 10")]
    assert mk.of(run_backend_rules(mk.bundle(rs), mk.rules_only("contradiction")), "contradiction") == []


def test_one_sun_counts_as_intensity(mk):
    ms = mk.measurement(spectrum="1 sun", intensity=None, unit=None)
    out = mk.of(run_backend_rules(mk.bundle(mk.jv_triple(15), measurements=[ms]),
                                  mk.rules_only("missing_conditions")), "missing_conditions")
    assert out == []


def test_ml_duplicates_with_same_key_are_exported_as_is_d010(mk):
    ml = [mk.issue("contradiction", [], desc="Структура прибора расходится"),
          mk.issue("contradiction", [], desc="Подложка расходится")]
    out = merge_issues([], ml)
    assert [i.description for i in out] == ["Структура прибора расходится", "Подложка расходится"]


def test_paper_scope_key_stable_when_text_changes(mk):
    a = assign_keys([mk.issue("missing_conditions", [], desc="нет площади", by="backend")], mk.PID)[0]
    b = assign_keys([mk.issue("missing_conditions", [], desc="нет спектра", by="backend")], mk.PID)[0]
    assert a.key == b.key


def test_check_paper_treats_previous_merged_as_ml_and_drops_stale_backend(mk, solar_profile):
    stale = mk.issue("range", ["R004"], by="backend", desc="старое")
    merged = mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"], by="backend", page=3, fragment="q")
    merged.merged = True
    out = check_paper(mk.bundle(mk.jv_triple(15), issues=[stale, merged]), solar_profile)
    assert mk.of(out.issues, "range") == []
    jv = mk.of(out.issues, "jv_consistency")
    assert len(jv) == 1 and jv[0].computed_by == "ml"  # правило больше не срабатывает - находка ML остаётся


def test_numbers_are_json_serializable(mk, solar_profile):
    import json
    rs = [*mk.jv_triple("12.5"), mk.result("R010", "PCE", "150", device="DV1", source_table="T")]
    out = check_paper(mk.bundle(rs, materials=[mk.material(eg="0.5")]), solar_profile)
    json.dumps([i.numbers for i in out.issues])
