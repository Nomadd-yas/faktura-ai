"""select_for_submission и materialize_manual_fact (spec/14 §14.5; REQ-002..REQ-010, К-14)."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import ReviewStatus
from faktura.domain.records import AddedBy, IssueRecord, PaperBundle
from faktura.export import SelectResult, materialize_manual_fact, select_for_submission


def _rid(b: PaperBundle) -> list[str]:
    return [r.result_id for r in b.results]










def test_corrected_value_replaces_normalized_and_metric_value(make_bundle, profile):
    b = make_bundle()
    b.results[0].meta.corrected_value = Decimal("19.4")
    r = select_for_submission(b, profile).bundle.results[0]
    assert r.normalized_value == Decimal("19.4")
    assert r.metric_value == Decimal("19.4")
    assert r.raw_value == "20.1"  # исходник из статьи остаётся










def _issue(by="ml", conf=None, status="open", check="ambiguity"):
    return IssueRecord(publication_id="P001", issue_id="I9", record_ids=["R001"], check_name=check, description="x",
                       computed_by=by, confidence=conf, status=status)




















# --- ручные факты ------------------------------------------------------------------------------













# --- materialize_manual_fact -------------------------------------------------------------------















