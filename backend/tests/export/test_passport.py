"""build_passport и passport_markdown (spec/04 §4.17; REQ-112..REQ-114, К-9)."""
from __future__ import annotations

import json

import pytest

from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, TABLES, TARGET_UNITS, VOCAB
from faktura.passport import build_passport, passport_markdown


@pytest.fixture
def passport(profile):
    return build_passport(profile)


def _table(p, name):
    return next(t for t in p["tables"] if t["name"] == name)


def test_top_level_keys(passport):
    for k in ("collection", "version", "schema_version", "scope", "tables", "units", "statuses", "rules"):
        assert k in passport
    assert passport["collection"] == "solar"


def test_json_serializable(passport):
    assert json.loads(json.dumps(passport, ensure_ascii=False)) == passport


def test_seven_tables_in_order(passport):
    assert [t["name"] for t in passport["tables"]] == list(TABLES)


@pytest.mark.parametrize("table", list(TABLES))
def test_all_columns_present_in_order(passport, table):
    t = _table(passport, table)
    assert [f["name"] for f in t["fields"]] == list(TABLES[table])
    assert t["key"]
    assert all(f.get("type") and f.get("description") is not None for f in t["fields"])


def test_vocabularies_match_template(passport):
    for (table, col), values in VOCAB.items():
        f = next(f for f in _table(passport, table)["fields"] if f["name"] == col)
        assert f["type"] == "enum" and set(f["values"]) == set(values)


def test_all_eleven_metrics_listed(passport):
    f = next(f for f in _table(passport, "results")["fields"] if f["name"] == "metric_name")
    assert set(EVAL_METRICS) <= set(f["values"]) and len(EVAL_METRICS) == 11


def test_numeric_fields_typed_number(passport):
    for table, col in [("results", "metric_value"), ("results", "source_page"), ("layers", "layer_order"),
                       ("devices", "active_area")]:
        f = next(f for f in _table(passport, table)["fields"] if f["name"] == col)
        assert f["type"] in ("number", "integer")


def test_links_present(passport):
    links = {(l["field"], l["to"]) for l in _table(passport, "results")["links"]}
    assert {("device_id", "devices.device_id"), ("measurement_id", "measurements.measurement_id"),
            ("material_id", "materials.material_id"), ("duplicate_of", "results.result_id")} <= links
    assert ("absorber_material_id", "materials.material_id") in {
        (l["field"], l["to"]) for l in _table(passport, "devices")["links"]}


def test_units_cover_metrics(passport):
    text = json.dumps(passport["units"], ensure_ascii=False)
    for unit in set(TARGET_UNITS.values()) | {"eV", "nm", "cm2"}:
        assert unit in text


def test_statuses(passport):
    s = passport["statuses"]
    assert s["extraction_status"] == ["extracted", "present_not_extractable", "absent"]
    assert set(s["review_status"]) == {"new", "accepted", "rejected"}


def test_rules_from_profile(passport, profile):
    ids = [r["id"] for r in passport["rules"]]
    assert set(profile.rules) <= set(ids)
    for r in passport["rules"]:
        assert r["id"] in CHECK_CATALOG
        assert r["title"] and r["level"] in ("error", "warn", "info", "conflict", "doubt")
        assert r["computed_by"] in ("backend", "ml", "both")


def test_internal_rules_not_in_passport(passport):
    ids = {r["id"] for r in passport["rules"]}
    assert not ids & {"unit_conversion", "low_confidence", "ocr_low_confidence"}


def test_deterministic(profile):
    assert build_passport(profile) == build_passport(profile)


def test_scope_mentions_tandem_exclusion(passport):
    assert "тандем" in passport["scope"].lower()


# --- Markdown ----------------------------------------------------------------------------------

def test_markdown_is_string_with_headings(passport):
    md = passport_markdown(passport)
    assert isinstance(md, str) and md.lstrip().startswith("#")


def test_markdown_lists_tables_columns_metrics_rules(passport):
    md = passport_markdown(passport)
    for t, cols in TABLES.items():
        assert t in md
        for c in cols:
            assert c in md
    for m in EVAL_METRICS:
        assert m in md
    assert "jv_consistency" in md


def test_markdown_lists_vocab_values(passport):
    md = passport_markdown(passport)
    for v in ("perovskite", "kesterite", "champion", "present_not_extractable"):
        assert v in md


def test_markdown_deterministic(passport):
    assert passport_markdown(passport) == passport_markdown(passport)


def test_markdown_handles_minimal_passport():
    md = passport_markdown({"collection": "solar", "version": "1.0", "tables": [], "units": [], "rules": [],
                            "statuses": {}})
    assert isinstance(md, str) and md
