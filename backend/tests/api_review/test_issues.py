"""ReviewService.resolve_issues / reopen_issue и эндпоинты замечаний (spec/04 §4.10, REQ-110).

Критерии: issues/resolve -> fixed, повтор -> 409 already_resolved, reopen возвращает факт в new.
Источник открытого замечания: правка КПД строки D001 до заведомо несходящегося значения (jv_consistency).
"""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data


@pytest.fixture
def jv_issue(env, svc, user):
    """(issue_id, pce_fact, другой факт строки в статусе new)."""
    row = env.jv_row()
    pce = env.find_fact("pce", row_id=row)
    svc.patch_value(user, pce.id, "5 %")
    ids = [i for i in env.open_issue_ids("jv_consistency") if row in i]
    assert ids, "правка КПД должна открыть jv_consistency"
    other = env.find_fact("voc", row_id=row)
    return ids[0], pce, other


def issue_of(env, issue_id):
    r = env.client.get(url(f"/papers/{env.pid()}/issues"))
    return next(i for i in r.json()["data"] if i["issue_id"] == issue_id)


class TestResolveService:
    def test_fixed(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        res = svc.resolve_issues(user, [iid], "fixed")
        assert res.resolved == [iid] and res.action is not None
        assert issue_of(env, iid)["status"] == "fixed"
        assert iid not in env.open_issue_ids()

    def test_ok_not_an_error(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        svc.resolve_issues(user, [iid], "ok")
        assert issue_of(env, iid)["status"] == "ok"

    def test_second_resolve_409_already_resolved(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        svc.resolve_issues(user, [iid], "fixed")
        with pytest.raises(AppError) as e:
            svc.resolve_issues(user, [iid], "fixed")
        assert e.value.code == "already_resolved" and e.value.http_status == 409

    def test_already_resolved_skipped_when_partial(self, env, svc, user, jv_issue):
        """Уже разобранные пропускаются; 409 только если разобраны все."""
        iid, _, _ = jv_issue
        other = [i for i in env.open_issue_ids() if i != iid]
        if not other:
            pytest.skip("нет второго открытого замечания в D001")
        svc.resolve_issues(user, [iid], "ok")
        res = svc.resolve_issues(user, [iid, other[0]], "ok")
        assert res.resolved == [other[0]]

    def test_accept_fact_id_accepts_new_fact(self, env, svc, user, jv_issue):
        iid, _, other = jv_issue
        svc.resolve_issues(user, [iid], "fixed", accept_fact_id=other.id)
        assert str(env.fact(other.id).review_status) == "accepted"

    def test_accept_fact_id_ignored_if_not_new(self, env, svc, user, jv_issue):
        iid, _, other = jv_issue
        svc.reject(user, other.id)
        svc.resolve_issues(user, [iid], "fixed", accept_fact_id=other.id)
        assert str(env.fact(other.id).review_status) == "rejected"

    def test_unknown_issue_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.resolve_issues(user, ["pap_x:jv_consistency:Z"], "fixed")
        assert e.value.http_status == 404

    def test_bad_how_400(self, svc, user, jv_issue):
        with pytest.raises(AppError) as e:
            svc.resolve_issues(user, [jv_issue[0]], "maybe")
        assert e.value.http_status == 400

    def test_empty_ids_400(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.resolve_issues(user, [], "ok")
        assert e.value.http_status == 400

    def test_merge_action_ids_single_journal_entry(self, env, svc, user, jv_issue):
        """merge_action_ids вливает ранее сделанное исправление: одна запись, откат целиком."""
        iid, pce, other = jv_issue
        edit = env.history(action="edit")[0]["action_id"]
        res = svc.resolve_issues(user, [iid], "fixed", accept_fact_id=other.id, merge_action_ids=[edit])
        svc.undo(user, res.action.id)
        assert env.fact(pce.id).corrected_value is None  # исправление откатилось вместе с решением
        assert str(env.fact(other.id).review_status) == "new"

    def test_resolve_hidden_from_history_but_undoable(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        res = svc.resolve_issues(user, [iid], "fixed")
        assert res.action.id not in [h["action_id"] for h in env.history()]
        svc.undo(user, res.action.id)
        assert iid in env.open_issue_ids()


class TestReopenService:
    def test_reopen_returns_to_open(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        svc.resolve_issues(user, [iid], "ok")
        res = svc.reopen_issue(user, iid)
        assert res.issue_id == iid and res.action is not None
        assert iid in env.open_issue_ids()

    def test_reopen_restores_touched_fact_to_new(self, env, svc, user, jv_issue):
        iid, _, other = jv_issue
        svc.resolve_issues(user, [iid], "fixed", accept_fact_id=other.id)
        assert issue_of(env, iid)["touched_fact_ids"] == [other.id]
        svc.reopen_issue(user, iid)
        assert str(env.fact(other.id).review_status) == "new"

    def test_reopen_open_issue_409(self, svc, user, jv_issue):
        with pytest.raises(AppError) as e:
            svc.reopen_issue(user, jv_issue[0])
        assert e.value.http_status == 409

    def test_reopen_unknown_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.reopen_issue(user, "pap_x:none:Z")
        assert e.value.code == "not_found"

    def test_reopen_undoable(self, env, svc, user, jv_issue):
        iid, _, _ = jv_issue
        svc.resolve_issues(user, [iid], "ok")
        res = svc.reopen_issue(user, iid)
        svc.undo(user, res.action.id)
        assert iid not in env.open_issue_ids()


class TestIssuesHttp:
    def test_resolve_200(self, env, client, jv_issue):
        iid, _, other = jv_issue
        r = client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "fixed",
                                                       "accept_fact_id": other.id})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["resolved"] == [iid] and j["accepted_fact_id"] == other.id
        assert j["action_id"] == r.headers["X-Action-Id"] and j["message"]
        assert "issues_changed" in j and "open_issues" in j["paper"]

    def test_resolve_repeat_409(self, client, jv_issue):
        b = {"issue_ids": [jv_issue[0]], "how": "ok"}
        assert client.post(url("/issues/resolve"), json=b).status_code == 200
        r = client.post(url("/issues/resolve"), json=b)
        assert r.status_code == 409 and err(r)["code"] == "already_resolved"

    def test_resolve_bad_body_400(self, client):
        assert client.post(url("/issues/resolve"), json={"how": "fixed"}).status_code == 400
        assert client.post(url("/issues/resolve"), json={"issue_ids": ["a"], "how": "zzz"}).status_code == 400

    def test_reopen_200(self, env, client, jv_issue):
        iid, _, other = jv_issue
        client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "fixed", "accept_fact_id": other.id})
        r = client.post(url(f"/issues/{iid}/reopen"))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["issue"]["status"] == "open" and j["restored_fact_ids"] == [other.id]
        assert j["action_id"] == r.headers["X-Action-Id"]
        assert str(env.fact(other.id).review_status) == "new"

    def test_reopen_404(self, client):
        assert client.post(url("/issues/pap_x:none:Z/reopen")).status_code == 404

    def test_paper_summary_open_issues_drop_after_resolve(self, env, client, jv_issue):
        n0 = env.paper().stats.open_issues
        r = client.post(url("/issues/resolve"), json={"issue_ids": [jv_issue[0]], "how": "ok"})
        assert r.json()["paper"]["open_issues"] == n0 - 1

    def test_get_issues_default_open_only(self, env, client, jv_issue):
        iid = jv_issue[0]
        client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"})
        ids = [i["issue_id"] for i in client.get(url("/issues"), params={"paper_id": env.pid()}).json()["data"]]
        assert iid not in ids
        ids = [i["issue_id"] for i in client.get(url("/issues"), params={"paper_id": env.pid(),
                                                                         "status": "all"}).json()["data"]]
        assert iid in ids

    def test_get_issues_filters(self, env, client, jv_issue):
        iid, pce, _ = jv_issue
        base = {"paper_id": env.pid()}
        by_check = client.get(url("/issues"), params={**base, "check_name": "jv_consistency"}).json()["data"]
        assert by_check and all(i["check_name"] == "jv_consistency" for i in by_check)
        assert iid in [i["issue_id"] for i in client.get(url("/issues"), params={**base, "fact_id": pce.id}).json()["data"]]
        sev = client.get(url("/issues"), params={**base, "severity": "warn"}).json()["data"]
        assert all(i["severity"] == "warn" for i in sev)
        assert client.get(url("/issues"), params={**base, "check_name": "no_such"}).json()["data"] == []

    def test_get_issues_shape(self, env, client, jv_issue):
        i = next(x for x in client.get(url("/issues"), params={"paper_id": env.pid()}).json()["data"]
                 if x["issue_id"] == jv_issue[0])
        for k in ("check_name", "plugin", "severity", "ui_level", "title", "text", "fact_ids", "main_fact_id",
                  "anchor_ids", "status", "computed_by", "rule_version", "stale", "touched_fact_ids", "paper_id"):
            assert k in i, k
        assert i["status"] == "open" and i["ui_level"] in ("conflict", "doubt", "warn")

    def test_get_issues_sort_severity_errors_first(self, env, client):
        data = client.get(url("/issues"), params={"paper_id": env.pid(), "status": "all"}).json()["data"]
        order = {"error": 0, "warn": 1, "info": 2}
        ranks = [order[i["severity"]] for i in data]
        assert ranks == sorted(ranks)

    def test_get_issues_pagination(self, env, client):
        p1 = client.get(url("/issues"), params={"paper_id": env.pid(), "status": "all", "limit": 2}).json()
        assert len(p1["data"]) <= 2
        if p1["next_cursor"]:
            p2 = client.get(url("/issues"), params={"paper_id": env.pid(), "status": "all", "limit": 2,
                                                    "cursor": p1["next_cursor"]}).json()
            assert not {i["issue_id"] for i in p1["data"]} & {i["issue_id"] for i in p2["data"]}

    def test_paper_issues_default_all(self, env, client, jv_issue):
        iid = jv_issue[0]
        client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"})
        r = client.get(url(f"/papers/{env.pid()}/issues"))
        assert r.status_code == 200 and iid in [i["issue_id"] for i in r.json()["data"]]  # разобранные внизу

    def test_paper_issues_404(self, client):
        assert client.get(url("/papers/pap_nope/issues")).status_code == 404

    def test_issues_collection_scope(self, client):
        r = client.get(url("/issues"), params={"collection": "solar"})
        assert r.status_code == 200 and "data" in r.json()

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        assert TestClient(env.app).get(url("/issues")).status_code == 401
