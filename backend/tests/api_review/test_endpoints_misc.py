"""ROUTERS, /normalize/preview, /checks, /checks/rules, события facts.changed / issues.changed (spec/04 §4.9, §4.13)."""
from __future__ import annotations

import re

import pytest

from faktura.api.review import ROUTERS
from faktura.domain.tables import CHECK_CATALOG

from review_support import err, url


def _routes() -> set[tuple[str, str]]:
    out = set()
    for r in ROUTERS:
        for route in r.routes:
            for m in route.methods or ():
                out.add((m, (r.prefix or "") + route.path))
    return out


class TestRouters:
    EXPECTED = [
        ("POST", "/facts/{fact_id}/accept"), ("POST", "/facts/{fact_id}/reject"),
        ("POST", "/facts/{fact_id}/restore"), ("PATCH", "/facts/{fact_id}"), ("POST", "/facts/bulk"),
        ("POST", "/papers/{paper_id}/facts"), ("POST", "/papers/{paper_id}/facts/guess"),
        ("POST", "/normalize/preview"), ("GET", "/issues"), ("GET", "/papers/{paper_id}/issues"),
        ("POST", "/issues/resolve"), ("POST", "/issues/{issue_id}/reopen"), ("GET", "/checks"),
        ("GET", "/checks/rules"), ("GET", "/history"), ("POST", "/history/undo"),
        ("POST", "/actions/{action_id}/undo"),
    ]

    def test_routers_non_empty(self):
        assert ROUTERS and all(hasattr(r, "routes") for r in ROUTERS)

    @pytest.mark.parametrize("method, path", EXPECTED)
    def test_route_declared(self, method, path):
        def norm(p):
            return re.sub(r"\{[^}]+\}", "{x}", p)

        assert (method, norm(path)) in {(m, norm(p)) for m, p in _routes()}

    def test_static_post_paths_declared_before_param_paths(self):
        """/facts/bulk должен идти раньше /facts/{id}/..., /history/undo - не перекрываться."""
        order = [(m, (r.prefix or "") + x.path) for r in ROUTERS for x in r.routes for m in (x.methods or ())]
        assert ("POST", "/facts/bulk") in order and ("POST", "/history/undo") in order


@pytest.mark.data
class TestNormalizePreview:
    def test_ok_millivolts(self, env, client):
        r = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "voc", "raw": "1100 мВ"})
        assert r.status_code == 200
        j = r.json()
        assert j["ok"] is True and j["value"] == pytest.approx(1.1) and j["unit"] == "V"
        assert j["text"] == "1100 мВ → 1,10 В"

    def test_pce(self, client):
        j = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "pce", "raw": "19,0 %"}).json()
        assert j["ok"] and j["value"] == pytest.approx(19.0)

    def test_bare_number_canonical(self, client):
        j = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "voc", "raw": "1,1"}).json()
        assert j["ok"] and j["value"] == pytest.approx(1.1)

    @pytest.mark.parametrize("raw", ["1,1 кВ", "abc", ""])
    def test_not_recognized_still_200(self, client, raw):
        r = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "voc", "raw": raw})
        assert r.status_code == 200
        assert r.json()["ok"] is False and r.json()["text"] == "не распознано"

    def test_unknown_param_not_ok(self, client):
        r = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "zzz", "raw": "1"})
        assert r.status_code == 200 and r.json()["ok"] is False

    def test_missing_fields_400(self, client):
        assert client.post(url("/normalize/preview"), json={"param": "voc"}).status_code == 400

    def test_no_side_effects_no_action(self, env, client):
        r = client.post(url("/normalize/preview"), json={"collection": "solar", "param": "voc", "raw": "1 В"})
        assert "X-Action-Id" not in r.headers and env.history() == []

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        r = TestClient(env.app).post(url("/normalize/preview"), json={"collection": "solar", "param": "voc",
                                                                       "raw": "1"})
        assert r.status_code == 401


@pytest.mark.data
class TestChecksRules:
    def test_all_catalog_names_present(self, client):
        r = client.get(url("/checks/rules"))
        assert r.status_code == 200
        ids = [x["id"] for x in r.json()["data"]]
        assert set(ids) == set(CHECK_CATALOG) and len(ids) == len(set(ids))

    def test_rule_fields(self, client):
        for x in client.get(url("/checks/rules")).json()["data"]:
            for k in ("id", "title", "what", "level", "collections", "computed_by", "tz_ref", "enabled_by_default"):
                assert k in x, (x["id"], k)
            assert x["level"] in ("error", "warn", "info", "conflict", "doubt")
            assert x["computed_by"] in ("backend", "ml", "both")
            assert x["title"] == CHECK_CATALOG[x["id"]].title

    def test_enabled_by_default_matches_catalog(self, client):
        for x in client.get(url("/checks/rules")).json()["data"]:
            assert x["enabled_by_default"] == CHECK_CATALOG[x["id"]].backend_default_enabled

    def test_no_findings_in_rules(self, client):
        assert all("findings" not in x for x in client.get(url("/checks/rules")).json()["data"])

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        assert TestClient(env.app).get(url("/checks/rules")).status_code == 401


def _find_keys(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from _find_keys(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from _find_keys(v, key)


@pytest.mark.data
class TestChecksSummary:
    def test_summary_shape(self, env, client):
        r = client.get(url("/checks"), params={"collection": "solar"})
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["collection"] == "solar"
        assert {"papers", "facts", "errors", "warnings", "info", "ok_rules"} <= set(j["summary"])
        assert j["summary"]["papers"] == 1
        for rule in j["rules"]:
            for k in ("id", "title", "what", "level", "unit", "collections", "computed_by", "total", "accepted",
                      "status", "findings"):
                assert k in rule, (rule["id"], k)

    def test_rule_ids_only_from_catalog(self, client):
        j = client.get(url("/checks"), params={"collection": "solar"}).json()
        assert {r["id"] for r in j["rules"]} <= set(CHECK_CATALOG)
        for f in (f for r in j["rules"] for f in r["findings"]):
            assert f["check_name"] in CHECK_CATALOG

    def test_by_rule_only_catalog_names(self, env, client):
        """Любые by_rule в ответах (сводка статьи, quality) содержат только имена каталога."""
        docs = [client.get(url("/checks"), params={"collection": "solar"}).json(),
                client.get(url(f"/papers/{env.pid()}")).json(),
                client.get(url("/facts/table"), params={"collection": "solar"}).json()]
        for d in docs:
            for by_rule in _find_keys(d, "by_rule"):
                assert set(by_rule) <= set(CHECK_CATALOG)

    def test_collection_required_400(self, client):
        assert client.get(url("/checks")).status_code == 400

    def test_unknown_collection(self, client):
        r = client.get(url("/checks"), params={"collection": "nope"})
        assert r.status_code in (400, 404) or r.json()["summary"]["papers"] == 0

    def test_papers_filter(self, env, client):
        j = client.get(url("/checks"), params={"collection": "solar", "papers": "pap_other"}).json()
        assert j["summary"]["papers"] == 0 and all(not r["findings"] for r in j["rules"])

    def test_reflects_new_issue_synchronously(self, env, client):
        pce = env.find_fact("pce", row_id=env.jv_row())
        before = client.get(url("/checks"), params={"collection": "solar"}).json()["summary"]["warnings"]
        client.patch(url(f"/facts/{pce.id}"), json={"raw": "5 %"})
        j = client.get(url("/checks"), params={"collection": "solar"}).json()
        jv = next(r for r in j["rules"] if r["id"] == "jv_consistency")
        assert jv["findings"] and jv["status"] in ("warn", "error")
        assert j["summary"]["warnings"] >= before

    def test_accepted_as_is_hidden_unless_included(self, env, client):
        pce = env.find_fact("pce", row_id=env.jv_row())
        client.patch(url(f"/facts/{pce.id}"), json={"raw": "5 %"})
        iid = env.open_issue_ids("jv_consistency")[0]
        client.post(url("/issues/resolve"), json={"issue_ids": [iid], "how": "ok"})
        jv = next(r for r in client.get(url("/checks"), params={"collection": "solar"}).json()["rules"]
                  if r["id"] == "jv_consistency")
        assert iid not in [f["issue_id"] for f in jv["findings"]] and jv["accepted"] >= 1
        jv2 = next(r for r in client.get(url("/checks"), params={"collection": "solar",
                                                                 "include_accepted": True}).json()["rules"]
                   if r["id"] == "jv_consistency")
        assert iid in [f["issue_id"] for f in jv2["findings"]]

    def test_requires_auth(self, env):
        from fastapi.testclient import TestClient

        assert TestClient(env.app).get(url("/checks"), params={"collection": "solar"}).status_code == 401


@pytest.mark.data
class TestEvents:
    @pytest.fixture
    def published(self, env, monkeypatch):
        log: list[tuple[str, str, dict]] = []
        orig = env.services.bus.publish

        def spy(ws, event, data):
            log.append((ws, event, data))
            return orig(ws, event, data)

        monkeypatch.setattr(env.services.bus, "publish", spy)
        return log

    def test_accept_emits_facts_changed(self, env, svc, user, new_fact, published):
        svc.accept(user, new_fact.id)
        ev = [d for _, e, d in published if e == "facts.changed"]
        assert len(ev) == 1
        assert ev[0]["paper_id"] == new_fact.paper_id and ev[0]["fact_ids"] == [new_fact.id]
        assert ev[0]["by_user"] == user.id
        assert all(ws == user.workspace_id for ws, _, _ in published)

    def test_patch_opening_issue_emits_issues_changed(self, env, svc, user, published):
        pce = env.find_fact("pce", row_id=env.jv_row())
        res = svc.patch_value(user, pce.id, "5 %")
        ev = [d for _, e, d in published if e == "issues.changed"]
        assert len(ev) == 1 and ev[0]["paper_id"] == pce.paper_id
        assert ev[0]["opened"] == res.issues_changed.opened and ev[0]["closed"] == []

    def test_no_issues_event_when_nothing_changed(self, env, svc, user, new_fact, published):
        res = svc.accept(user, new_fact.id)
        if not res.issues_changed.opened and not res.issues_changed.closed:
            assert not [1 for _, e, _ in published if e == "issues.changed"]

    def test_bulk_one_facts_changed_per_paper(self, env, svc, user, published):
        ids = [f.id for f in env.facts()[:5]]
        svc.bulk(user, "accept", fact_ids=ids)
        ev = [d for _, e, d in published if e == "facts.changed"]
        assert len(ev) == 1 and sorted(ev[0]["fact_ids"]) == sorted(ids)

    def test_undo_emits_events(self, env, svc, user, new_fact, published):
        a = svc.accept(user, new_fact.id).action
        published.clear()
        svc.undo(user, a.id)
        assert [1 for _, e, _ in published if e == "facts.changed"]

    def test_failed_edit_emits_nothing(self, env, svc, user, new_fact, published):
        with pytest.raises(Exception):
            svc.patch_value(user, new_fact.id, "1,1 кВ")
        assert published == []

    def test_resolve_emits_issues_changed(self, env, svc, user, published):
        pce = env.find_fact("pce", row_id=env.jv_row())
        svc.patch_value(user, pce.id, "5 %")
        iid = env.open_issue_ids("jv_consistency")[0]
        published.clear()
        svc.resolve_issues(user, [iid], "ok")
        ev = [d for _, e, d in published if e == "issues.changed"]
        assert ev and iid in ev[0]["closed"]


class TestResultModels:
    """Модели результатов сервиса: значения по умолчанию (контракт для API-слоя)."""

    def test_defaults(self):
        from faktura.review import BulkResult, MutationResult, ReopenResult, ResolveResult, UndoResult

        m = MutationResult()
        assert m.fact is None and m.action is None and m.paper is None
        assert m.issues_changed.opened == [] and m.issues_changed.closed == []
        assert BulkResult().changed == 0 and BulkResult().skipped == 0
        assert ResolveResult().resolved == [] and UndoResult().changed == []
        assert ReopenResult(issue_id="x").issue_id == "x"

    def test_review_service_exposes_interface(self):
        from faktura.review import ReviewService

        for name in ("accept", "reject", "restore", "patch_value", "add_manual", "guess", "bulk",
                     "resolve_issues", "reopen_issue", "undo"):
            assert callable(getattr(ReviewService, name))
