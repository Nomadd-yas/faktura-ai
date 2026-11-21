"""Модели записей, профиль, идентификаторы, безопасность, настройки."""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError, ERROR_CATALOG
from faktura.domain.ids import issue_key, new_id, stable_id
from faktura.domain.profile import builtin_solar_profile, metric_to_param, param_to_metric
from faktura.domain.records import ROW_MODELS
from faktura.domain.security import MIN_PASSWORD_LEN, generate_password, hash_password, verify_password
from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, TABLES


@pytest.mark.parametrize("table", list(TABLES)[:1])
def test_model_fields_match_template_columns(table):
    model = ROW_MODELS[table]
    fields = [f for f in model.model_fields if f != "meta"]
    assert list(TABLES[table]) == [f for f in fields if f in TABLES[table]]  # порядок и состав колонок
    assert set(TABLES[table]) <= set(fields)












