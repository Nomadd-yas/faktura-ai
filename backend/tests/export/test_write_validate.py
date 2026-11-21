"""write_submission, strict_validate, run_check_script (REQ-012..REQ-021, К-3)."""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from faktura.domain.entities import CheckReport, ValidationProblem
from faktura.domain.io import read_tables
from faktura.domain.tables import TABLES
from faktura.export import run_check_script, strict_validate, write_submission


# --- write_submission --------------------------------------------------------------------------

def test_writes_exactly_seven_files(make_bundle, tmp_path):
    paths = write_submission([make_bundle()], tmp_path / "out")
    assert [p.name for p in paths] == [f"{t}.csv" for t in TABLES]
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(f"{t}.csv" for t in TABLES)






















# --- strict_validate ---------------------------------------------------------------------------

def _tables(make_bundle, tables_of):
    return tables_of([make_bundle()])


def _by_level(problems, level):
    return [p for p in problems if p.level == level]
































# --- run_check_script --------------------------------------------------------------------------

FAKE_OK = '''
import sys
print("таблиц: 7, результатов: 2, ошибок: 0, предупреждений: 1, проверено цитат: 10, не найдено: 2")
print("внимание: results.csv, строка 2: цитата не найдена на с. 3 файла P001.pdf: «abc»")
sys.exit(0)
'''
FAKE_ERR = '''
import sys
print("ОШИБКА: materials.csv, строка 2: material_class = «x» — нет в списке допустимых")
print("ОШИБКА: results.csv, строка 3: metric_value = «y» — не число")
print("внимание: publications.csv: P001 исключена без exclusion_reason")
print("\\nтаблиц: 7, результатов: 2, ошибок: 2, предупреждений: 1")
sys.exit(1)
'''
FAKE_CRASH = '''
raise RuntimeError("boom")
'''
























