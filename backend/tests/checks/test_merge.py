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


# --- ML как есть; бэк только добавляет ---------------------------------------------------------------
def test_exact_key_ml_kept_as_is_backend_dropped(mk):
    be = _backend_jv(mk)
    ml = mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"], desc="текст ML", page=13,
                  fragment="ml quote", confidence=0.8)
    out = merge_issues([be], [ml])
    assert len(out) == 1
    m = out[0]
    assert m.record_ids == ["R001", "R002", "R003", "R004"]
    assert (m.source_page, m.source_fragment, m.confidence, m.description) == (13, "ml quote", 0.8, "текст ML")
    assert m.computed_by == "ml" and m.merged is False and not m.numbers


















# --- правило 4: прочее и схлопывание повторов бэка ---------------------------------------------------










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










