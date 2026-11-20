"""evaluate, evaluate_issues, accuracy_response, LocalEval, FakturaEvalAdapter (spec/14 §14.7; К-9).

Контракт EvalReport: per_metric[m] и totals - словари с ключами gold, pred, tp, fp, fn, precision, recall, f1;
для метрики без строк в эталоне (T80, Rsh в mini_gold) precision/recall/f1 = None и
per_metric[m]["insufficient_data"] is True; в warnings есть текст «мало данных» с названием метрики.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from faktura.domain.ports import EvalAdapter, EvalReport, IssueEvalReport
from faktura.domain.tables import EVAL_METRICS
from faktura.evaluation import (FakturaEvalAdapter, LocalEval, accuracy_response, evaluate, evaluate_issues)

# метрики, которые есть в mini_gold
PRESENT = ("PCE", "Voc", "Jsc", "FF", "Rs", "HI", "Jsc_EQE", "stabilized_PCE", "retained_PCE")
ABSENT = ("Rsh", "T80")


@pytest.fixture
def gold(mini_gold_dir):
    return mini_gold_dir


@pytest.fixture
def pred(gold, tmp_path):
    d = tmp_path / "pred"
    shutil.copytree(gold, d)
    return d


def _edit(d: Path, table: str, fn):
    p = d / f"{table}.csv"
    with open(p, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        cols = rd.fieldnames
        rows = [dict(r) for r in rd]
    rows = fn(rows)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _pce_rows(rows):
    return [r for r in rows if r["metric_name"] == "PCE"]


# --- evaluate ----------------------------------------------------------------------------------

def test_report_type_and_all_eleven_metrics(gold):
    rep = evaluate(gold, gold)
    assert isinstance(rep, EvalReport)
    assert set(rep.per_metric) == set(EVAL_METRICS)


def test_gold_vs_gold_perfect_on_present_metrics(gold):
    rep = evaluate(gold, gold)
    for m in PRESENT:
        pm = rep.per_metric[m]
        assert pm["gold"] > 0
        assert (pm["precision"], pm["recall"], pm["f1"]) == (1, 1, 1), m
        assert pm["fp"] == 0 and pm["fn"] == 0 and pm["tp"] == pm["gold"]
    t = rep.totals
    assert (t["precision"], t["recall"], t["f1"]) == (1, 1, 1)
    assert t["gold"] == sum(rep.per_metric[m]["gold"] for m in EVAL_METRICS)


def test_metrics_with_no_gold_rows_marked_insufficient(gold):
    rep = evaluate(gold, gold)
    for m in ABSENT:
        pm = rep.per_metric[m]
        assert pm["gold"] == 0 and pm["insufficient_data"] is True
        assert pm["f1"] is None
        assert any(m in w and "мало данных" in w for w in rep.warnings)
    for m in PRESENT:
        assert not rep.per_metric[m].get("insufficient_data")


def test_non_evaluated_metrics_are_ignored(gold, pred):
    def drop_jmpp(rows):
        return [r for r in rows if r["metric_name"] != "Jmpp"]
    _edit(pred, "results", drop_jmpp)
    rep = evaluate(gold, pred)
    assert rep.totals["fp"] == 0 and rep.totals["fn"] == 0
    assert "Jmpp" not in rep.per_metric


def test_metrics_argument_restricts_scope(gold):
    rep = evaluate(gold, gold, metrics=("PCE", "Voc"))
    assert set(rep.per_metric) == {"PCE", "Voc"}
    assert rep.totals["gold"] == rep.per_metric["PCE"]["gold"] + rep.per_metric["Voc"]["gold"]


def test_missing_prediction_is_fn(gold, pred):
    n_gold = evaluate(gold, gold).per_metric["PCE"]["gold"]

    def drop_one(rows):
        for i, r in enumerate(rows):
            if r["metric_name"] == "PCE" and not r["duplicate_of"]:
                return rows[:i] + rows[i + 1:]
        return rows
    _edit(pred, "results", drop_one)
    pm = evaluate(gold, pred).per_metric["PCE"]
    assert pm["fn"] == 1 and pm["fp"] == 0 and pm["tp"] == n_gold - 1
    assert pm["recall"] == pytest.approx((n_gold - 1) / n_gold) and pm["precision"] == 1


def test_wrong_value_is_fp_and_fn(gold, pred):
    def break_one(rows):
        for r in rows:
            if r["metric_name"] == "Voc" and r["normalized_value"]:
                r["normalized_value"] = r["metric_value"] = "9.99"
                break
        return rows
    _edit(pred, "results", break_one)
    pm = evaluate(gold, pred).per_metric["Voc"]
    assert pm["fp"] == 1 and pm["fn"] == 1


def test_last_digit_rounding_tolerated(gold, pred):
    def nudge(rows):
        for r in rows:
            if r["metric_name"] == "Jsc" and r["normalized_value"] == "15.28":
                r["normalized_value"] = r["metric_value"] = "15.29"
        return rows
    _edit(pred, "results", nudge)
    pm = evaluate(gold, pred).per_metric["Jsc"]
    assert pm["fp"] == 0 and pm["fn"] == 0 and pm["f1"] == 1


def test_extra_prediction_is_fp(gold, pred):
    def add(rows):
        extra = dict(rows[0])
        extra.update(result_id="R900", metric_name="PCE", device_id="DV1", measurement_id="MS1",
                     normalized_value="3.33", metric_value="3.33", aggregation="median", time_point="")
        return rows + [extra]
    _edit(pred, "results", add)
    pm = evaluate(gold, pred).per_metric["PCE"]
    assert pm["fp"] == 1 and pm["fn"] == 0 and pm["recall"] == 1 and pm["precision"] < 1


def test_result_ids_do_not_matter_only_keys(gold, pred):
    def rename(rows):
        for r in rows:
            r["result_id"] = "X" + r["result_id"]
            if r["duplicate_of"]:
                r["duplicate_of"] = "X" + r["duplicate_of"]
        return rows
    _edit(pred, "results", rename)
    assert evaluate(gold, pred).totals["f1"] == 1


def test_row_order_does_not_matter(gold, pred):
    _edit(pred, "results", lambda rows: list(reversed(rows)))
    assert evaluate(gold, pred).totals["f1"] == 1


def test_device_matched_by_label_not_by_id(gold, pred):
    def swap_ids(rows):
        m = {r["device_id"]: "Z" + r["device_id"] for r in rows}
        for r in rows:
            r["device_id"] = m[r["device_id"]]
        return rows
    _edit(pred, "devices", swap_ids)

    def rename_results(rows):
        for r in rows:
            if r["device_id"]:
                r["device_id"] = "Z" + r["device_id"]
        return rows
    _edit(pred, "results", rename_results)
    _edit(pred, "layers", lambda rows: [dict(r, device_id="Z" + r["device_id"]) if r["device_id"] else r for r in rows])
    _edit(pred, "measurements", lambda rows: [dict(r, device_id=";".join("Z" + x for x in r["device_id"].split(";")
                                                                          if x)) for r in rows])
    assert evaluate(gold, pred).totals["f1"] == 1


def test_empty_prediction_zero_recall(gold, pred):
    _edit(pred, "results", lambda rows: [])
    rep = evaluate(gold, pred)
    assert rep.totals["recall"] == 0 and rep.totals["tp"] == 0 and rep.totals["fn"] == rep.totals["gold"]
    assert rep.totals["precision"] in (None, 0)


def test_missing_prediction_dir_is_empty_prediction(gold, tmp_path):
    rep = evaluate(gold, tmp_path / "nothing")
    assert rep.totals["recall"] == 0


def test_in_scope_accuracy_if_reported(gold, pred):
    rep = evaluate(gold, pred)
    assert rep.totals.get("in_scope_accuracy", 1) == 1


def test_source_page_accuracy_drops_when_page_changed(gold, pred):
    base = evaluate(gold, gold).totals.get("source_page_accuracy", 1)

    def shift(rows):
        for r in rows:
            if r["source_page"]:
                r["source_page"] = str(int(r["source_page"]) + 1)
        return rows
    _edit(pred, "results", shift)
    changed = evaluate(gold, pred)
    assert base == 1
    assert changed.totals["source_page_accuracy"] < 1
    assert changed.totals["f1"] == 1  # страница не влияет на сопоставление значений


def test_report_is_json_serializable(gold):
    json.dumps(evaluate(gold, gold).model_dump(), default=str)


# --- evaluate_issues ---------------------------------------------------------------------------

def test_issues_gold_vs_gold(gold):
    rep = evaluate_issues(gold, gold)
    assert isinstance(rep, IssueEvalReport)
    assert rep.recall == 1 and rep.precision == 1 and rep.tp == 8 and rep.fp == 0 and rep.fn == 0
    assert rep.by_check["contradiction"]["tp"] == 2


def test_issues_missing_one_lowers_recall(gold, pred):
    _edit(pred, "issues", lambda rows: rows[:-1])
    rep = evaluate_issues(gold, pred)
    assert rep.fn == 1 and rep.recall == pytest.approx(7 / 8) and rep.precision == 1


def test_issues_extra_lowers_precision(gold, pred):
    _edit(pred, "issues", lambda rows: rows + [dict(rows[0], issue_id="I99", check_name="range")])
    rep = evaluate_issues(gold, pred)
    assert rep.fp == 1 and rep.recall == 1 and rep.precision == pytest.approx(8 / 9)


def test_issues_record_ids_order_and_issue_id_ignored(gold, pred):
    def flip(rows):
        out = []
        for i, r in enumerate(rows):
            r = dict(r, issue_id=f"Q{i}", record_ids=";".join(reversed(r["record_ids"].split(";"))) if r["record_ids"] else "")
            out.append(r)
        return out
    _edit(pred, "issues", flip)
    assert evaluate_issues(gold, pred).recall == 1


def test_issues_different_record_ids_is_miss(gold, pred):
    _edit(pred, "issues", lambda rows: [dict(rows[0], record_ids="R001")] + rows[1:])
    rep = evaluate_issues(gold, pred)
    assert rep.fn == 1 and rep.fp == 1


def test_issues_empty_gold_recall_none(tmp_path, pred):
    g = tmp_path / "g"
    g.mkdir()
    (g / "issues.csv").write_text("publication_id,issue_id,record_ids,check_name,description,source_page,"
                                  "source_fragment\r\n", encoding="utf-8-sig")
    rep = evaluate_issues(g, pred)
    assert rep.recall is None and rep.tp == 0


def test_issues_empty_prediction(gold, pred):
    _edit(pred, "issues", lambda rows: [])
    rep = evaluate_issues(gold, pred)
    assert rep.recall == 0 and rep.fn == 8 and rep.precision in (None, 0)


# --- accuracy_response -------------------------------------------------------------------------

def test_accuracy_response_shape(gold):
    rep = evaluate(gold, gold)
    r = accuracy_response(rep, collection="solar", model_version="extractor-2026.11")
    assert r["collection"] == "solar" and r["model_version"] == "extractor-2026.11"
    assert set(r["total"]) >= {"gold", "extracted", "correct", "p", "r", "f1"}
    assert r["total"]["gold"] == rep.totals["gold"]
    assert r["total"]["p"] == 1 and r["total"]["r"] == 1 and r["total"]["f1"] == 1
    assert r["linked"] is None  # нет bbox - привязку не считаем
    assert "normalized" in r
    for k in ("by_param", "by_source", "by_paper", "errors"):
        assert isinstance(r[k], list)
    json.dumps(r)


def test_accuracy_response_by_param_lowercase_labels(gold):
    r = accuracy_response(evaluate(gold, gold), collection="solar", model_version=None)
    params = {p["param"] for p in r["by_param"]}
    assert "pce" in params and "jsc_eqe" in params
    for p in r["by_param"]:
        assert set(p) >= {"param", "label", "gold", "extracted", "correct", "p", "r", "f1"}
    assert r["model_version"] is None


def test_accuracy_response_insufficient_metrics_have_null_scores(gold):
    r = accuracy_response(evaluate(gold, gold), collection="solar", model_version="m")
    t80 = next(p for p in r["by_param"] if p["param"] == "t80")
    assert t80["gold"] == 0 and t80["f1"] is None


def test_accuracy_response_errors_listed_for_mismatch(gold, pred):
    _edit(pred, "results", lambda rows: [r for r in rows if not (r["metric_name"] == "FF")][:])
    r = accuracy_response(evaluate(gold, pred), collection="solar", model_version="m")
    assert r["total"]["r"] < 1
    assert any(e["kind"] == "miss" for e in r["errors"])
    for e in r["errors"]:
        assert set(e) >= {"kind", "kind_label", "paper_id", "param"}


def test_accuracy_response_empty_report():
    r = accuracy_response(EvalReport(), collection="solar", model_version="m")
    assert r["total"]["gold"] == 0 and r["by_param"] == [] and r["errors"] == []


# --- адаптеры ----------------------------------------------------------------------------------

def test_local_eval_equals_evaluate(gold, pred):
    _edit(pred, "results", lambda rows: rows[:-3])
    a = LocalEval().evaluate(gold, pred)
    b = evaluate(gold, pred)
    assert a.model_dump() == b.model_dump()


def test_adapters_satisfy_protocol():
    assert isinstance(LocalEval(), EvalAdapter)
    assert isinstance(FakturaEvalAdapter(), EvalAdapter)


@pytest.fixture
def fake_faktura_eval(tmp_path, monkeypatch):
    """Поддельный исполняемый `faktura-eval` в PATH: печатает EvalReport как JSON в stdout."""
    d = tmp_path / "bin"
    d.mkdir()
    report = EvalReport(per_metric={"PCE": {"gold": 2, "pred": 2, "tp": 2, "fp": 0, "fn": 0, "precision": 1.0,
                                           "recall": 1.0, "f1": 1.0}},
                        totals={"gold": 2, "tp": 2, "f1": 1.0}, warnings=["T80: мало данных"])
    (d / "payload.json").write_text(report.model_dump_json(), encoding="utf-8")
    (d / "fake_eval.py").write_text(
        "import sys, pathlib\nprint(pathlib.Path(__file__).with_name('payload.json').read_text(encoding='utf-8'))\n",
        encoding="utf-8")
    if os.name == "nt":
        (d / "faktura-eval.cmd").write_text(f'@"{sys.executable}" "%~dp0fake_eval.py" %*\r\n')
    else:
        exe = d / "faktura-eval"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_eval.py" "$@"\n')
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))
    return report


def test_faktura_eval_adapter_parses_subprocess_json(fake_faktura_eval, gold, pred):
    rep = FakturaEvalAdapter().evaluate(gold, pred)
    assert isinstance(rep, EvalReport)
    assert rep.per_metric["PCE"]["tp"] == 2 and rep.totals["f1"] == 1.0
    assert rep.warnings == ["T80: мало данных"]


def test_faktura_eval_adapter_not_installed_gives_clear_error(gold, pred, monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(Exception) as ei:
        FakturaEvalAdapter().evaluate(gold, pred)
    assert not isinstance(ei.value, NotImplementedError)
    assert "faktura-eval" in str(ei.value)
