"""check_ml_issues: висячие record_ids и ненайденная цитата -> пометки, замечания не удаляются (REQ-055)."""
from __future__ import annotations

import copy

from faktura.checks import check_ml_issues


def _b(mk):
    return mk.bundle([mk.result("R001", "PCE", 15), mk.result("R002", "Voc", 1)])


def test_valid_issue_passes_unchanged_without_notes(mk):
    iss = [mk.issue("contradiction", ["R001", "R002"], page=3, fragment="exhibited PCE")]
    out = check_ml_issues(iss, _b(mk), mk.FakeQuote(found={"exhibited PCE"}))
    assert len(out) == 1 and out[0].record_ids == ["R001", "R002"]
    assert out[0].computed_by == "ml" and out[0].meta.notes_backend == []


def test_dangling_record_id_is_marked_not_removed(mk):
    out = check_ml_issues([mk.issue("contradiction", ["R001", "R999"])], _b(mk), None)
    assert len(out) == 1
    assert out[0].meta.notes_backend and any("R999" in n for n in out[0].meta.notes_backend)
    assert out[0].record_ids == ["R001", "R999"]  # ссылки не стираются


def test_all_ids_dangling_still_kept(mk):
    out = check_ml_issues([mk.issue("ambiguity", ["R404"])], _b(mk), None)
    assert len(out) == 1 and out[0].meta.notes_backend


def test_quote_not_found_marks_note_keeps_fragment(mk):
    out = check_ml_issues([mk.issue("typo", [], page=2, fragment="нет такого текста")], _b(mk),
                          mk.FakeQuote(answer=False))
    assert len(out) == 1 and out[0].source_fragment == "нет такого текста"
    assert out[0].meta.notes_backend


def test_quote_none_means_not_checked_no_note(mk):
    out = check_ml_issues([mk.issue("typo", [], page=2, fragment="x")], _b(mk), mk.FakeQuote(answer=None))
    assert out[0].meta.notes_backend == []


def test_no_quote_checker_skips_quote_check(mk):
    out = check_ml_issues([mk.issue("typo", [], page=2, fragment="x")], _b(mk), None)
    assert out[0].meta.notes_backend == []


def test_table_row_reference_fragment_is_not_checked(mk):
    q = mk.FakeQuote(answer=False)
    out = check_ml_issues([mk.issue("jv_consistency", ["R001"], page=11, fragment="Table 1, строка C-ED-1")],
                          _b(mk), q)
    assert out[0].meta.notes_backend == []
    assert (11, "Table 1, строка C-ED-1") not in q.calls


def test_empty_fragment_and_empty_ids_ok(mk):
    q = mk.FakeQuote(answer=False)
    out = check_ml_issues([mk.issue("figure_only", [], page=None, fragment=None)], _b(mk), q)
    assert len(out) == 1 and out[0].meta.notes_backend == []


def test_empty_list_returns_empty(mk):
    assert check_ml_issues([], _b(mk), None) == []


def test_order_and_count_preserved(mk):
    iss = [mk.issue("typo", [], fragment="a"), mk.issue("ambiguity", ["R404"]), mk.issue("derived_values", ["R001"])]
    out = check_ml_issues(iss, _b(mk), None)
    assert [i.check_name for i in out] == ["typo", "ambiguity", "derived_values"]


def test_input_not_mutated(mk):
    iss = [mk.issue("contradiction", ["R999"], fragment="x")]
    snap = copy.deepcopy([i.model_dump() for i in iss])
    check_ml_issues(iss, _b(mk), mk.FakeQuote(answer=False))
    assert [i.model_dump() for i in iss] == snap


def test_ml_fields_confidence_and_description_preserved(mk):
    i = mk.issue("possible_swap", ["R001"], desc="Значения перепутаны: 5 и 7", confidence=0.7)
    out = check_ml_issues([i], _b(mk), None)[0]
    assert out.confidence == 0.7 and out.description == "Значения перепутаны: 5 и 7"
