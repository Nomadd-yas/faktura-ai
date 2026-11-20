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


def test_upsert_into_empty_opens_all(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3)
    new = [make_issue("D001", "I-a"), make_issue("D001", "I-b", records=("R002",))]
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, new)
    assert isinstance(d, IssuesDiff) and set(d.opened) == {"I-a", "I-b"}
    assert d.closed == [] and d.stale == [] and d.changed == []
    assert set(_issues(db, p.id)) == {"I-a", "I-b"}


def test_upsert_same_issues_is_noop(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-a")])
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-a")])
    assert d == IssuesDiff()


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


def test_upsert_vanished_reviewed_issue_marked_stale(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-fix", status="fixed"), make_issue("D001", "I-gone")])
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, [])
    assert d.stale == ["I-fix"]          # проверенное вручную не теряется, а помечается stale
    assert d.closed == ["I-gone"]        # непроверенное закрывается
    got = _issues(db, p.id)
    assert "I-fix" in got and got["I-fix"].status == IssueStatus.fixed
    assert "I-gone" not in got


def test_upsert_reappearing_stale_issue_is_revived(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-fix", status="fixed")])
    with db.uow() as u:
        u.issues.upsert_preserving_review(p.id, [])
        d = u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-fix")])
    assert d.stale == [] and _issues(db, p.id)["I-fix"].status == IssueStatus.fixed


def test_upsert_changed_description_reported(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-a", description="old")])
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-a", description="new")])
    assert d.changed == ["I-a"]
    assert _issues(db, p.id)["I-a"].description == "new"


def test_upsert_is_per_paper(db, new_paper, process, make_issue):
    p1, p2 = new_paper("D001"), new_paper("D002")
    process(p1, n=2, issues=[make_issue("D001", "I-a")])
    process(p2, n=2, issues=[make_issue("D002", "I-a")])
    with db.uow() as u:
        u.issues.upsert_preserving_review(p1.id, [])
    assert "I-a" in _issues(db, p2.id)


def test_upsert_unknown_paper(db, make_issue):
    with pytest.raises(NOT_FOUND):
        with db.uow() as u:
            u.issues.upsert_preserving_review("pap_nope", [make_issue("D001", "I-a")])


def test_upsert_duplicate_keys_in_input_collapsed(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=2)
    with db.uow() as u:
        d = u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-a"), make_issue("D001", "I-a")])
    assert d.opened == ["I-a"]
    assert list(_issues(db, p.id)) == ["I-a"]


def test_upsert_updates_paper_open_issue_counter(db, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3)
    with db.uow() as u:
        u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-a"), make_issue("D001", "I-b")])
        assert u.papers.get(p.id).stats.open_issues == 2
        u.issues.upsert_preserving_review(p.id, [make_issue("D001", "I-a")])
        assert u.papers.get(p.id).stats.open_issues == 1


# ---- actions ---------------------------------------------------------------------------------

CH = [{"target": {"entity": "facts", "id": "fct_1"}, "field": "review_status", "before": "new", "after": "accepted"}]


def test_action_record_fields(clocked_db, user):
    with clocked_db.uow() as u:
        a = u.actions.record(user, "accept", "Принято значение", CH, paper_id="pap_1", fact_id="fct_1",
                             group_id="grp_1")
    assert a.id and a.user_id == user.id and a.action == "accept" and a.label == "Принято значение"
    assert a.changes == CH and a.paper_id == "pap_1" and a.fact_id == "fct_1" and a.group_id == "grp_1"
    assert a.undone is False and a.workspace_id == user.workspace_id and a.created_at is not None


def test_action_record_defaults_optional(clocked_db, user):
    with clocked_db.uow() as u:
        a = u.actions.record(user, "export", "Выгрузка", [])
    assert a.paper_id is None and a.fact_id is None and a.group_id is None and a.changes == []


def test_action_ids_unique(clocked_db, user):
    with clocked_db.uow() as u:
        assert len({u.actions.record(user, "edit", "x", CH).id for _ in range(30)}) == 30


def test_last_undoable_none_when_empty(clocked_db, user):
    with clocked_db.uow() as u:
        assert u.actions.last_undoable(user.id) is None


def test_last_undoable_returns_latest(clocked_db, clock, user):
    with clocked_db.uow() as u:
        u.actions.record(user, "accept", "1", CH)
        clock.advance(minutes=1)
        b = u.actions.record(user, "reject", "2", CH)
        assert u.actions.last_undoable(user.id).id == b.id


def test_last_undoable_user_isolation(clocked_db, user):
    from faktura.domain.entities import CurrentUser
    other = CurrentUser(id="usr_2", workspace_id=user.workspace_id, email="o@example.com")
    with clocked_db.uow() as u:
        u.actions.record(user, "accept", "mine", CH)
        assert u.actions.last_undoable(other.id) is None


def test_last_undoable_window_24h(clocked_db, clock, user):
    with clocked_db.uow() as u:
        a = u.actions.record(user, "accept", "old", CH)
        clock.advance(hours=23, minutes=59)
        assert u.actions.last_undoable(user.id).id == a.id
        clock.advance(minutes=2)
        assert u.actions.last_undoable(user.id) is None


def test_last_undoable_custom_window(clocked_db, clock, user):
    with clocked_db.uow() as u:
        u.actions.record(user, "accept", "x", CH)
        clock.advance(hours=2)
        assert u.actions.last_undoable(user.id, within_h=1) is None
        assert u.actions.last_undoable(user.id, within_h=3) is not None


def test_last_undoable_skips_undone(clocked_db, clock, user):
    """Допущение: actions.mark_undone(action_id)."""
    with clocked_db.uow() as u:
        a = u.actions.record(user, "accept", "1", CH)
        clock.advance(minutes=1)
        b = u.actions.record(user, "reject", "2", CH)
        u.actions.mark_undone(b.id)
        assert u.actions.last_undoable(user.id).id == a.id
        u.actions.mark_undone(a.id)
        assert u.actions.last_undoable(user.id) is None


def test_last_undoable_depth_50(clocked_db, clock, user):
    """Отмена доступна только для 50 последних действий: если все они уже отменены, старшие недоступны."""
    with clocked_db.uow() as u:
        old = u.actions.record(user, "accept", "old", CH)
        recent = []
        for i in range(50):
            clock.advance(minutes=1)
            recent.append(u.actions.record(user, "edit", f"e{i}", CH))
        for a in recent:
            u.actions.mark_undone(a.id)
        assert u.actions.last_undoable(user.id) is None       # old - 51-е с конца
        assert u.actions.last_undoable(user.id, depth=51).id == old.id


def test_last_undoable_depth_boundary_49_undone(clocked_db, clock, user):
    with clocked_db.uow() as u:
        recent = []
        for i in range(50):
            clock.advance(minutes=1)
            recent.append(u.actions.record(user, "edit", f"e{i}", CH))
        for a in recent[1:]:
            u.actions.mark_undone(a.id)
        assert u.actions.last_undoable(user.id).id == recent[0].id  # 50-е с конца - ещё в глубине


def test_mark_undone_unknown_action(clocked_db):
    with pytest.raises(NOT_FOUND):
        with clocked_db.uow() as u:
            u.actions.mark_undone("act_nope")


def test_action_roundtrips_nested_changes(clocked_db, user):
    ch = [{"target": {"entity": "facts", "id": "f"}, "field": "corrected_value", "before": None, "after": "12.3",
           "meta": {"list": [1, 2, {"x": "ж"}]}}]
    with clocked_db.uow() as u:
        a = u.actions.record(user, "edit", "x", ch)
    with clocked_db.uow() as u:
        assert u.actions.last_undoable(user.id).changes == ch and a.changes == ch
