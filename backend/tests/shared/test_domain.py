"""Модели записей, профиль, идентификаторы, безопасность, настройки."""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError, ERROR_CATALOG
from faktura.domain.ids import issue_key, new_id, stable_id
from faktura.domain.profile import builtin_solar_profile, metric_to_param, param_to_metric
from faktura.domain.records import ROW_MODELS
from faktura.domain.security import MIN_PASSWORD_LEN, generate_password, hash_password, verify_password
from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, TABLES


@pytest.mark.parametrize("table", list(TABLES))
def test_model_fields_match_template_columns(table):
    model = ROW_MODELS[table]
    fields = [f for f in model.model_fields if f != "meta"]
    assert list(TABLES[table]) == [f for f in fields if f in TABLES[table]]  # порядок и состав колонок
    assert set(TABLES[table]) <= set(fields)


def test_builtin_profile():
    p = builtin_solar_profile()
    assert p.profile_id == "prf_solar_tz_v1"
    assert {f.key for f in p.fields if f.evaluated} == set(EVAL_METRICS)
    assert p.rules["jv_consistency"].params["threshold_pct"] == 3
    assert p.rules["eqe_consistency"].params["threshold_pct"] == 5
    assert p.rules["comparability"].enabled is False
    assert p.flags.export_absent is False
    assert all(n in CHECK_CATALOG for n in p.rules)


def test_param_mapping():
    assert param_to_metric("pce") == "PCE"
    assert param_to_metric("jsc_eqe") == "Jsc_EQE"
    assert param_to_metric("bandgap") is None
    assert metric_to_param("Voc") == "voc"


def test_issue_key_deterministic():
    a = issue_key("jv_consistency", ["R2", "R1"])
    assert a == issue_key("jv_consistency", ["R1", "R2"])
    assert a != issue_key("contradiction", ["R1", "R2"])
    assert issue_key("missing_conditions", [], {"k": 1}) == issue_key("missing_conditions", [], {"k": 1})
    assert stable_id("iss", "a", 1) == stable_id("iss", "a", 1)
    assert new_id("crp") != new_id("crp")
    assert len(new_id("crp").split("_")[1]) == 12


def test_security():
    h = hash_password("correct-horse")
    assert h.startswith("$argon2id$")
    assert verify_password("correct-horse", h)
    assert not verify_password("wrong-horse", h)
    assert not verify_password("x", "not-a-hash")
    with pytest.raises(AppError) as e:
        hash_password("a" * (MIN_PASSWORD_LEN - 1))
    assert e.value.code == "validation_error" and e.value.field == "password"
    assert len(generate_password()) == 16


def test_error_catalog():
    assert AppError("not_found").http_status == 404
    assert AppError("conflict").http_status == ERROR_CATALOG["conflict"][0] == 409


def test_settings_defaults(settings):
    assert settings.retry_delays == (20.0, 60.0)
    assert settings.extract_timeout_s == 330
    assert settings.autolink_duplicates == "auto"
    assert settings.bbox_origin == "top-left"
