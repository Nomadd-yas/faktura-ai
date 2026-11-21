"""E2E 1: dev-PDF -> faktura extract (мок ML-1) -> 7 CSV -> check_submission.py -> сравнение с dev_gold.

Источники: BACKEND_SPEC (CLI), docs/wave1/02_plan.md (roundtrip REQ-120, коды выхода extract: 0|2 допустимы).
"""
from __future__ import annotations

import csv
import subprocess
import sys

import pytest

from e2e_support import EXCLUDED_PAPER, E2E_PAPERS, MIN_PRECISION, MIN_RECALL, TABLES, read_csv, run_cli

pytestmark = [pytest.mark.data, pytest.mark.slow]


@pytest.fixture(scope="module")
def extracted(tmp_path_factory, e2e_in, e2e_gold, check_script):
    out = tmp_path_factory.mktemp("e2e_out")
    proc = run_cli("extract", "--domain", "solar", "--in", str(e2e_in), "--out", str(out),
                   "--check", str(check_script), "--extractor", "mock", "--gold-dir", str(e2e_gold),
                   "--db", str(out.parent / "e2e.db"), "--no-cache")
    return proc, out


def test_cli_exit_code_ok(extracted):
    proc, _ = extracted
    # 0 - успех, 2 - частичный сбой с записанным набором; 1 (ошибка/NotImplementedError) и 3 (валидатор) - провал
    assert proc.returncode in (0, 2), f"rc={proc.returncode}\n{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}"


def test_exactly_seven_csv_and_meta(extracted):
    _, out = extracted
    assert sorted(p.name for p in out.glob("*.csv")) == sorted(f"{t}.csv" for t in TABLES)
    meta = out / "_meta"
    assert meta.is_dir()
    for name in ("manifest.json", "passport.json", "passport.md", "files.csv", "check_report.txt"):
        assert (meta / name).is_file(), f"нет _meta/{name}"


def test_check_submission_clean(extracted, check_script, e2e_in):
    """Независимый прогон валидатора ТЗ по результату (не полагаясь на --check внутри CLI)."""
    _, out = extracted
    proc = subprocess.run([sys.executable, str(check_script), str(out), str(e2e_in)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-1000:]


def test_headers_match_template(extracted, case_dir):
    _, out = extracted
    for t in TABLES:
        with open(case_dir / "dev_gold" / f"{t}.csv", encoding="utf-8-sig", newline="") as f:
            gold_cols = next(csv.reader(f))
        with open(out / f"{t}.csv", encoding="utf-8-sig", newline="") as f:
            assert next(csv.reader(f)) == gold_cols, t


def test_publications_cover_all_inputs_including_excluded(extracted):
    _, out = extracted
    pubs = {r["publication_id"]: r for r in read_csv(out / "publications.csv")}
    assert set(pubs) == set(E2E_PAPERS)
    ex = pubs[EXCLUDED_PAPER]
    assert ex["in_scope"] == "false"
    assert ex["exclusion_reason"].strip()
    # исключённая статья: ни одной строки данных
    for t in ("results", "measurements", "devices", "layers", "materials"):
        assert not [r for r in read_csv(out / f"{t}.csv") if r["publication_id"] == EXCLUDED_PAPER], t


def test_every_value_has_page_and_quote(extracted):
    _, out = extracted
    rows = [r for r in read_csv(out / "results.csv") if r["extraction_status"] == "extracted"]
    assert rows, "нет извлечённых результатов"
    for r in rows:
        assert r["source_page"].isdigit() and int(r["source_page"]) >= 1, r["result_id"]
        assert r["source_fragment"].strip(), r["result_id"]


def test_compare_with_dev_gold(extracted, e2e_gold):
    """Полнота/точность по 11 метрикам ТЗ (faktura.evaluation.evaluate). Пороги - заглушки (TODO в conftest)."""
    from faktura.domain.tables import EVAL_METRICS
    from faktura.evaluation import evaluate

    _, out = extracted
    rep = evaluate(e2e_gold, out)
    assert set(rep.per_metric) <= set(EVAL_METRICS)
    checked = 0
    for metric, m in rep.per_metric.items():
        if m.get("gold", 0) == 0:
            continue  # в эталоне этих статей метрики может не быть
        recall, precision = m.get("recall"), m.get("precision")
        assert recall is not None and recall >= MIN_RECALL, f"{metric}: recall={recall}"  # TODO порог
        assert precision is not None and precision >= MIN_PRECISION, f"{metric}: precision={precision}"  # TODO порог
        checked += 1
    assert checked >= 4  # PCE, Voc, Jsc, FF есть в эталоне этих статей


def test_cli_eval_command(extracted, e2e_gold, tmp_path):
    _, out = extracted
    proc = run_cli("eval", "--gold", str(e2e_gold), "--pred", str(out), "--json", str(tmp_path / "eval.json"))
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert (tmp_path / "eval.json").is_file()


def test_rerun_is_deterministic(extracted, e2e_in, e2e_gold, check_script, tmp_path):
    """Повторный прогон даёт тот же results.csv (идентификаторы и значения стабильны)."""
    _, out1 = extracted
    out2 = tmp_path / "out2"
    proc = run_cli("extract", "--in", str(e2e_in), "--out", str(out2), "--check", str(check_script),
                   "--extractor", "mock", "--gold-dir", str(e2e_gold), "--db", str(tmp_path / "x.db"), "--no-cache")
    assert proc.returncode in (0, 2), proc.stderr[-1500:]
    assert (out1 / "results.csv").read_bytes() == (out2 / "results.csv").read_bytes()


def test_cli_bad_input_returns_nonzero(tmp_path):
    proc = run_cli("extract", "--in", str(tmp_path / "nope"), "--out", str(tmp_path / "o"))
    assert proc.returncode != 0
