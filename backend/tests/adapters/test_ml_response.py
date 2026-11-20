"""ml.parse_extract_response: разбор ответа spec/07 §7.0.1 (REQ-060, REQ-028, К-1)."""
from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.extraction import ExtractOutcome
from faktura.ml import parse_extract_response


def test_example_parses_without_loss(example_7_0_1):
    out = parse_extract_response(example_7_0_1, "D021")
    assert isinstance(out, ExtractOutcome)
    b = out.bundle
    assert b.publication_id == "D021"
    assert out.model_version == "extract-0.3"
    assert out.warnings == []
    assert [len(x) for x in (b.materials, b.devices, b.layers, b.measurements, b.results, b.issues)] == [1, 1, 1, 1, 3, 1]
    assert b.publication.title == "Example CZTS solar cell"
    assert b.publication.authors == ["A.B. Ivanov", "C. Petrov"]
    assert b.publication.in_scope is True
    assert out.raw.get("model_version") == "extract-0.3"


def test_all_rows_get_publication_id(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    for _, rows in b.iter_tables():
        for r in rows:
            assert r.publication_id == "D021"


def test_bbox_page_confidence_go_to_meta(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    m = b.materials[0].meta
    assert tuple(m.bbox) == (72.1, 410.3, 290.0, 422.8)
    assert m.page == 6 and m.confidence == pytest.approx(0.81)
    assert b.publication.meta.confidence == pytest.approx(0.97)
    r2 = b.by_id("results", "R002")
    assert tuple(r2.meta.bbox) == (356.1, 438.0, 391.2, 452.3)
    assert r2.meta.confidence == pytest.approx(0.93)
    assert b.layers[0].meta.page == 4
    assert b.issues[0].meta.confidence == pytest.approx(0.8) or b.issues[0].confidence == pytest.approx(0.8)


def test_bbox_not_leaked_into_row_fields(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    dumped = b.results[0].model_dump()
    assert "_bbox" not in dumped and "_confidence" not in dumped


def test_row_without_bbox_has_none(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    assert b.devices[0].meta.bbox is None and b.devices[0].meta.confidence == pytest.approx(0.88)


def test_ml_order_follows_response_order(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    orders = [r.meta.ml_order for r in b.results]
    assert orders == sorted(orders) and len(set(orders)) == 3
    assert [r.result_id for r in b.results] == ["R002", "R003", "R004"]


def test_result_values_kept_verbatim(example_7_0_1):
    r = parse_extract_response(example_7_0_1, "D021").bundle.by_id("results", "R002")
    assert r.raw_value == "3.85" and r.raw_unit == "%"
    assert str(r.source_page) == "13" and r.source_table == "Table 4"
    assert r.source_fragment == "Table 4, B4 (record)"
    assert r.metric_name == "PCE" and r.extraction_status == "extracted"
    d = parse_extract_response(example_7_0_1, "D021").bundle.by_id("results", "R004")
    assert d.duplicate_of == "R003"


def test_measurement_device_list_and_additives(example_7_0_1):
    b = parse_extract_response(example_7_0_1, "D021").bundle
    assert b.measurements[0].device_id == ["DV-B4"]
    assert b.materials[0].additives == []


def test_issue_record_ids(example_7_0_1):
    i = parse_extract_response(example_7_0_1, "D021").bundle.issues[0]
    assert i.record_ids == ["R002", "R003"] and i.check_name == "contradiction"
    assert i.source_page in (13, "13")


def test_ml_metric_and_normalized_fields_dropped(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][0].update(metric_value=3.85, metric_unit="%", normalized_value=3.85, normalized_unit="%")
    r = parse_extract_response(data, "D021").bundle.by_id("results", "R002")
    assert r.metric_value is None and r.metric_unit is None
    assert r.normalized_value is None and r.normalized_unit is None
    assert r.raw_value == "3.85"


def test_spread_value_preserved(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][0]["spread_value"] = 0.12
    data["results"][1]["spread_value"] = "0.30"
    b = parse_extract_response(data, "D021").bundle
    assert Decimal(str(b.by_id("results", "R002").spread_value)) == Decimal("0.12")
    assert Decimal(str(b.by_id("results", "R003").spread_value)) == Decimal("0.30")
    assert b.by_id("results", "R004").spread_value in (None, "")


def test_unknown_fields_go_to_meta_extra(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][0]["vendor_field"] = "xyz"
    r = parse_extract_response(data, "D021").bundle.by_id("results", "R002")
    assert r.meta.extra.get("vendor_field") == "xyz"


def test_out_of_scope_paper_only_publication():
    data = {"model_version": "m1", "publication": {"publication_id": "D050", "title": "Calc only", "year": 2020,
                                                 "DOI": None, "authors": [], "in_scope": False,
                                                 "exclusion_reason": "расчётная работа"},
            "materials": [], "devices": [], "layers": [], "measurements": [], "results": [], "issues": []}
    out = parse_extract_response(data, "D050")
    assert out.bundle.publication.in_scope is False
    assert out.bundle.results == [] and out.bundle.materials == []


def test_missing_optional_arrays_ok():
    data = {"model_version": "m1", "publication": {"publication_id": "D050", "title": "T"}}
    out = parse_extract_response(data, "D050")
    assert out.bundle.results == [] and out.bundle.issues == []


def test_broken_records_dropped_with_warning(example_broken):
    out = parse_extract_response(example_broken, "D021")
    assert out.bundle.materials == []              # bandgap_value = объект
    assert all(r.result_id for r in out.bundle.results)
    assert len(out.bundle.results) <= 2            # "garbage" вместо записи отброшена
    assert len(out.warnings) >= 2
    assert len(out.bundle.devices) == 1 and len(out.bundle.layers) == 1  # остальное цело


def test_envelope_without_publication(example_7_0_1):
    data = {k: v for k, v in example_7_0_1.items() if k != "publication"}
    with pytest.raises(ExtractError) as e:
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_schema_mismatch"


@pytest.mark.parametrize("data", [[], "text", None, 5, {"publication": "x"}, {"publication": []},
                                  {"publication": {"title": "no id"}, "model_version": "m"}])
def test_invalid_envelope_is_schema_mismatch(data):
    with pytest.raises(ExtractError) as e:
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_schema_mismatch"


def test_results_not_a_list_is_mismatch_or_warning(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"] = {"oops": 1}
    try:
        out = parse_extract_response(data, "D021")
    except ExtractError as e:
        assert e.code == "ml_schema_mismatch"
    else:
        assert out.bundle.results == [] and out.warnings


def test_does_not_mutate_input(example_7_0_1):
    before = copy.deepcopy(example_7_0_1)
    parse_extract_response(example_7_0_1, "D021")
    assert example_7_0_1 == before


def test_publication_id_param_used_when_ml_omits_it(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    del data["publication"]["publication_id"]
    try:
        out = parse_extract_response(data, "D077")
    except ExtractError as e:
        assert e.code == "ml_schema_mismatch"
    else:
        assert out.bundle.publication_id == "D077"
