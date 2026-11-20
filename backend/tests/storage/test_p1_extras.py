"""Дополнительные методы storage сверх зафиксированных сигнатур (используются API и конвейером)."""
from __future__ import annotations

import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from faktura.domain.entities import FactFilter, PaperStats, ReviewStatus
from faktura.domain.errors import AppError
from faktura.domain.profile import BUILTIN_SOLAR_ALIAS, BUILTIN_SOLAR_ID
from faktura.domain.records import IssueStatus
from faktura.storage import normalize_key


def test_savepoint_rolls_back_only_inner_part(db, ws_id):
    with db.uow() as u:
        a = u.corpora.create(workspace_id=ws_id, name="A")
        with pytest.raises(RuntimeError):
            with u.savepoint():
                u.corpora.create(workspace_id=ws_id, name="B")
                raise RuntimeError
    with db.uow() as u:
        assert [c.id for c in u.corpora.list(ws_id)] == [a.id]


def test_parallel_threads_save_without_lock_errors(db, ws_id, corpus, new_paper, process):
    papers = [new_paper(f"D{i:03d}") for i in range(8)]
    errors: list[BaseException] = []

    def work(p):
        try:
            for _ in range(3):
                process(p, n=20)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(p,)) for p in papers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    with db.uow() as u:
        assert u.stats.overview(ws_id)["totals"]["facts"] == 8 * 20


def test_builtin_profile_by_id_and_alias(db):
    ws = "ws_test"
    with db.uow() as u:
        assert u.profiles.get(BUILTIN_SOLAR_ID).builtin is True
        assert u.profiles.get(BUILTIN_SOLAR_ALIAS).profile_id == BUILTIN_SOLAR_ID
        with pytest.raises(AppError):
            u.profiles.save(u.profiles.get(BUILTIN_SOLAR_ID), workspace_id=ws)
        p = u.profiles.get(BUILTIN_SOLAR_ID).model_copy(update={"profile_id": "prf_mine", "name": "Мой"})
        saved = u.profiles.save(p, workspace_id=ws)
        again = u.profiles.save(saved, workspace_id=ws)
        assert (saved.version, again.version) == (1, 2) and u.profiles.get("prf_mine").version == 2


def test_issue_set_status_updates_counters(db, ws_id, corpus, new_paper, process, make_issue):
    p = new_paper()
    process(p, n=3, issues=[make_issue("D001", "I-a", records=("R001",))])
    with db.uow() as u:
        (iss,) = u.issues.list(p.id)
        u.issues.set_status(iss.id, IssueStatus.fixed, by="usr_1")
        assert u.papers.get(p.id).stats.open_issues == 0
        assert u.stats.overview(ws_id)["totals"]["open_issues"] == 0
        assert u.facts.query(FactFilter(workspace_id=ws_id, has_open_issues=True), [], 10, None).data == []
        u.issues.set_status(iss.id, "open")
        assert u.stats.overview(ws_id)["totals"]["open_issues"] == 1


def test_apply_review_main_fact_wins(db, new_paper, process, make_bundle, make_drafts):
    p = new_paper()
    b = make_bundle("D001", n=1)
    drafts = make_drafts(b)
    extra = drafts[0].model_copy(update={"ref": "R001.raw", "target": {"entity": "results", "id": "R001",
                                                                        "field": "raw_value"}})
    with db.uow() as u:
        res = u.papers.save_processed(p.id, b, [drafts[0], extra], [], [], stats=PaperStats(), model_version="m",
                                      versions={})
        u.facts.update_review(res.fact_ids["R001"], review_status="accepted", corrected_value=Decimal("5"))
        u.facts.update_review(res.fact_ids["R001.raw"], review_status="rejected")
        meta = u.papers.load_bundle(p.id).results[0].meta
    assert meta.review_status == ReviewStatus.accepted and meta.corrected_value == Decimal("5")


def test_sessions_revoke_chain(db):
    from datetime import datetime, timezone
    exp = datetime.now(timezone.utc) + timedelta(days=1)
    with db.uow() as u:
        usr = u.users.create(workspace_id="ws_test", email="s@example.com")
        s1 = u.sessions.create(usr.id, "h1", exp)
        s2 = u.sessions.create(usr.id, "h2", exp, parent_id=s1.id)
        s3 = u.sessions.create(usr.id, "h3", exp, parent_id=s2.id)
        other = u.sessions.create(usr.id, "h4", exp)
        assert u.sessions.revoke_chain(s2.id) == 3
        assert u.sessions.get(s3.id).revoked_at is not None and u.sessions.get(other.id).revoked_at is None


def test_exports_and_synonyms(db, ws_id):
    with db.uow() as u:
        e = u.exports.create(workspace_id=ws_id, user_id="usr_1", format="submission", scope={"corpus_id": "c"})
        done = u.exports.update(e.id, status="done", file_key="exports/x.zip")
        assert done.status == "done" and u.exports.list(ws_id)[0].id == e.id
        s = u.synonyms.add(ws_id, "solar", "voc", "Uхх")
        with pytest.raises(AppError):
            u.synonyms.add(ws_id, "solar", "voc", "uХХ")
        assert [x.id for x in u.synonyms.list(ws_id, "solar")] == [s.id]


def test_purge_expired_corpora(clocked_db, clock, ws_id, make_bundle, make_drafts):
    with clocked_db.uow() as u:
        c = u.corpora.create(workspace_id=ws_id, name="K")
        p = u.papers.create(workspace_id=ws_id, corpus_id=c.id, publication_id="D001")
        b = make_bundle("D001", n=2)
        u.papers.save_processed(p.id, b, make_drafts(b), [], [], stats=PaperStats(), model_version="m", versions={})
        u.corpora.soft_delete(c.id)
    clock.advance(days=31)
    with clocked_db.uow() as u:
        assert u.corpora.purge_expired() == 1
        assert u.corpora.get(c.id) is None and u.papers.get(p.id) is None


@pytest.mark.parametrize("key,expected", [("a/./b/../c.txt", "a/c.txt"), ("a\\b", "a/b"), ("x//y/", "x/y")])
def test_normalize_key(key, expected):
    assert normalize_key(key) == expected
