"""Паспорт набора в формате фронта (§7а.6): данные областей сверены с шаблонами сдачи."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from faktura.domain.registry import RAG_SCHEMA_PATH, get_domain
from faktura.passport.run_passport import build_run_passport, passport_json_bytes, passport_md

TEMPLATE = Path(__file__).resolve().parents[3] / "data" / "caseB_kejs5" / "submission_template"


def _domain(dom: str) -> dict:
    return build_run_passport(dom, model_versions=["m-1"], rules="r", dictionary="d")["domains"][0]


@pytest.mark.parametrize("dom", ["solar", "rag"])
def test_entities_match_registry(dom):
    spec = get_domain(dom)
    ents = _domain(dom)["entities"]
    assert [e["id"] for e in ents] == list(spec.tables)
    for e in ents:
        assert [f["name"] for f in e["fields"]] == list(spec.tables[e["id"]])
        for f in e["fields"]:
            assert f["type"] in ("string", "int", "number", "bool", "enum", "list")
            assert set(f) == {"name", "type", "unit", "required", "description", "values", "ref"}


def test_solar_headers_match_template_csv():
    if not TEMPLATE.is_dir():
        pytest.skip("нет шаблона кейса")
    for e in _domain("solar")["entities"]:
        head = next(csv.reader((TEMPLATE / f"{e['id']}.csv").read_text(encoding="utf-8-sig").splitlines()))
        assert [f["name"] for f in e["fields"]] == head


def test_rag_columns_match_schema_json():
    schema = json.loads(RAG_SCHEMA_PATH.read_text(encoding="utf-8"))
    tables = schema.get("tables") or schema
    for e in _domain("rag")["entities"]:
        t = tables[e["id"]]
        cols = t["columns"] if isinstance(t, dict) and "columns" in t else t
        names = [c["name"] if isinstance(c, dict) else c for c in cols]
        assert [f["name"] for f in e["fields"]] == names


@pytest.mark.parametrize("dom", ["solar", "rag"])
def test_guides_rules_and_shape(dom):
    d = _domain(dom)
    assert [g["id"] for g in d["guides"]] == ["inclusion", "comparability", "extraction"]
    assert all(g["items"] and g["description"] for g in d["guides"])
    assert all(r["level"] in ("error", "warn", "info", "format") and r["computed_by"] in ("бэк", "ML", "бэк + ML")
               for r in d["rules"])
    assert d["normalization"] and all({"quantity", "from", "to", "factor", "note"} == set(n) for n in d["normalization"])


def test_versions_and_determinism():
    p = build_run_passport("solar", model_versions=["b", "a", None], rules="checks-3", dictionary="dict-7")
    assert p["versions"] == {"model": "a, b", "rules": "checks-3", "dictionary": "dict-7"}
    assert build_run_passport("solar")["versions"]["model"] is None
    assert passport_json_bytes(p) == passport_json_bytes(build_run_passport(
        "solar", model_versions=["a", "b"], rules="checks-3", dictionary="dict-7"))
    md = passport_md(p)
    assert md.startswith("# Паспорт данных «Фактура AI»") and "| metric_name | словарь |" in md
    assert "Собран с версиями: model a, b, rules checks-3, dictionary dict-7." in md
    assert "×0.001" in md and "×1000" in md


def test_unknown_domain():
    from faktura.domain.errors import AppError

    with pytest.raises(AppError):
        build_run_passport("chem")
