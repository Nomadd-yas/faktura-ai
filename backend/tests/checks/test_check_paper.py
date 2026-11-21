"""check_paper: правила + ML-находки + слияние + ключи + (опц.) автосвязывание повторов; CheckOutcome."""
from __future__ import annotations

import copy

from faktura.checks import RULES_VERSION, CheckOutcome, DuplicateLink, check_paper








def test_ml_issue_from_bundle_replaces_backend_duplicate(mk, solar_profile):
    ml = mk.issue("jv_consistency", ["R001", "R002", "R003", "R004"], page=13, fragment="ml", confidence=0.9)
    out = check_paper(mk.bundle(mk.jv_triple("12.5"), issues=[ml]), solar_profile)
    jv = mk.of(out.issues, "jv_consistency")
    assert len(jv) == 1 and jv[0].computed_by == "ml" and jv[0].source_page == 13






















