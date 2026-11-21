"""Приёмка модуля export на реальных данных: dev_gold -> io -> select -> sanitize -> write -> check_submission.

Маркер data: пропускается без data/caseB_kejs5.
"""
from __future__ import annotations

from collections import Counter

import pytest

from faktura.domain.io import bundles_from_tables, parse_cell, read_tables
from faktura.domain.profile import builtin_solar_profile
from faktura.domain.tables import EVAL_METRICS, TABLES
from faktura.evaluation import evaluate, evaluate_issues
from faktura.export import (number_issues, run_check_script, sanitize, select_for_submission, strict_validate,
                            write_submission)

pytestmark = pytest.mark.data

# К-7: encapsulation (unencapsulated -> false); gold-ячейка layer_thickness «63–68» не число и чистится;
# REQ-037: у D010/M1 в эталоне bandgap_unit=eV без bandgap_value - санитайзер очищает единицу
TOLERATED = {"devices": {"encapsulation"}, "layers": {"layer_thickness", "thickness_unit", "note"},
             "materials": {"bandgap_unit"}, "issues": {"issue_id"}}


@pytest.fixture(scope="module")
def pipeline_out(tmp_path_factory, dev_gold_dir):
    tmp = tmp_path_factory.mktemp("dev_out")
    profile = builtin_solar_profile()
    bundles = bundles_from_tables(read_tables(dev_gold_dir))
    selected, events = [], []
    for b in bundles:
        r = select_for_submission(b, profile)
        selected.append(r.bundle)
        events += r.events
    san = sanitize(selected)
    final = [number_issues(b) for b in san.bundles]
    write_submission(final, tmp)
    return tmp, events, san.events


def _cells(rows, table, drop=()):
    cols = [c for c in TABLES[table] if c not in drop]
    return Counter(tuple(parse_cell(table, c, r.get(c, "")) if not isinstance(parse_cell(table, c, r.get(c, "")), list)
                         else tuple(parse_cell(table, c, r.get(c, ""))) for c in cols) for r in rows)


@pytest.mark.slow
def test_check_submission_zero_errors(pipeline_out, check_script, dev_dir):
    out, _, _ = pipeline_out
    rep = run_check_script(check_script, out, dev_dir)
    assert rep.crashed is False
    assert rep.errors == [] and rep.ok is True


def test_strict_validator_zero_errors(pipeline_out):
    out, _, _ = pipeline_out
    assert [p for p in strict_validate(read_tables(out)) if p.level == "error"] == []


def test_seven_files_bom_crlf_header(pipeline_out):
    out, _, _ = pipeline_out
    assert sorted(p.name for p in out.iterdir()) == sorted(f"{t}.csv" for t in TABLES)
    for t, cols in TABLES.items():
        raw = (out / f"{t}.csv").read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert raw[3:].split(b"\r\n", 1)[0].decode().split(",") == list(cols)


@pytest.mark.parametrize("table", list(TABLES))
def test_cells_match_gold_up_to_order(pipeline_out, dev_gold_dir, table):
    out, _, _ = pipeline_out
    got = read_tables(out)[table]
    want = read_tables(dev_gold_dir)[table]
    assert len(got) == len(want)
    drop = TOLERATED.get(table, set())
    assert _cells(got, table, drop) == _cells(want, table, drop)


def test_at_most_four_encapsulation_cells_differ(pipeline_out, dev_gold_dir):
    out, _, _ = pipeline_out
    got = Counter(r["encapsulation"] for r in read_tables(out)["devices"])
    want = Counter(r["encapsulation"] for r in read_tables(dev_gold_dir)["devices"])
    diff = sum((got - want).values())
    assert diff <= 4


def test_spread_values_preserved(pipeline_out, dev_gold_dir):
    out, _, _ = pipeline_out
    got = sorted(float(r["spread_value"]) for r in read_tables(out)["results"] if r["spread_value"])
    want = sorted(float(r["spread_value"]) for r in read_tables(dev_gold_dir)["results"] if r["spread_value"])
    assert len(want) == 49 and got == want


def test_issues_with_empty_source_untouched_and_warned(pipeline_out, dev_gold_dir):
    out, _, _ = pipeline_out
    gold_empty = [r for r in read_tables(dev_gold_dir)["issues"] if not r["source_page"] or not r["source_fragment"]]
    got_empty = [r for r in read_tables(out)["issues"] if not r["source_page"] or not r["source_fragment"]]
    assert len(got_empty) == len(gold_empty)
    warns = [p for p in strict_validate(read_tables(out)) if p.code == "issue_source_missing"]
    assert len(warns) == len(gold_empty)


def test_sanitizer_events_are_few_on_clean_gold(pipeline_out):
    _, sel_events, san_events = pipeline_out
    assert len(san_events) <= 10  # только «63–68» и редкие правки


def test_evaluate_dev_output_vs_gold(pipeline_out, dev_gold_dir):
    out, _, _ = pipeline_out
    rep = evaluate(dev_gold_dir, out)
    assert set(rep.per_metric) == set(EVAL_METRICS)
    for m in EVAL_METRICS:
        pm = rep.per_metric[m]
        if pm["gold"]:
            assert pm["f1"] == 1, m
    assert rep.totals["gold"] > 0


def test_evaluate_gold_vs_gold_full(dev_gold_dir):
    rep = evaluate(dev_gold_dir, dev_gold_dir)
    assert (rep.totals["precision"], rep.totals["recall"], rep.totals["f1"]) == (1, 1, 1)
    assert rep.totals["gold"] >= 600  # 659 results, из них 11 метрик ТЗ


def test_evaluate_issues_gold_vs_gold_full(dev_gold_dir):
    rep = evaluate_issues(dev_gold_dir, dev_gold_dir)
    assert rep.recall == 1 and rep.precision == 1 and rep.fn == 0


def test_manual_fact_roundtrip_on_gold(pipeline_out, dev_gold_dir, check_script, dev_dir, tmp_path, make_fact):
    """Ручной факт с цитатой -> строка RM1, check_submission без ошибок; без цитаты - не выгружен."""
    profile = builtin_solar_profile()
    bundles = bundles_from_tables(read_tables(dev_gold_dir))
    b = next(x for x in bundles if x.results)
    pid = b.publication_id
    dev = b.devices[0].device_id if b.devices else None
    good = make_fact(publication_id=pid, row_id=dev)
    bad = make_fact(publication_id=pid, source_fragment=None, source_table=None, source_figure=None)
    sel = select_for_submission(b, profile, manual_facts=[good, bad])
    rm = [r for r in sel.bundle.results if r.result_id.startswith("RM")]
    assert [r.result_id for r in rm] == ["RM1"]
    assert sel.events
    san = sanitize([sel.bundle])
    write_submission([number_issues(san.bundles[0])], tmp_path)
    rep = run_check_script(check_script, tmp_path, dev_dir)
    assert rep.crashed is False and rep.errors == []
