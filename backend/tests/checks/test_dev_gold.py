"""Приёмка на данных dev_gold (маркер data): воспроизведение эталонных замечаний бэка, снимок лишних, перф."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from faktura.checks import BACKEND_RULES, check_paper, merge_issues, run_backend_rules
from faktura.domain.profile import builtin_solar_profile
from faktura.domain.tables import CHECK_CATALOG

pytestmark = pytest.mark.data

SNAPSHOT = Path(__file__).parent / "snapshots" / "dev_extra_issues.json"
BACKEND_OWNED = ("jv_consistency", "eqe_consistency", "champion_vs_mean")


def _key(i):
    return (i.publication_id, i.check_name, frozenset(i.record_ids))


@pytest.fixture(scope="module")
def profile():
    return builtin_solar_profile()


def test_ml_only_plus_rules_reproduce_gold_backend_issues(gold_bundles, profile):
    expected, produced = {}, set()
    for b in gold_bundles:
        for i in b.issues:
            if i.check_name in BACKEND_OWNED:
                expected.setdefault(i.check_name, set()).add(_key(i))
        ml_only = [i for i in b.issues if i.check_name not in BACKEND_OWNED]
        merged = merge_issues(run_backend_rules(b, profile.rules), ml_only)
        produced |= {_key(i) for i in merged}
    assert {k: len(v) for k, v in expected.items()} == {"jv_consistency": 7, "eqe_consistency": 1,
                                                        "champion_vs_mean": 1}
    for name, keys in expected.items():
        missing = keys - produced
        assert not missing, f"{name}: бэк не воспроизвёл {sorted(map(str, missing))}"


def test_d021_has_no_false_jv_consistency(gold_bundles, profile):
    b = next(x for x in gold_bundles if x.publication_id == "D021")
    assert [i for i in run_backend_rules(b, profile.rules) if i.check_name == "jv_consistency"] == []


def _all_mode_extras(gold_bundles, profile):
    extras, missing = [], []
    for b in gold_bundles:
        merged = merge_issues(run_backend_rules(b, profile.rules), b.issues)
        got = {_key(i) for i in merged}
        missing += [k for k in {_key(i) for i in b.issues} if k not in got]
        gold_keys = {_key(i) for i in b.issues}
        extras += [i for i in merged if _key(i) not in gold_keys]
        # ML-замечания выгружаются как есть (в эталоне D010 два contradiction без record_ids); проверяем добавки бэка
        keys = [_key(i) for i in merged if _key(i) not in gold_keys]
        assert len(keys) == len(set(keys)), f"{b.publication_id}: добавки бэка дублируются по ключу"
    return extras, missing


def test_issues_all_every_gold_key_present(gold_bundles, profile):
    _, missing = _all_mode_extras(gold_bundles, profile)
    assert missing == []


def test_issues_all_extras_only_backend_rules_and_match_snapshot(gold_bundles, profile):
    extras, _ = _all_mode_extras(gold_bundles, profile)
    allowed = {n for n, i in CHECK_CATALOG.items() if i.computed_by in ("backend", "both")} & set(BACKEND_RULES)
    assert all(i.check_name in allowed for i in extras), [i.check_name for i in extras if i.check_name not in allowed]
    actual = sorted([i.publication_id, i.check_name, sorted(i.record_ids)] for i in extras)
    if os.environ.get("FAKTURA_UPDATE_SNAPSHOT") == "1":
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(actual, ensure_ascii=False, indent=1), encoding="utf-8")
    assert SNAPSHOT.is_file(), f"нет снимка {SNAPSHOT}; создать осознанно: FAKTURA_UPDATE_SNAPSHOT=1"
    assert actual == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_backend_issues_source_from_anchor_and_russian_description(gold_bundles, profile):
    checked = 0
    for b in gold_bundles:
        for i in run_backend_rules(b, profile.rules):
            assert re.search(r"[а-яА-Я]{3,}", i.description or ""), (b.publication_id, i.check_name)
            assert i.check_name in BACKEND_RULES and i.computed_by == "backend"
            if i.record_ids:
                anchor = b.by_id("results", i.record_ids[0])
                if anchor is not None and anchor.source_page is not None:
                    assert i.source_page == anchor.source_page, (b.publication_id, i.check_name)
                    if anchor.source_fragment:
                        assert i.source_fragment == anchor.source_fragment
                    checked += 1
    assert checked > 0


def test_backend_scope_paper_at_most_one_per_check(gold_bundles, profile):
    for b in gold_bundles:
        out = run_backend_rules(b, profile.rules)
        for name, info in CHECK_CATALOG.items():
            if info.scope == "paper":
                assert sum(1 for i in out if i.check_name == name) <= 1, (b.publication_id, name)


def test_no_comparability_on_gold_with_default_profile(gold_bundles, profile):
    for b in gold_bundles:
        assert not [i for i in run_backend_rules(b, profile.rules) if i.check_name == "comparability"]


def test_run_twice_same_result_on_all_gold(gold_bundles, profile):
    for b in gold_bundles:
        a = [(i.check_name, tuple(i.record_ids), i.description) for i in run_backend_rules(b, profile.rules)]
        c = [(i.check_name, tuple(i.record_ids), i.description) for i in run_backend_rules(b, profile.rules)]
        assert a == c


@pytest.mark.perf
def test_perf_check_paper_d021_under_100ms_x3(gold_bundles, profile):
    b = next(x for x in gold_bundles if x.publication_id == "D021")
    assert len(b.results) == 122
    check_paper(b, profile)  # прогрев
    best = min(_time(lambda: check_paper(b, profile)) for _ in range(5))
    assert best <= 0.1 * 3, f"{best * 1000:.0f} мс"


def _time(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t
