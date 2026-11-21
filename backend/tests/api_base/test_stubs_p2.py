"""Заглушки P2 без другого владельца (api_contract §4.14, §1.8, У-15): код, X-Faktura-Stub: 1, ключи как в контракте."""
from __future__ import annotations

import pytest

# (id, method, path, json-тело, статус, точный набор ключей верхнего уровня или None, ожидаемое тело или None)
STUBS = [
    ("me_stats", "GET", "/me/stats", None, 200,
     {"papers", "mine", "facts", "accepted", "edited", "rejected", "issues", "projects", "questions"}, None),
    ("collection_stats", "GET", "/collections/solar/stats", None, 200,
     {"papers", "facts", "processing", "open_issues"}, None),
    ("views_list", "GET", "/views", None, 200, {"data"}, {"data": []}),
    ("views_create", "POST", "/views", {"name": "Мой вид", "query": "КПД выше 18"}, 201,
     {"name", "query", "id", "action_id"}, None),
    ("graph", "GET", "/graph", None, 200, {"nodes", "edges", "default_root"},
     {"nodes": [], "edges": [], "default_root": None}),
    ("search", "GET", "/search?q=perovskite", None, 200, {"q", "layout_fixed", "groups"},
     {"q": "perovskite", "layout_fixed": None, "groups": []}),
    ("projects_list", "GET", "/projects", None, 200, {"data"}, {"data": []}),
    ("projects_create", "POST", "/projects", {"name": "Перовскиты", "collection": "solar", "note": ""}, 201,
     {"id", "name", "collection", "note", "pinned", "created_at", "updated_at", "chats_count", "papers_count",
      "open_issues", "action_id"}, None),
    ("chats_list", "GET", "/chats", None, 200, {"data", "total", "next_cursor"},
     {"data": [], "total": 0, "next_cursor": None}),
    ("dict_unit_add", "POST", "/dictionary/units", {"param": "voc", "unit": "uV", "factor": 0.000001}, 201,
     {"unit", "renormalize", "action_id"}, None),  # синонимы словаря - настоящие, см. tests/api_core/test_dictionary.py
]
STUB_IDS = [s[0] for s in STUBS]


@pytest.mark.parametrize("sid,method,path,body,status,keys,expected", STUBS, ids=STUB_IDS)
class TestStubContract:
    def test_status_header_and_keys(self, client, user_token, h, sid, method, path, body, status, keys, expected):
        r = client.request(method, h.url(path), json=body, headers=h.bearer(user_token))
        assert r.status_code == status, r.text
        assert r.headers.get("X-Faktura-Stub") == "1"
        assert r.headers.get("X-Request-Id")
        assert r.headers["content-type"].startswith("application/json")
        assert set(r.json()) == keys
        if expected is not None:
            assert r.json() == expected

    def test_requires_auth(self, client, h, sid, method, path, body, status, keys, expected):
        h.assert_error(client.request(method, h.url(path), json=body), 401, "unauthorized")

    def test_expired_token_rejected(self, client, user_token, clock, h, sid, method, path, body, status, keys, expected):
        clock.advance(minutes=20)
        r = client.request(method, h.url(path), json=body, headers=h.bearer(user_token))
        h.assert_error(r, 401, "token_expired")

    def test_mutating_stub_marks_action_id(self, client, user_token, h, sid, method, path, body, status, keys,
                                           expected):
        if method == "GET":
            pytest.skip("только изменяющие")
        r = client.request(method, h.url(path), json=body, headers=h.bearer(user_token))
        assert r.json()["action_id"] and r.headers["X-Action-Id"] == r.json()["action_id"]


class TestStubDetails:
    def test_me_stats_are_zeros(self, client, user_token, h):
        body = client.get(h.url("/me/stats"), headers=h.bearer(user_token)).json()
        assert all(isinstance(v, int) and v == 0 for v in body.values())

    def test_collection_stats_are_zeros_for_rag_too(self, client, user_token, h):
        r = client.get(h.url("/collections/rag/stats"), headers=h.bearer(user_token))
        assert r.status_code == 200 and r.headers["X-Faktura-Stub"] == "1"

    def test_collection_stats_project_id_param_accepted(self, client, user_token, h):
        r = client.get(h.url("/collections/solar/stats"), params={"project_id": "pr_1"}, headers=h.bearer(user_token))
        assert r.status_code == 200

    def test_views_post_echoes_fields_and_stores_nothing(self, client, user_token, h):
        r = client.post(h.url("/views"), json={"name": "Мой вид", "query": "КПД выше 18"}, headers=h.bearer(user_token))
        body = r.json()
        assert body["name"] == "Мой вид" and body["query"] == "КПД выше 18" and body["id"]
        assert client.get(h.url("/views"), headers=h.bearer(user_token)).json() == {"data": []}

    def test_projects_post_stores_nothing(self, client, user_token, h):
        client.post(h.url("/projects"), json={"name": "X", "collection": "solar"}, headers=h.bearer(user_token))
        assert client.get(h.url("/projects"), headers=h.bearer(user_token)).json() == {"data": []}

    def test_projects_post_echoes_name_and_empty_name_default(self, client, user_token, h):
        ok = client.post(h.url("/projects"), json={"name": "Перовскиты", "collection": "solar"},
                         headers=h.bearer(user_token)).json()
        assert ok["name"] == "Перовскиты" and ok["collection"] == "solar"
        blank = client.post(h.url("/projects"), json={"name": "", "collection": "solar"},
                            headers=h.bearer(user_token)).json()
        assert blank["name"] == "Новый проект"

    def test_projects_post_bad_collection_400(self, client, user_token, h):
        r = client.post(h.url("/projects"), json={"name": "X", "collection": "no-such"}, headers=h.bearer(user_token))
        h.assert_error(r, 400, "validation_error", field="collection")

    def test_project_by_id_404(self, client, user_token, h):
        h.assert_error(client.get(h.url("/projects/pr_x"), headers=h.bearer(user_token)), 404, "not_found")

    @pytest.mark.parametrize("method,path", [("PATCH", "/projects/pr_x"), ("DELETE", "/projects/pr_x"),
                                              ("POST", "/projects/pr_x/restore")])
    def test_project_mutations_on_unknown_404(self, client, user_token, h, method, path):
        r = client.request(method, h.url(path), json={} if method != "DELETE" else None, headers=h.bearer(user_token))
        h.assert_error(r, 404, "not_found")

    def test_chat_by_id_404(self, client, user_token, h):
        h.assert_error(client.get(h.url("/chats/ch_x"), headers=h.bearer(user_token)), 404, "not_found")

    def test_chat_turns_of_unknown_chat_404(self, client, user_token, h):
        h.assert_error(client.get(h.url("/chats/ch_x/turns"), headers=h.bearer(user_token)), 404, "not_found")

    def test_entities_papers_unknown_404(self, client, user_token, h):
        h.assert_error(client.get(h.url("/entities/ent_x/papers"), headers=h.bearer(user_token)), 404, "not_found")

    def test_graph_edges_csv_header_only(self, client, user_token, h):
        r = client.get(h.url("/graph/edges.csv"), headers=h.bearer(user_token))
        assert r.status_code == 200
        assert r.headers["X-Faktura-Stub"] == "1"
        assert r.headers["content-type"].startswith("text/csv")
        lines = r.text.strip().splitlines()
        assert lines == ["source,target,type,source_label,target_label"]

    def test_graph_edges_csv_requires_auth(self, client, h):
        h.assert_error(client.get(h.url("/graph/edges.csv")), 401, "unauthorized")

    def test_search_echoes_query(self, client, user_token, h):
        r = client.get(h.url("/search"), params={"q": "КПД выше 18"}, headers=h.bearer(user_token))
        assert r.json()["q"] == "КПД выше 18" and r.json()["groups"] == []


class TestChatAskStub:
    ASK = {"question": "Какие приборы лучшие по КПД?", "project_id": None, "collection": "solar"}

    def test_ask_json_201_shape(self, client, user_token, h):
        r = client.post(h.url("/chats/ask"), json=self.ASK, headers={**h.bearer(user_token), "Accept": "application/json"})
        assert r.status_code == 201, r.text
        assert r.headers["X-Faktura-Stub"] == "1"
        body = r.json()
        assert {"chat", "turn_id", "stream_url", "existing"} <= set(body)
        assert body["existing"] is False
        assert body["chat"]["id"] and body["chat"]["kind"] == "ask"
        assert body["stream_url"].endswith(f"/turns/{body['turn_id']}/stream")

    def test_stream_has_step_outro_done_in_order(self, client, user_token, h):
        body = client.post(h.url("/chats/ask"), json=self.ASK,
                           headers={**h.bearer(user_token), "Accept": "application/json"}).json()
        r = client.get(body["stream_url"], headers=h.bearer(user_token))
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = r.text
        i_step, i_outro, i_done = text.find("event: step"), text.find("event: outro"), text.find("event: done")
        assert -1 < i_step < i_outro < i_done
        assert "Ответы в чате появятся позже" in text

    @pytest.mark.parametrize("question", ["", "   ", "я" * 2001])
    def test_ask_invalid_question_400(self, client, user_token, question, h):
        r = client.post(h.url("/chats/ask"), json={**self.ASK, "question": question}, headers=h.bearer(user_token))
        h.assert_error(r, 400, "validation_error", field="question")

    def test_ask_requires_auth(self, client, h):
        h.assert_error(client.post(h.url("/chats/ask"), json=self.ASK), 401, "unauthorized")


class TestStubsAreSecondary:
    def test_all_stub_routes_exist_in_openapi_or_routes(self, app):
        """Каждый путь параметризованной таблицы реально смонтирован (а не 404 из-за опечатки)."""
        import re

        from fastapi.routing import APIRoute

        have = {(m, re.sub(r"\{[^}]+\}", "{}", r.path)) for r in app.routes if isinstance(r, APIRoute) for m in r.methods}
        for sid, method, path, *_ in STUBS:
            p = "/api/v1" + path.split("?")[0]
            p = re.sub(r"\{[^}]+\}", "{}", p)
            norm = {(m, re.sub(r"\{[^}]+\}", "{}", q)) for m, q in have}
            match = any(m == method and re.fullmatch(re.sub(r"\{\}", "[^/]+", q), p) for m, q in norm)
            assert match, f"{sid}: {method} {p} не смонтирован"

    def test_stub_header_absent_on_real_endpoints(self, client, user_token, h):
        for path in ("/me", "/collections", "/auth/me"):
            assert "X-Faktura-Stub" not in client.get(h.url(path), headers=h.bearer(user_token)).headers
        assert "X-Faktura-Stub" not in client.get(h.url("/health")).headers
