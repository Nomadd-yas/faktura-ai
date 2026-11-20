"""merge_issues: 4 правила слияния (К-15, REQ-056) и инварианты."""
from __future__ import annotations

import copy
import itertools
import random

import pytest

from faktura.checks import merge_issues
from faktura.domain.tables import CHECK_CATALOG

JV = ["R004", "R001", "R002", "R003"]


def _backend_jv(mk, ids=None, **kw):
    return mk.issue("jv_consistency", ids or JV, desc="PCE 2.36 % не согласуется с Voc·Jsc·FF/Pin = 3.11 %",
                    page=12, fragment="backend fragment", by="backend", numbers={"calc": 3.11, "pce": 2.36},
                    severity="warn", **kw)


def _single(out, check):
    r = [i for i in out if i.check_name == check]
    assert len(r) == 1, r
    return r[0]


# --- правило 1: точное совпадение ключа ---------------------------------------------------------------
def test_rule1_exact_key_one_record_ml_wins_ids_page_fragment_confidence(mk):
    be = _backend_jv(mk)
    ml = mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"], desc="текст ML", page=13,
                  fragment="ml quote", confidence=0.8)
    out = merge_issues([be], [ml])
    assert len(out) == 1
    m = out[0]
    assert m.record_ids == ["R001", "R002", "R003", "R004"]  # порядок от ML
    assert (m.source_page, m.source_fragment, m.confidence) == (13, "ml quote", 0.8)
    assert m.description == be.description and m.numbers["calc"] == 3.11 and m.severity == "warn"
    assert m.computed_by == "backend" and m.merged is True


def test_rule1_ml_empty_page_and_fragment_keep_backend_values(mk):
    ml = mk.issue("jv_consistency", JV, page=None, fragment=None)
    m = merge_issues([_backend_jv(mk)], [ml])[0]
    assert (m.source_page, m.source_fragment) == (12, "backend fragment")


def test_rule1_same_set_different_order_still_exact_key(mk):
    out = merge_issues([_backend_jv(mk)], [mk.issue("jv_consistency", list(reversed(JV)), page=1, fragment="q")])
    assert len(out) == 1 and out[0].merged


# --- правило 2: пересечение --------------------------------------------------------------------------
def test_rule2_intersection_ml_ids_extra_backend_ids_go_to_numbers(mk):
    ml = mk.issue("jv_consistency", ["R001", "R002"], page=3, fragment="q", confidence=0.6)
    out = merge_issues([_backend_jv(mk)], [ml])
    assert len(out) == 1
    m = out[0]
    assert m.record_ids == ["R001", "R002"]
    assert set(m.numbers["extra_record_ids"]) == {"R003", "R004"}
    assert m.merged and m.computed_by == "backend" and m.source_page == 3


def test_rule2_ml_superset_of_backend_also_merges(mk):
    ml = mk.issue("jv_consistency", [*JV, "R010"], page=3, fragment="q")
    out = merge_issues([_backend_jv(mk)], [ml])
    assert len(out) == 1 and set(out[0].record_ids) == {*JV, "R010"}


def test_rule2_partial_overlap(mk):
    ml = mk.issue("jv_consistency", ["R003", "R004", "R010"], page=3, fragment="q")
    out = merge_issues([_backend_jv(mk)], [ml])
    assert len(out) == 1 and set(out[0].record_ids) == {"R003", "R004", "R010"}
    assert set(out[0].numbers["extra_record_ids"]) == {"R001", "R002"}


def test_rule2_different_check_name_does_not_merge(mk):
    out = merge_issues([_backend_jv(mk)], [mk.issue("contradiction", ["R001", "R002"])])
    assert len(out) == 2 and not any(i.merged for i in out)


def test_rule2_disjoint_sets_do_not_merge(mk):
    out = merge_issues([_backend_jv(mk)], [mk.issue("jv_consistency", ["R020", "R021"])])
    assert len(out) == 2 and not any(i.merged for i in out)


# --- правило 3: замечание о статье -------------------------------------------------------------------
def test_rule3_paper_level_merges_when_ml_ids_empty(mk):
    be = mk.issue("missing_conditions", [], desc="Условия измерения не указаны", page=3, fragment="Device", by="backend",
                  severity="warn")
    ml = mk.issue("missing_conditions", [], desc="В SI", page=5, fragment="Detailed experi", confidence=0.7)
    out = merge_issues([be], [ml])
    assert len(out) == 1
    m = out[0]
    assert m.record_ids == [] and m.merged and m.computed_by == "backend"
    assert m.description == "Условия измерения не указаны"
    assert (m.source_page, m.source_fragment) == (5, "Detailed experi")


def test_rule3_ml_with_ids_does_not_merge_with_paper_rule(mk):
    be = mk.issue("missing_conditions", [], by="backend", severity="warn", page=3, fragment="x")
    ml = mk.issue("missing_conditions", ["R001"], page=5, fragment="y")
    out = merge_issues([be], [ml])
    assert len(out) == 2


def test_rule3_ml_empty_ids_other_check_not_merged_with_backend_paper(mk):
    be = mk.issue("missing_conditions", [], by="backend", severity="warn", page=3, fragment="x")
    ml = mk.issue("ambiguity", [], page=5, fragment="y")
    out = merge_issues([be], [ml])
    assert len(out) == 2 and _single(out, "ambiguity").computed_by == "ml" and not _single(out, "ambiguity").merged


# --- правило 4: прочее и схлопывание повторов бэка ---------------------------------------------------
def test_rule4_unmatched_stay_as_is(mk):
    ml = mk.issue("ambiguity", ["R001"], desc="Неясно", page=2, fragment="f", confidence=0.4)
    be = mk.issue("range", ["R002"], by="backend", severity="error", page=1, fragment="z")
    out = merge_issues([be], [ml])
    a, r = _single(out, "ambiguity"), _single(out, "range")
    assert (a.computed_by, a.merged, a.confidence, a.description) == ("ml", False, 0.4, "Неясно")
    assert (r.computed_by, r.merged) == ("backend", False)


def test_rule4_backend_duplicates_by_key_collapsed(mk):
    b1 = _backend_jv(mk)
    b2 = _backend_jv(mk, ids=list(reversed(JV)))
    out = merge_issues([b1, b2], [])
    assert len(out) == 1


def test_rule4_backend_paper_scope_duplicates_collapsed(mk):
    mc = lambda: mk.issue("missing_conditions", [], by="backend", severity="warn", page=3, fragment="x")  # noqa: E731
    assert len(merge_issues([mc(), mc()], [])) == 1


def test_empty_inputs(mk):
    assert merge_issues([], []) == []
    ml = mk.issue("typo", [], page=1, fragment="a")
    assert [i.check_name for i in merge_issues([], [ml])] == ["typo"]
    assert [i.check_name for i in merge_issues([_backend_jv(mk)], [])] == ["jv_consistency"]


def test_inputs_not_mutated(mk):
    be, ml = [_backend_jv(mk)], [mk.issue("jv_consistency", ["R001", "R002"], page=3, fragment="q")]
    snap = copy.deepcopy(([i.model_dump() for i in be], [i.model_dump() for i in ml]))
    merge_issues(be, ml)
    assert ([i.model_dump() for i in be], [i.model_dump() for i in ml]) == snap


# --- инвариант: нет двух записей с одним ключом, scope=paper - не больше одной на check_name ----------
def _scenarios(mk):
    mc_b = lambda: mk.issue("missing_conditions", [], by="backend", severity="warn", page=3, fragment="x")  # noqa: E731
    return {
        "exact": ([_backend_jv(mk)], [mk.issue("jv_consistency", JV, page=1, fragment="q")]),
        "intersect": ([_backend_jv(mk)], [mk.issue("jv_consistency", ["R001", "R002"], page=1, fragment="q")]),
        "paper": ([mc_b()], [mk.issue("missing_conditions", [], page=1, fragment="q")]),
        "paper_dupes_backend": ([mc_b(), mc_b()], [mk.issue("missing_conditions", [], page=1, fragment="q")]),
        "ml_twice_same_key": ([_backend_jv(mk)], [mk.issue("jv_consistency", JV, page=1, fragment="a"),
                                                  mk.issue("jv_consistency", JV[::-1], page=2, fragment="b")]),
        "ml_overlaps_two_backend": ([_backend_jv(mk), _backend_jv(mk, ids=["R004", "R005", "R006"])],
                                    [mk.issue("jv_consistency", ["R004"], page=1, fragment="q")]),
        "mixed": ([_backend_jv(mk), mc_b(), mk.issue("range", ["R009"], by="backend", severity="error")],
                  [mk.issue("ambiguity", [], page=1, fragment="a"), mk.issue("contradiction", ["R001", "R002"]),
                   mk.issue("missing_conditions", [], page=2, fragment="b")]),
        "all_empty": ([], []),
    }


@pytest.mark.parametrize("name", ["exact", "intersect", "paper", "paper_dupes_backend", "ml_twice_same_key",
                                  "ml_overlaps_two_backend", "mixed", "all_empty"])
def test_invariant_no_two_records_with_same_key(mk, name):
    be, ml = _scenarios(mk)[name]
    out = merge_issues(be, ml)
    keys = [(i.check_name, frozenset(i.record_ids)) for i in out]
    assert len(keys) == len(set(keys)), keys
    for check, info in CHECK_CATALOG.items():
        if info.scope == "paper":
            assert sum(1 for i in out if i.check_name == check) <= 1, check


@pytest.mark.parametrize("seed", range(6))
def test_record_ids_order_does_not_change_result_keys(mk, seed):
    rnd = random.Random(seed)
    be, ml = _scenarios(mk)["mixed"]
    base = merge_issues(copy.deepcopy(be), copy.deepcopy(ml))
    be2, ml2 = copy.deepcopy(be), copy.deepcopy(ml)
    for i in (*be2, *ml2):
        rnd.shuffle(i.record_ids)
    shuffled = merge_issues(be2, ml2)
    k = lambda xs: sorted((i.check_name, tuple(sorted(i.record_ids)), i.merged) for i in xs)  # noqa: E731
    assert k(base) == k(shuffled)


def test_merge_is_idempotent_for_backend_side(mk):
    be, ml = _scenarios(mk)["intersect"]
    once = merge_issues(be, ml)
    twice = merge_issues(once, [])
    assert len(twice) == len(once) == 1


def test_all_ml_issues_from_gold_like_set_are_kept_or_merged(mk):
    ml = [mk.issue(c, [], page=1, fragment="f") for c in ("typo", "derived_values", "figure_only")]
    ml += [mk.issue("contradiction", ["R001", "R002"], page=1, fragment="f")]
    out = merge_issues([], ml)
    assert {(i.check_name, frozenset(i.record_ids)) for i in out} == {(i.check_name, frozenset(i.record_ids)) for i in ml}
    assert all(i.computed_by == "ml" and not i.merged for i in out)


def test_permutations_of_input_order_give_same_set(mk):
    be, ml = _scenarios(mk)["mixed"]
    ref = {(i.check_name, frozenset(i.record_ids)) for i in merge_issues(be, ml)}
    for pb, pm in itertools.product(itertools.permutations(be), itertools.permutations(ml)):
        got = {(i.check_name, frozenset(i.record_ids)) for i in merge_issues(list(pb), list(pm))}
        assert got == ref
