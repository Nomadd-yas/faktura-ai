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










