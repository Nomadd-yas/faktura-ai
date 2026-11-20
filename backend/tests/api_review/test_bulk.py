"""ReviewService.bulk и POST /facts/bulk (spec/04 §4.8, REQ-107): одна запись журнала, count, лимит 5000."""
from __future__ import annotations

import pytest

from faktura.domain.entities import FactFilter, ReviewStatus
from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data


class TestBulkService:
    def test_accept_by_ids_one_journal_entry(self, env, svc, user):
        ids = [f.id for f in env.facts()[:5]]
        res = svc.bulk(user, "accept", fact_ids=ids)
        assert res.changed == 5 and res.skipped == 0
        assert res.action is not None and res.action.action == "acceptAll"
        assert all(str(env.fact(i).review_status) == "accepted" for i in ids)
        acc = [h for h in env.history() if h["action"] == "acceptAll"]
        assert len(acc) == 1 and acc[0]["count"] == 5

    def test_accept_by_filter_all_new(self, env, svc, user):
        n_new = len([f for f in env.facts() if str(f.review_status) == "new"])
        res = svc.bulk(user, "accept", filter=FactFilter(paper_ids=[env.pid()], review_status=[ReviewStatus.new]))
        assert res.changed == n_new and n_new > 0
        assert not [f for f in env.facts() if str(f.review_status) == "new"]
        assert len([h for h in env.history() if h["action"] in ("accept", "acceptAll")]) == 1

    def test_only_changing_facts_counted(self, env, svc, user):
        """Принимаются только new: уже принятые пропускаются (skipped)."""
        facts = env.facts()[:4]
        svc.accept(user, facts[0].id)
        svc.reject(user, facts[1].id)
        res = svc.bulk(user, "accept", fact_ids=[f.id for f in facts])
        assert res.changed == 2 and res.skipped == 2  # принимаются только new (spec/04 §4.8)

    def test_reject_bulk(self, env, svc, user):
        ids = [f.id for f in env.facts()[:3]]
        res = svc.bulk(user, "reject", fact_ids=ids)
        assert res.changed == 3 and res.action.action in ("reject", "rejectAll")
        assert all(str(env.fact(i).review_status) == "rejected" for i in ids)

    def test_restore_bulk(self, env, svc, user):
        ids = [f.id for f in env.facts()[:3]]
        svc.bulk(user, "accept", fact_ids=ids)
        res = svc.bulk(user, "restore", fact_ids=ids)
        assert res.changed == 3
        assert all(str(env.fact(i).review_status) == "new" for i in ids)

    def test_bulk_issues_recheck_once(self, env, svc, user, monkeypatch):
        import faktura.pipeline.review as rv

        calls = []
        orig = rv.recheck_paper

        def spy(*a, **k):
            calls.append(1)
            return orig(*a, **k)

        monkeypatch.setattr(rv, "recheck_paper", spy)
        monkeypatch.setattr("faktura.pipeline.recheck_paper", spy)
        svc.bulk(user, "accept", fact_ids=[f.id for f in env.facts()[:10]])
        assert len(calls) == 1  # один пересчёт на статью, не на факт

    def test_paper_summary_updated(self, env, svc, user):
        svc.bulk(user, "accept", filter=FactFilter(paper_ids=[env.pid()], review_status=[ReviewStatus.new]))
        assert env.paper().stats.quality is not None

    def test_neither_ids_nor_filter_400(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.bulk(user, "accept")
        assert e.value.http_status == 400 and e.value.code == "validation_error"

    def test_unknown_action_400(self, env, svc, user):
        with pytest.raises(AppError) as e:
            svc.bulk(user, "delete", fact_ids=[env.facts()[0].id])
        assert e.value.http_status == 400

    def test_empty_ids_zero_changed_or_400(self, svc, user):
        try:
            res = svc.bulk(user, "accept", fact_ids=[])
        except AppError as e:
            assert e.http_status == 400
        else:
            assert res.changed == 0 and res.action is None

    def test_more_than_5000_400(self, svc, user):
        ids = [f"fct_{i}" for i in range(5001)]
        with pytest.raises(AppError) as e:
            svc.bulk(user, "accept", fact_ids=ids)
        assert e.value.http_status == 400

    def test_exactly_5000_not_rejected_by_limit(self, svc, user):
        """Граница: 5000 допустимо (неизвестные id просто пропускаются или 404, но не 400 из-за лимита)."""
        ids = [f"fct_{i}" for i in range(5000)]
        try:
            res = svc.bulk(user, "accept", fact_ids=ids)
        except AppError as e:
            assert e.http_status != 400 or "5000" not in e.message
        else:
            assert res.changed == 0

    def test_filter_over_5000_400(self, env, svc, user, monkeypatch):
        """Фильтр, подходящий под > 5000 фактов, - 400 (проверяется на уровне repo.query)."""
        f0 = env.facts()[0]
        big = [f0.model_copy(update={"id": f"fct_big_{i}"}) for i in range(5001)]
        import faktura.storage.repos as repos
        from faktura.domain.entities import FactRow, Page

        monkeypatch.setattr(repos.FactsRepo, "query",
                            lambda self, *a, **k: Page(data=[FactRow(fact=x) for x in big], total=5001))
        with pytest.raises(AppError) as e:
            svc.bulk(user, "accept", filter=FactFilter(paper_ids=[env.pid()]))
        assert e.value.http_status == 400

    def test_multi_paper_filter_one_entry(self, env, svc, user):
        res = svc.bulk(user, "accept", filter=FactFilter(paper_ids=[env.pid()]))
        assert len(env.history(action="accept")) == 1 and res.action is not None

    def test_undo_whole_group(self, env, svc, user):
        ids = [f.id for f in env.facts()[:6]]
        res = svc.bulk(user, "accept", fact_ids=ids)
        svc.undo(user, res.action.id)
        assert all(str(env.fact(i).review_status) == "new" for i in ids)


class TestBulkHttp:
    def test_ids_200(self, env, client):
        ids = [f.id for f in env.facts()[:4]]
        r = client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": ids})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["count"] == 4 and sorted(j["fact_ids"]) == sorted(ids)
        assert j["action_id"] == r.headers["X-Action-Id"]

    def test_filter_form_accept_all_in_paper(self, env, client):
        r = client.post(url("/facts/bulk"), json={"action": "accept",
                                                   "filter": {"paper_id": env.pid(), "review_status": "new"}})
        assert r.status_code == 200 and r.json()["count"] > 0
        h = [x for x in env.history() if x["action"] == "acceptAll"]
        assert len(h) == 1 and h[0]["count"] == r.json()["count"]

    def test_filter_reliable(self, env, client):
        r = client.post(url("/facts/bulk"), json={"action": "accept",
                                                   "filter": {"paper_id": env.pid(), "review_status": "new",
                                                              "reliable": True}})
        assert r.status_code == 200
        accepted = [f for f in env.facts() if str(f.review_status) == "accepted"]
        assert len(accepted) == r.json()["count"]
        assert all((f.confidence or 0) >= 0.9 for f in accepted)

    def test_neither_400(self, client):
        r = client.post(url("/facts/bulk"), json={"action": "accept"})
        assert r.status_code == 400 and err(r)["code"] == "validation_error"

    def test_bad_action_400(self, env, client):
        assert client.post(url("/facts/bulk"), json={"action": "burn", "fact_ids": ["x"]}).status_code == 400

    def test_over_5000_400(self, client):
        r = client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": [f"f{i}" for i in range(5001)]})
        assert r.status_code == 400

    def test_response_has_issues_changed_and_papers(self, env, client):
        r = client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": [env.facts()[0].id]})
        assert "issues_changed" in r.json()

    def test_zero_changes_no_journal_entry(self, env, client):
        ids = [f.id for f in env.facts()[:2]]
        client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": ids})
        n = len(env.history())
        r = client.post(url("/facts/bulk"), json={"action": "accept", "fact_ids": ids})
        assert r.status_code == 200 and r.json()["count"] == 0 and len(env.history()) == n
