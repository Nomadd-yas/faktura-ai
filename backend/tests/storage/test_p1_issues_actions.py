"""Этап B: issues.upsert_preserving_review, actions.record / last_undoable."""
from __future__ import annotations

import pytest

from faktura.domain.entities import IssuesDiff
from faktura.domain.errors import AppError
from faktura.domain.records import IssueStatus

NOT_FOUND = (AppError, KeyError, LookupError)


# ---- issues ----------------------------------------------------------------------------------

def _issues(db, paper_id):
    with db.uow() as u:
        return {i.key: i for i in u.papers.load_bundle(paper_id).issues}






def test_upsert_preserves_fixed_and_ok(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-fix", status="fixed"),
                            make_issue("D001", "I-ok", status="ok", records=("R002",)),
                            make_issue("D001", "I-open", records=("R003",))])
    fresh = [make_issue("D001", "I-fix"), make_issue("D001", "I-ok", records=("R002",)),
             make_issue("D001", "I-open", records=("R003",))]  # пересчёт: все снова open
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, fresh)
    got = _issues(db, p.id)
    assert got["I-fix"].status == IssueStatus.fixed and got["I-ok"].status == IssueStatus.ok
    assert got["I-open"].status == IssueStatus.open
    assert d.stale == [] and d.opened == []
















# ---- actions ---------------------------------------------------------------------------------

CH = [{"target": {"entity": "facts", "id": "fct_1"}, "field": "review_status", "before": "new", "after": "accepted"}]










def test_last_undoable_returns_latest(clocked_db, clock, user):
    with clocked_db.uow() as u:
        u.actions.record(user, "accept", "1", CH)
        clock.advance(minutes=1)
        b = u.actions.record(user, "reject", "2", CH)
        assert u.actions.last_undoable(user.id).id == b.id
















