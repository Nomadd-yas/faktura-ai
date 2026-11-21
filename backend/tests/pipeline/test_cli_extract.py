"""faktura extract: коды выхода 0/1/2/3, 7 CSV, кеш, дубли, недоступный ML (план 5.6, P0). Без данных кейса."""
from __future__ import annotations

import csv
import json
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from faktura.domain.tables import TABLES

TABLE_FILES = sorted(f"{t}.csv" for t in TABLES)


def rows(out: Path, table: str) -> list[dict]:
    with open(out / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def files_csv(out: Path) -> dict[str, dict]:
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        return {r["file"]: r for r in csv.DictReader(f)}


@pytest.fixture
def base(clock, run_cli, pdf_dir, mini_gold_dir, tmp_path):
    """extract на моке mini_gold; base(*extra, out=...) -> CliResult."""
    def _run(*extra, src=None, out=None, mock=True, env=None):
        out = out or tmp_path / "out"
        args = ["extract", "--domain", "solar", "--in", src or pdf_dir, "--out", out, "--retry-delays", "1,1,1"]
        if mock:
            args += ["--extractor", "mock", "--gold-dir", mini_gold_dir]
        return run_cli(*args, *extra, env=env)
    _run.out = tmp_path / "out"
    return _run


# --- успешный прогон ---------------------------------------------------------------------------------------------

def test_ok_exit_0_seven_csv_and_meta(base):
    r = base()
    assert r.exit_code == 0, r
    assert sorted(p.name for p in base.out.glob("*.csv")) == TABLE_FILES
    assert (base.out / "_meta").is_dir()
    assert [x["publication_id"] for x in rows(base.out, "publications")] == ["D001", "D003"]
















# --- проверка сдачи и код 3 -------------------------------------------------------------------------------------------











# --- неверные аргументы -> 1 -----------------------------------------------------------------------------------------























# --- файлы, не принятые к разбору: строка publications только с id, код 2 (REQ-024) ----------------------------------













# --- ML по http: кеш, дубли, пауза (шпионский транспорт httpx) -----------------------------------------------------------

@pytest.fixture
def http_base(clock, run_cli, http_ml, tmp_path, make_pdf):
    """Четыре разных PDF P1..P4 (разное содержимое) и вызов extract --extractor http."""
    src = tmp_path / "hsrc"
    for i in range(1, 5):
        make_pdf([f"unique text {i}"], name=f"P{i}.pdf", directory=src)

    def _run(*extra, out=None, delays="1,1,1"):
        out = out or tmp_path / "hout"
        return run_cli("extract", "--in", src, "--out", out, "--extractor", "http", "--extract-url", "http://ml.test",
                       "--retry-delays", delays, *extra)

    _run.src, _run.out, _run.spy = src, tmp_path / "hout", http_ml
    return _run






















# --- недоступный ML (REQ-065) --------------------------------------------------------------------------------------------------









# --- import-raw / поведение до записи ------------------------------------------------------------------------------------------------





