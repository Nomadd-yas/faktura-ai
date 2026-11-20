"""check_paper: правила + ML-находки + слияние + ключи + (опц.) автосвязывание повторов; CheckOutcome."""
from __future__ import annotations

import copy

from faktura.checks import RULES_VERSION, CheckOutcome, DuplicateLink, check_paper


def test_rules_version_is_nonempty_string():
    from faktura.checks import RULES_VERSION as v
    assert isinstance(v, str) and v


def test_outcome_shape_and_timings(mk, solar_profile):
    out = check_paper(mk.bundle(mk.jv_triple("12.5")), solar_profile)
    assert isinstance(out, CheckOutcome) and isinstance(out.issues, list) and isinstance(out.links, list)
    assert isinstance(out.timings, dict) and out.timings
    assert all(isinstance(k, str) and isinstance(v, (int, float)) and v >= 0 for k, v in out.timings.items())


def test_backend_issue_gets_key_and_is_found(mk, solar_profile):
    out = check_paper(mk.bundle(mk.jv_triple("12.5")), solar_profile)
    jv = mk.of(out.issues, "jv_consistency")
    assert len(jv) == 1 and jv[0].key.startswith("I-") and jv[0].computed_by == "backend"


def test_ml_issue_from_bundle_merged_with_backend(mk, solar_profile):
    ml = mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"], page=13, fragment="ml", confidence=0.9)
    out = check_paper(mk.bundle(mk.jv_triple("12.5"), issues=[ml]), solar_profile)
    jv = mk.of(out.issues, "jv_consistency")
    assert len(jv) == 1 and jv[0].merged and jv[0].source_page == 13


def test_ml_only_findings_pass_through_with_keys(mk, solar_profile):
    ml = [mk.issue("typo", [], page=2, fragment="x"), mk.issue("ambiguity", ["R001"], page=3, fragment="y")]
    out = check_paper(mk.bundle([mk.result("R001", "PCE", 15)], issues=ml), solar_profile)
    assert {i.check_name for i in out.issues} >= {"typo", "ambiguity"}
    assert all(i.key for i in out.issues) and len({i.key for i in out.issues}) == len(out.issues)


def test_dangling_ml_ids_marked_via_quote_and_bundle(mk, solar_profile):
    ml = mk.issue("contradiction", ["R404"], page=2, fragment="nope")
    out = check_paper(mk.bundle([mk.result("R001", "PCE", 15)], issues=[ml]), solar_profile,
                      quote=mk.FakeQuote(answer=False))
    c = mk.of(out.issues, "contradiction")[0]
    assert c.meta.notes_backend


def test_backend_rules_flag_off_gives_only_ml_issues(mk, solar_profile):
    solar_profile.flags.backend_rules = False
    ml = mk.issue("typo", [], page=2, fragment="x")
    out = check_paper(mk.bundle(mk.jv_triple("12.5"), issues=[ml]), solar_profile)
    assert [(i.check_name, i.computed_by, i.merged) for i in out.issues] == [("typo", "ml", False)]


def test_default_profile_does_not_create_comparability(mk, solar_profile):
    ms = mk.measurement(spectrum="halogen lamp", intensity=None, unit=None)
    out = check_paper(mk.bundle(mk.jv_triple(15), measurements=[ms]), solar_profile)
    assert mk.of(out.issues, "comparability") == []


def test_ml_comparability_passes_through_as_is(mk, solar_profile):
    ml = mk.issue("comparability", ["R004"], page=5, fragment="halogen lamp")
    out = check_paper(mk.bundle(mk.jv_triple(15), issues=[ml]), solar_profile)
    c = mk.of(out.issues, "comparability")
    assert len(c) == 1 and c[0].computed_by == "ml"


def test_autolink_false_returns_no_links(mk, solar_profile):
    rs = [mk.result("R001", "PCE", 15, source_table="Table 1"), mk.result("R002", "PCE", 15, source_figure="Fig 1")]
    assert check_paper(mk.bundle(rs), solar_profile, autolink=False).links == []


def test_autolink_true_returns_links_and_does_not_mutate_bundle(mk, solar_profile):
    rs = [mk.result("R001", "PCE", 15, source_table="Table 1"), mk.result("R002", "PCE", 15, source_figure="Fig 1")]
    b = mk.bundle(rs)
    snap = b.model_dump()
    out = check_paper(b, solar_profile, autolink=True)
    assert out.links == [DuplicateLink(result_id="R002", duplicate_of="R001", note="связано бэком")]
    assert b.model_dump() == snap


def test_deterministic_two_runs_same_keys_and_order(mk, solar_profile):
    b = mk.bundle([*mk.jv_triple("12.5"), mk.result("R010", "PCE", 150, device="DV1")],
                  issues=[mk.issue("typo", [], page=1, fragment="x")])
    a = [(i.key, i.check_name, tuple(i.record_ids)) for i in check_paper(copy.deepcopy(b), solar_profile).issues]
    c = [(i.key, i.check_name, tuple(i.record_ids)) for i in check_paper(copy.deepcopy(b), solar_profile).issues]
    assert a and a == c


def test_no_duplicate_keys_in_outcome(mk, solar_profile):
    b = mk.bundle([*mk.jv_triple("12.5")], issues=[mk.issue("jv_consistency", ["R004", "R001", "R002", "R003"],
                                                            page=1, fragment="q")])
    out = check_paper(b, solar_profile)
    ks = [(i.check_name, frozenset(i.record_ids)) for i in out.issues]
    assert len(ks) == len(set(ks))


def test_failed_stub_bundle_does_not_crash(mk, solar_profile):
    from faktura.domain.records import PaperBundle
    out = check_paper(PaperBundle.failed_stub("X1"), solar_profile)
    assert isinstance(out, CheckOutcome)


def test_issues_have_publication_id_of_bundle(mk, solar_profile):
    out = check_paper(mk.bundle(mk.jv_triple("12.5")), solar_profile)
    assert out.issues and all(i.publication_id == mk.PID for i in out.issues)
