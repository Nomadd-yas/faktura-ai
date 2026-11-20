"""ReviewService.undo, GET /history, POST /history/undo, POST /actions/{id}/undo (spec/04 §4.15, REQ-107).

Критерии: undo возвращает состояние; повторный undo -> 409 already_undone; отмена после чужой правки -> 409 conflict;
/history не показывает служебные действия, но они отменяются. Глубина 50 действий за 24 ч.
"""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data


class TestUndoService:
    def test_undo_accept_restores_state(self, env, svc, user, new_fact):
        a = svc.accept(user, new_fact.id).action
        res = svc.undo(user, a.id)
        assert res.undone is not None and res.undone.id == a.id
        assert new_fact.id in res.changed
        f = env.fact(new_fact.id)
        assert str(f.review_status) == "new"

    def test_undo_edit_restores_value_and_status(self, env, svc, user, new_fact):
        a = svc.patch_value(user, new_fact.id, "1100 мВ").action
        svc.undo(user, a.id)
        f = env.fact(new_fact.id)
        assert f.corrected_value is None and f.corrected_raw is None
        assert str(f.review_status) == "new" and f.extraction_status == new_fact.extraction_status

    def test_undo_recomputes_issues(self, env, svc, user):
        row = env.jv_row()
        pce = env.find_fact("pce", row_id=row)
        a = svc.patch_value(user, pce.id, "5 %").action
        assert env.open_issue_ids("jv_consistency")
        svc.undo(user, a.id)
        assert not any(row in i for i in env.open_issue_ids("jv_consistency"))

    def test_undo_updates_paper_stats(self, env, svc, user):
        pce = env.find_fact("pce", row_id=env.jv_row())
        n0 = env.paper().stats.open_issues
        a = svc.patch_value(user, pce.id, "5 %").action
        assert env.paper().stats.open_issues > n0
        svc.undo(user, a.id)
        assert env.paper().stats.open_issues == n0

    def test_second_undo_409_already_undone(self, svc, user, new_fact):
        a = svc.accept(user, new_fact.id).action
        svc.undo(user, a.id)
        with pytest.raises(AppError) as e:
            svc.undo(user, a.id)
        assert e.value.code == "already_undone" and e.value.http_status == 409

    def test_conflict_after_foreign_change(self, env, svc, user, new_fact):
        """Другой пользователь изменил тот же факт после действия -> отмена невозможна."""
        _, other = env.make_user()
        a = svc.accept(user, new_fact.id).action
        svc.reject(other, new_fact.id)
        with pytest.raises(AppError) as e:
            svc.undo(user, a.id)
        assert e.value.code == "conflict" and e.value.http_status == 409
        assert str(env.fact(new_fact.id).review_status) == "rejected"  # ничего не откатилось

    def test_conflict_after_own_later_change(self, svc, user, new_fact):
        a = svc.accept(user, new_fact.id).action
        svc.patch_value(user, new_fact.id, "1 В")
        with pytest.raises(AppError) as e:
            svc.undo(user, a.id)
        assert e.value.code == "conflict"

    def test_undo_in_reverse_order_works(self, env, svc, user, new_fact):
        a = svc.accept(user, new_fact.id).action
        b = svc.patch_value(user, new_fact.id, "1 В").action
        svc.undo(user, b.id)
        svc.undo(user, a.id)
        assert str(env.fact(new_fact.id).review_status) == "new"

    def test_undo_without_id_takes_last_own(self, env, svc, user):
        f1, f2 = env.facts()[:2]
        svc.accept(user, f1.id)
        svc.accept(user, f2.id)
        res = svc.undo(user)
        assert res.undone.fact_id == f2.id
        assert str(env.fact(f1.id).review_status) == "accepted"

    def test_undo_last_skips_already_undone(self, env, svc, user):
        f1, f2 = env.facts()[:2]
        svc.accept(user, f1.id)
        svc.accept(user, f2.id)
        svc.undo(user)
        assert svc.undo(user).undone.fact_id == f1.id

    def test_undo_last_ignores_other_users_actions(self, env, svc, user):
        _, other = env.make_user()
        f1, f2 = env.facts()[:2]
        svc.accept(user, f1.id)
        svc.accept(other, f2.id)
        assert svc.undo(user).undone.fact_id == f1.id
        assert str(env.fact(f2.id).review_status) == "accepted"

    def test_undo_nothing_returns_none(self, svc, user):
        res = svc.undo(user)
        assert res.undone is None and res.changed == []

    def test_undo_foreign_action_by_id_403_or_404(self, env, svc, user, new_fact):
        _, other = env.make_user()
        a = svc.accept(other, new_fact.id).action
        with pytest.raises(AppError) as e:
            svc.undo(user, a.id)
        assert e.value.http_status in (403, 404)

    def test_undo_unknown_action_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.undo(user, "act_nope")
        assert e.value.code == "not_found"

    def test_group_undone_entirely(self, env, svc, user):
        f1, f2 = env.facts()[:2]
        a1 = svc.accept(user, f1.id).action
        svc.accept(user, f2.id, group_id=a1.id)
        svc.undo(user, a1.id)
        assert str(env.fact(f1.id).review_status) == "new" and str(env.fact(f2.id).review_status) == "new"

    def test_undo_is_journaled_and_marks_original(self, env, svc, user, new_fact):
        a = svc.accept(user, new_fact.id).action
        svc.undo(user, a.id)
        orig = next(h for h in env.history() if h["action_id"] == a.id)
        assert orig["undone"] is True

    def test_undo_depth_50(self, env, svc, user):
        """Отменяемы только 50 последних действий пользователя: 51-е с конца отменить нельзя."""
        facts = env.facts()
        first = svc.accept(user, facts[0].id).action
        with env.uow() as uow:
            for i in range(50):
                uow.actions.record(user, "note", f"служебное {i}", [], paper_id=env.pid())
        with pytest.raises(AppError) as e:
            svc.undo(user, first.id)
        assert e.value.http_status in (404, 409)

    def test_undo_24h_window_in_repo(self, env, svc, user, new_fact):
        svc.accept(user, new_fact.id)
        with env.uow() as uow:
            assert uow.actions.last_undoable(user.id, within_h=24) is not None
            assert uow.actions.last_undoable(user.id, within_h=0) is None

    def test_undo_bulk_restores_all(self, env, svc, user):
        ids = [f.id for f in env.facts()[:7]]
        a = svc.bulk(user, "accept", fact_ids=ids).action
        res = svc.undo(user, a.id)
        assert set(ids) <= set(res.changed)

    def test_undo_manual_add_removes_fact(self, env, svc, user):
        row = env.jv_row()
        res = svc.add_manual(user, env.pid(), {"row_id": row, "param": "voc", "raw": "1 В", "quote": "q",
                                               "selection": {"page": 2}})
        svc.undo(user, res.action.id)
        assert res.fact.id not in [f.id for f in env.facts()]


class TestHistoryHttp:
    def test_entries_shape(self, env, client, new_fact):
        client.patch(url(f"/facts/{new_fact.id}"), json={"raw": "1100 мВ"})
        h = client.get(url("/history")).json()
        e = h["data"][0]
        for k in ("action_id", "at", "who", "user_id", "action", "paper_id", "paper_title", "fact_id",
                  "param_label", "row_label", "from", "to", "count", "undone"):
            assert k in e, k
        assert e["action"] == "edit" and e["fact_id"] == new_fact.id and e["user_id"] == env.admin.id
        assert "next_cursor" in h

    def test_sorted_newest_first(self, env, client):
        f1, f2 = env.facts()[:2]
        a1 = client.post(url(f"/facts/{f1.id}/accept")).json()["action_id"]
        a2 = client.post(url(f"/facts/{f2.id}/reject")).json()["action_id"]
        ids = [h["action_id"] for h in client.get(url("/history")).json()["data"]]
        assert ids.index(a2) < ids.index(a1)

    def test_accept_filter_includes_accept_all(self, env, client):
        f = env.facts()
        client.post(url(f"/facts/{f[0].id}/accept"))
        client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": [f[1].id, f[2].id]})
        client.post(url(f"/facts/{f[3].id}/reject"))
        kinds = {h["action"] for h in env.history(action="accept")}
        assert kinds == {"accept", "acceptAll"}

    @pytest.mark.parametrize("action", ["accept", "reject", "restore", "edit", "add", "acceptAll"])
    def test_action_filter_accepts_all_kinds(self, client, action):
        assert client.get(url("/history"), params={"action": action}).status_code == 200

    def test_bad_action_400(self, client):
        assert client.get(url("/history"), params={"action": "zzz"}).status_code == 400

    def test_paper_filter(self, env, client, new_fact):
        client.post(url(f"/facts/{new_fact.id}/accept"))
        assert env.history(paper_id=env.pid())
        assert env.history(paper_id="pap_other") == []

    def test_user_me_filter(self, env, client, new_fact):
        c2, u2 = env.make_user()
        client.post(url(f"/facts/{new_fact.id}/accept"))
        other = env.facts()[5]
        c2.post(url(f"/facts/{other.id}/accept"))
        mine = client.get(url("/history"), params={"user_id": "me"}).json()["data"]
        assert mine and all(h["user_id"] == env.admin.id for h in mine)
        assert {h["user_id"] for h in env.history()} == {env.admin.id, u2.id}

    def test_pagination(self, env, client):
        for f in env.facts()[:5]:
            client.post(url(f"/facts/{f.id}/accept"))
        p1 = client.get(url("/history"), params={"limit": 2}).json()
        assert len(p1["data"]) == 2 and p1["next_cursor"]
        p2 = client.get(url("/history"), params={"limit": 2, "cursor": p1["next_cursor"]}).json()
        assert not {h["action_id"] for h in p1["data"]} & {h["action_id"] for h in p2["data"]}

    def test_default_limit_100(self, env, client):
        assert len(client.get(url("/history")).json()["data"]) <= 100

    def test_from_to_filters(self, env, client, new_fact):
        client.post(url(f"/facts/{new_fact.id}/accept"))
        assert client.get(url("/history"), params={"from": "2999-01-01T00:00:00Z"}).json()["data"] == []
        assert client.get(url("/history"), params={"to": "2000-01-01T00:00:00Z"}).json()["data"] == []
        assert client.get(url("/history"), params={"from": "2000-01-01T00:00:00Z"}).json()["data"]

    def test_service_actions_hidden(self, env, client):
        """issue_resolve и прочие служебные действия в журнале не показываются."""
        f = env.find_fact("pce", row_id=env.jv_row())
        client.patch(url(f"/facts/{f.id}"), json={"raw": "5 %"})
        iid = env.open_issue_ids("jv_consistency")[0]
        aid = client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"}).json()["action_id"]
        hist = client.get(url("/history")).json()["data"]
        assert aid not in [h["action_id"] for h in hist]
        assert {h["action"] for h in hist} <= {"accept", "reject", "restore", "edit", "add", "acceptAll"}

    def test_service_action_still_undoable(self, env, client):
        f = env.find_fact("pce", row_id=env.jv_row())
        client.patch(url(f"/facts/{f.id}"), json={"raw": "5 %"})
        iid = env.open_issue_ids("jv_consistency")[0]
        aid = client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"}).json()["action_id"]
        assert client.post(url(f"/actions/{aid}/undo")).status_code == 200
        assert iid in env.open_issue_ids()

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        assert TestClient(env.app).get(url("/history")).status_code == 401


class TestUndoHttp:
    def test_history_undo_last(self, env, client, new_fact):
        r = client.patch(url(f"/facts/{new_fact.id}"), json={"raw": "1100 мВ"})
        u = client.post(url("/history/undo"))
        assert u.status_code == 200, u.text
        j = u.json()
        assert j["undone_action_id"] == r.json()["action_id"] and j["fact_id"] == new_fact.id
        assert j["label"] and "Отменено" in j["message"]
        assert env.fact(new_fact.id).corrected_value is None

    def test_history_undo_nothing(self, client):
        u = client.post(url("/history/undo"))
        assert u.status_code == 200
        assert u.json()["undone_action_id"] is None and u.json()["message"] == "Отменять нечего"

    def test_history_undo_includes_service_action(self, env, client):
        f = env.find_fact("pce", row_id=env.jv_row())
        client.patch(url(f"/facts/{f.id}"), json={"raw": "5 %"})
        iid = env.open_issue_ids("jv_consistency")[0]
        aid = client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"}).json()["action_id"]
        assert client.post(url("/history/undo")).json()["undone_action_id"] == aid  # «любого типа, в т. ч. служебное»

    def test_history_undo_only_own(self, env, client):
        c2, _ = env.make_user()
        f = env.facts()[0]
        c2.post(url(f"/facts/{f.id}/accept"))
        assert client.post(url("/history/undo")).json()["undone_action_id"] is None

    def test_action_undo_200_shape(self, env, client, new_fact):
        a = client.post(url(f"/facts/{new_fact.id}/accept")).json()["action_id"]
        u = client.post(url(f"/actions/{a}/undo"))
        assert u.status_code == 200
        assert {"undone_action_id", "label", "message", "fact_id", "issues_changed"} <= set(u.json())

    def test_action_undo_twice_409(self, client, new_fact):
        a = client.post(url(f"/facts/{new_fact.id}/accept")).json()["action_id"]
        client.post(url(f"/actions/{a}/undo"))
        r = client.post(url(f"/actions/{a}/undo"))
        assert r.status_code == 409 and err(r)["code"] == "already_undone"

    def test_action_undo_after_foreign_change_409(self, env, client, new_fact):
        c2, _ = env.make_user()
        a = client.post(url(f"/facts/{new_fact.id}/accept")).json()["action_id"]
        c2.post(url(f"/facts/{new_fact.id}/reject"))
        r = client.post(url(f"/actions/{a}/undo"))
        assert r.status_code == 409 and err(r)["code"] == "conflict"

    def test_action_undo_404(self, client):
        r = client.post(url("/actions/act_nope/undo"))
        assert r.status_code == 404 and err(r)["code"] == "not_found"

    def test_undo_returns_issues_changed_closed(self, env, client):
        f = env.find_fact("pce", row_id=env.jv_row())
        a = client.patch(url(f"/facts/{f.id}"), json={"raw": "5 %"}).json()["action_id"]
        u = client.post(url(f"/actions/{a}/undo")).json()
        assert any("jv_consistency" in i for i in u["issues_changed"]["closed"])

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        c = TestClient(env.app)
        assert c.post(url("/history/undo")).status_code == 401
        assert c.post(url("/actions/act_x/undo")).status_code == 401
