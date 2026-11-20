"""TABLES, VOCAB, NUMERIC_STRICT 1:1 с check_submission.py (разбор через ast, без выполнения скрипта)."""
from __future__ import annotations

import ast
import csv
from pathlib import Path

import pytest

from faktura.domain.tables import CHECK_CATALOG, EVAL_METRICS, NUMERIC_STRICT, TABLES, TARGET_UNITS, VOCAB

pytestmark = pytest.mark.data


def _literal(script: Path, name: str):
    tree = ast.parse(script.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} не найден в {script}")


def test_tables_match_script(check_script):
    script_tables = _literal(check_script, "TABLES")
    assert {k: tuple(v.split()) for k, v in script_tables.items()} == TABLES
    assert list(script_tables) == list(TABLES)  # порядок таблиц


def test_vocab_match_script(check_script):
    script_vocab = _literal(check_script, "VOCAB")
    assert {k: frozenset(v) for k, v in script_vocab.items()} == VOCAB


def test_numeric_strict_match_script(check_script):
    assert set(_literal(check_script, "NUMERIC")) == set(NUMERIC_STRICT)


def test_columns_match_submission_template(case_dir):
    for table, cols in TABLES.items():
        with open(case_dir / "submission_template" / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f))
        assert tuple(header) == cols, table


def test_check_catalog_covers_dev_gold(dev_gold_dir):
    with open(dev_gold_dir / "issues.csv", encoding="utf-8-sig", newline="") as f:
        names = {r["check_name"] for r in csv.DictReader(f)}
    assert len(names) == 21
    assert names <= set(CHECK_CATALOG)


def test_eval_metrics_have_target_units():
    assert len(EVAL_METRICS) == 11
    assert set(EVAL_METRICS) <= set(TARGET_UNITS)
