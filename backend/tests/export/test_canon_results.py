"""Выгрузка results: metric_* = normalized_* при заполненном normalized_value (check_submission RAG/solar)."""
from faktura.api.corpus.exports import canon_results


def test_metric_pair_always_equals_normalized_pair_raw_keeps_paper_record():
    rows = canon_results([
        # RAG: из статьи 0.734 (доля), нормализовано в %
        {"metric_value": "0.734", "metric_unit": "fraction", "raw_value": "0.734", "raw_unit": "",
         "normalized_value": "73.4", "normalized_unit": "%"},
        # solar: мВ -> В, raw пуст: исходная запись уходит в raw
        {"metric_value": "1050", "metric_unit": "mV", "raw_value": "", "raw_unit": "",
         "normalized_value": "1.05", "normalized_unit": "V"},
        # правка вручную (v2): normalized_value новое, normalized_unit не был задан
        {"metric_value": "21.3", "metric_unit": "%", "raw_value": "21,3 %", "raw_unit": "%",
         "normalized_value": "21.5", "normalized_unit": ""},
        # без нормализации - не трогаем
        {"metric_value": "", "metric_unit": "", "raw_value": "n/a", "raw_unit": "", "normalized_value": "",
         "normalized_unit": ""},
    ])
    for r in rows[:3]:
        assert r["metric_value"] == r["normalized_value"] and r["metric_unit"] == r["normalized_unit"]
    assert rows[0]["metric_unit"] == "%" and rows[0]["raw_value"] == "0.734"
    assert (rows[1]["raw_value"], rows[1]["raw_unit"], rows[1]["metric_value"]) == ("1050", "mV", "1.05")
    assert rows[2]["metric_unit"] == "%" and rows[2]["metric_value"] == "21.5" and rows[2]["raw_value"] == "21,3 %"
    assert rows[3]["metric_value"] == "" and rows[3]["raw_value"] == "n/a"
