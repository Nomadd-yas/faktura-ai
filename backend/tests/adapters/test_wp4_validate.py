"""WP4: строгая проверка ответа модели (ml/validate.py), ретраи и значения по умолчанию."""
from __future__ import annotations

import copy

import httpx
import pytest

from faktura.config import Settings
from faktura.domain.errors import ExtractError, public_reason
from faktura.ml import HttpExtractor
from faktura.ml.response import parse_extract_response


def test_duplicate_result_ids_is_error_with_public_reason(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][1]["result_id"] = data["results"][0]["result_id"]
    with pytest.raises(ExtractError) as e:
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_invalid_data"
    text = public_reason(e.value.code, e.value.message)
    assert text.startswith("Модель вернула некорректные данные: ")
    assert "R002" in text and "ML" not in text and "бэк" not in text.lower()


def test_record_without_id_and_non_list_table_are_errors(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    del data["results"][0]["result_id"]
    with pytest.raises(ExtractError) as e:
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_invalid_data"
    data = copy.deepcopy(example_7_0_1)
    data["devices"] = {"oops": 1}
    with pytest.raises(ExtractError) as e:
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_schema_mismatch"


def test_dangling_device_ref_keeps_row_clears_cell(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][0]["device_id"] = "DV-NOPE"
    data["layers"][0]["device_id"] = "DV-NOPE"
    data["measurements"][0]["device_id"] = ["DV-B4", "DV-NOPE"]
    out = parse_extract_response(data, "D021")
    b = out.bundle
    assert b.by_id("results", "R002").device_id is None
    assert b.layers[0].device_id is None
    assert b.measurements[0].device_id == ["DV-B4"]
    assert len(b.results) == 3
    assert any("DV-NOPE" in w for w in out.warnings)


def test_vocab_mismatch_cleared_with_warning_and_case_fixed(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["devices"][0]["device_role"] = "Record"      # регистр -> исправляется молча
    data["devices"][0]["architecture"] = "weird"      # нет в словаре -> очищается
    out = parse_extract_response(data, "D021")
    d = out.bundle.devices[0]
    assert d.device_role == "record" and d.architecture is None
    assert any("architecture" in w and "weird" in w for w in out.warnings)


def test_duplicate_of_self_and_cycle_cleared(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["results"][0]["duplicate_of"] = "R002"
    data["results"][1]["duplicate_of"] = "R004"
    data["results"][2]["duplicate_of"] = "R003"
    out = parse_extract_response(data, "D021")
    r = {x.result_id: x for x in out.bundle.results}
    assert r["R002"].duplicate_of is None
    # цикл R003 -> R004 -> R003 разорван
    assert not (r["R003"].duplicate_of == "R004" and r["R004"].duplicate_of == "R003")


def test_valid_example_has_no_validation_warnings(example_7_0_1):
    out = parse_extract_response(copy.deepcopy(example_7_0_1), "D021")
    assert not any("очищен" in w or "вне словаря" in w for w in out.warnings)


def test_retry_defaults_are_three_attempts():
    s = Settings(_env_file=None)
    assert s.retry_delays == (20.0, 60.0)
    assert s.ml_pause_max_s <= 300
    assert s.file_timeout_s == 600


def test_http_three_attempts_then_unavailable(extract_req):
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503)

    sleeps: list[float] = []
    ext = HttpExtractor("http://ml:8000", timeout_s=5, retry_delays=Settings(_env_file=None).retry_delays,
                        sleep=sleeps.append, transport=httpx.MockTransport(handler))
    with pytest.raises(ExtractError) as e:
        ext.extract(extract_req())
    assert len(calls) == 3 and sleeps == [20.0, 60.0]
    assert public_reason(e.value.code, e.value.message) == "Сервис разбора временно недоступен"


def test_request_timeout_capped_by_file_timeout(tmp_path):
    from faktura.ml.adapters import build_extractor

    s = Settings(_env_file=None, extractor="http", extract_url="http://ml:1", extract_timeout_s=1800,
                 file_timeout_s=600)
    assert build_extractor(s).timeout_s == 600
