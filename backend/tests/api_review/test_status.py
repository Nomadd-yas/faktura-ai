"""ReviewService.accept/reject/restore и POST /facts/{id}/accept|reject|restore (spec/04 §4.8, REQ-107).

Критерий приёмки: accept -> 200, X-Action-Id, issues_changed, обновлённые paper.quality/open_issues/top_level.
"""
from __future__ import annotations

import pytest

from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data


# --- сервис -------------------------------------------------------------------------------------

class TestServiceAccept:
    def test_accept_new_fact(self, env, svc, user, new_fact):
        res = svc.accept(user, new_fact.id)
        assert str(res.fact.review_status) == "accepted"
        assert res.fact.id == new_fact.id
        assert res.fact.reviewed_by == user.id and res.fact.reviewed_at is not None
        assert res.action is not None and res.action.action == "accept" and res.action.fact_id == new_fact.id
        assert res.action.user_id == user.id and res.action.paper_id == new_fact.paper_id
        assert str(env.fact(new_fact.id).review_status) == "accepted"  # сохранено в БД

    def test_accept_returns_paper_summary(self, env, svc, user, new_fact):
        res = svc.accept(user, new_fact.id)
        assert res.paper is not None and res.paper.id == new_fact.paper_id
        saved = env.paper()
        # сводка в ответе совпадает с сохранённой (stats.update_paper в той же транзакции)
        assert res.paper.stats.open_issues == saved.stats.open_issues
        assert res.paper.stats.top_level == saved.stats.top_level
        assert res.paper.stats.quality == saved.stats.quality

    def test_accept_changes_quality(self, env, svc, user, new_fact):
        q0 = env.paper().stats.quality
        svc.accept(user, new_fact.id)
        assert env.paper().stats.quality != q0  # принятый факт входит в расчёт quality (spec/05 §5.5)

    def test_accept_already_accepted_is_noop(self, env, svc, user, new_fact):
        svc.accept(user, new_fact.id)
        res = svc.accept(user, new_fact.id)  # повторно: без ошибки, статус остаётся
        assert str(res.fact.review_status) == "accepted"

    def test_accept_rejected_fact_makes_accepted(self, env, svc, user, new_fact):
        svc.reject(user, new_fact.id)
        assert str(svc.accept(user, new_fact.id).fact.review_status) == "accepted"

    def test_accept_issues_changed_is_diff(self, env, svc, user, new_fact):
        d = svc.accept(user, new_fact.id).issues_changed
        assert isinstance(d.opened, list) and isinstance(d.closed, list)
        assert not set(d.opened) & set(d.closed)

    def test_accept_unknown_fact_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.accept(user, "fct_does_not_exist")
        assert e.value.code == "not_found" and e.value.http_status == 404

    def test_accept_group_id_merges_into_one_history_entry(self, env, svc, user):
        a, b = env.facts()[:2]
        r1 = svc.accept(user, a.id)
        r2 = svc.accept(user, b.id, group_id=r1.action.id)
        assert r2.action.group_id == r1.action.id or r2.action.id == r1.action.id
        acc = [h for h in env.history() if h["action"] in ("accept", "acceptAll")]
        assert len(acc) == 1  # объединено в одну запись журнала

    def test_accept_not_ready_paper_422(self, env, svc, user, new_fact):
        from faktura.domain.entities import PaperStatus

        with env.uow() as uow:
            uow.papers.update(new_fact.paper_id, status=PaperStatus.processing)
        with pytest.raises(AppError) as e:
            svc.accept(user, new_fact.id)
        assert e.value.code == "paper_not_ready" and e.value.http_status == 422


class TestServiceReject:
    def test_reject_new_fact(self, env, svc, user, new_fact):
        res = svc.reject(user, new_fact.id)
        assert str(res.fact.review_status) == "rejected"
        assert res.action.action == "reject"

    def test_reject_removes_fact_from_checks(self, env, svc, user):
        """Отклонённый факт не участвует в проверках: jv_consistency по строке закрывается."""
        row = env.jv_row()
        pce = env.find_fact("pce", row_id=row)
        svc.patch_value(user, pce.id, "99 %")  # заведомо не сходится
        assert env.open_issue_ids("jv_consistency")
        res = svc.reject(user, pce.id)
        assert res.issues_changed.closed or not any(row in i for i in env.open_issue_ids("jv_consistency"))

    def test_reject_unknown_fact_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.reject(user, "fct_nope")
        assert e.value.code == "not_found"

    def test_reject_keeps_fact_row(self, env, svc, user, new_fact):
        svc.reject(user, new_fact.id)
        assert str(env.fact(new_fact.id).review_status) == "rejected"  # мягко: факт остаётся


class TestServiceRestore:
    def test_restore_resets_to_new(self, env, svc, user, new_fact):
        svc.accept(user, new_fact.id)
        res = svc.restore(user, new_fact.id)
        assert str(res.fact.review_status) == "new"
        assert res.action.action == "restore"

    def test_restore_resets_correction(self, env, svc, user, new_fact):
        svc.patch_value(user, new_fact.id, "1100 мВ")
        res = svc.restore(user, new_fact.id)
        assert res.fact.corrected_value is None and res.fact.corrected_raw is None
        assert res.fact.extraction_status == new_fact.extraction_status

    def test_restore_new_fact_noop_no_error(self, svc, user, new_fact):
        assert str(svc.restore(user, new_fact.id).fact.review_status) == "new"

    def test_restore_rejected(self, svc, user, new_fact):
        svc.reject(user, new_fact.id)
        assert str(svc.restore(user, new_fact.id).fact.review_status) == "new"

    def test_restore_unknown_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.restore(user, "fct_nope")
        assert e.value.http_status == 404


class TestServiceTransaction:
    def test_failure_in_recheck_rolls_back_everything(self, env, svc, user, new_fact, monkeypatch):
        """Одна транзакция: storage + recheck + stats + журнал. Сбой пересчёта -> ничего не сохранено."""
        def boom(*a, **k):
            raise RuntimeError("recheck failed")

        monkeypatch.setattr("faktura.pipeline.recheck_paper", boom)
        monkeypatch.setattr("faktura.pipeline.review.recheck_paper", boom)
        n_before = len(env.history())
        with pytest.raises(Exception):
            svc.accept(user, new_fact.id)
        assert str(env.fact(new_fact.id).review_status) == "new"
        assert len(env.history()) == n_before

    def test_recheck_called_once_per_edit(self, env, svc, user, new_fact, monkeypatch):
        import faktura.pipeline.review as rv

        calls = []
        orig = rv.recheck_paper

        def spy(*a, **k):
            calls.append(1)
            return orig(*a, **k)

        monkeypatch.setattr(rv, "recheck_paper", spy)
        monkeypatch.setattr("faktura.pipeline.recheck_paper", spy)
        svc.accept(user, new_fact.id)
        assert len(calls) == 1


# --- HTTP ---------------------------------------------------------------------------------------

class TestHttpStatus:
    @pytest.mark.parametrize("verb, status", [("accept", "accepted"), ("reject", "rejected")])
    def test_verb_200_shape(self, env, client, new_fact, verb, status):
        r = client.post(url(f"/facts/{new_fact.id}/{verb}"))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["fact"]["review_status"] == status
        assert body["fact"]["fact_id"] == new_fact.id
        assert body["action_id"].startswith("act_")
        assert r.headers["X-Action-Id"] == body["action_id"]
        assert set(body["issues_changed"]) >= {"opened", "closed"}
        assert {"quality", "open_issues", "top_level"} <= set(body["paper"])

    def test_restore_200(self, env, client, new_fact):
        client.post(url(f"/facts/{new_fact.id}/accept"))
        r = client.post(url(f"/facts/{new_fact.id}/restore"))
        assert r.status_code == 200 and r.json()["fact"]["review_status"] == "new"

    def test_paper_summary_matches_saved(self, env, client, new_fact):
        body = client.post(url(f"/facts/{new_fact.id}/accept")).json()
        assert body["paper"]["open_issues"] == env.paper().stats.open_issues

    def test_body_with_group_id(self, env, client, new_fact):
        r = client.post(url(f"/facts/{new_fact.id}/accept"), json={"group_id": "act_missing"})
        assert r.status_code in (200, 400, 404)  # неизвестная группа не должна давать 500

    def test_404_error_shape(self, client):
        r = client.post(url("/facts/fct_nope/accept"))
        assert r.status_code == 404 and err(r)["code"] == "not_found"

    def test_requires_auth(self, env, new_fact):
        from fastapi.testclient import TestClient

        assert TestClient(env.app).post(url(f"/facts/{new_fact.id}/accept")).status_code == 401

    def test_accept_then_undo_restores_state(self, env, client, new_fact):
        r = client.post(url(f"/facts/{new_fact.id}/accept"))
        assert client.post(url(f"/actions/{r.json()['action_id']}/undo")).status_code == 200
        assert str(env.fact(new_fact.id).review_status) == "new"
