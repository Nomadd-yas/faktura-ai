"""api.base.deps: get_services, get_uow, current_user, admin_user, page_params, page_response, mutation_response,
stub_response, idempotent. Чистые функции вызываются напрямую, зависимости - через тестовые маршруты."""
from __future__ import annotations

import json

import pytest
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from faktura.api.base.deps import (IdempotencyContext, PageParams, admin_user, current_user, get_services, get_uow,
                                   idempotent, mutation_response, page_params, page_response, stub_response)
from faktura.domain.entities import Action, CurrentUser, Page
from faktura.domain.errors import AppError


def _action(id_: str = "act_1") -> Action:
    return Action(id=id_, user_id="usr_1", action="edit", label="исправление")


# --- page_params -------------------------------------------------------------------------------

class TestPageParams:
    def test_defaults(self):
        p = page_params()
        assert isinstance(p, PageParams)
        assert p.limit == 50 and p.cursor is None and p.sort is None

    def test_passes_values(self):
        p = page_params(limit=100, cursor="abc", sort="-year,title")
        assert (p.limit, p.cursor, p.sort) == (100, "abc", "-year,title")

    def test_limit_500_is_allowed(self):
        assert page_params(limit=500).limit == 500

    @pytest.mark.parametrize("bad", [501, 100000])
    def test_limit_above_500_rejected_or_clamped(self, bad):
        try:
            p = page_params(limit=bad)
        except AppError as e:
            assert e.code == "validation_error" and e.field == "limit"
        else:
            assert p.limit == 500

    @pytest.mark.parametrize("bad", [0, -1, -50])
    def test_limit_below_one_rejected_or_clamped(self, bad):
        try:
            p = page_params(limit=bad)
        except AppError as e:
            assert e.code == "validation_error" and e.field == "limit"
        else:
            assert p.limit >= 1

    def test_http_non_numeric_limit_is_400_validation_error(self, app, h):
        app.add_api_route(h.url("/_t/page"), lambda p=Depends(page_params): p.model_dump(), methods=["GET"])
        r = TestClient(app).get(h.url("/_t/page"), params={"limit": "abc"})
        h.assert_error(r, 400, "validation_error", field="limit")

    def test_http_defaults_and_query(self, app, h):
        app.add_api_route(h.url("/_t/page2"), lambda p=Depends(page_params): p.model_dump(), methods=["GET"])
        c = TestClient(app)
        assert c.get(h.url("/_t/page2")).json() == {"limit": 50, "cursor": None, "sort": None}
        assert c.get(h.url("/_t/page2"), params={"limit": 7, "cursor": "zz"}).json()["limit"] == 7


# --- page_response -----------------------------------------------------------------------------

class TestPageResponse:
    def test_shape_with_cursor(self):
        out = page_response(Page(data=[{"a": 1}, {"a": 2}], total=42, next_cursor="eyJrIjpbIkQwMDEiLDEyXX0"))
        assert out == {"data": [{"a": 1}, {"a": 2}], "total": 42, "next_cursor": "eyJrIjpbIkQwMDEiLDEyXX0"}

    def test_last_page_has_null_cursor(self):
        out = page_response(Page(data=[{"a": 1}], total=1, next_cursor=None))
        assert "next_cursor" in out and out["next_cursor"] is None

    def test_empty_page(self):
        out = page_response(Page(data=[], total=0, next_cursor=None))
        assert out == {"data": [], "total": 0, "next_cursor": None}

    def test_unknown_total_is_null_not_missing(self):
        out = page_response(Page(data=[{"a": 1}]))
        assert "total" in out and out["total"] is None

    def test_result_is_json_serializable(self):
        json.dumps(page_response(Page(data=[{"a": "б"}], total=1, next_cursor="x")))

    def test_pydantic_items_are_serialized(self):
        out = page_response(Page(data=[_action()], total=1))
        json.dumps(out, default=None)  # без default: должны быть уже dict
        assert out["data"][0]["id"] == "act_1"


# --- mutation_response -------------------------------------------------------------------------

class TestMutationResponse:
    def test_adds_action_id_to_body_and_header(self):
        r = mutation_response({"paper": {"x": 1}}, _action("act_95"))
        assert isinstance(r, JSONResponse)
        assert r.status_code == 200
        assert json.loads(r.body) == {"paper": {"x": 1}, "action_id": "act_95"}
        assert r.headers["X-Action-Id"] == "act_95"

    @pytest.mark.parametrize("status", [200, 201, 202])
    def test_custom_status(self, status):
        assert mutation_response({}, _action(), status=status).status_code == status

    def test_does_not_mutate_input_payload(self):
        payload = {"a": 1}
        mutation_response(payload, _action())
        assert payload == {"a": 1}

    def test_action_overrides_stale_action_id_in_payload(self):
        r = mutation_response({"action_id": "old"}, _action("act_new"))
        assert json.loads(r.body)["action_id"] == "act_new"
        assert r.headers["X-Action-Id"] == "act_new"

    def test_non_ascii_payload(self):
        r = mutation_response({"message": "Отменено: исправление"}, _action())
        assert json.loads(r.body.decode("utf-8"))["message"] == "Отменено: исправление"

    def test_empty_payload(self):
        assert json.loads(mutation_response({}, _action("act_7")).body) == {"action_id": "act_7"}


# --- stub_response -----------------------------------------------------------------------------

class TestStubResponse:
    def test_marks_stub(self):
        r = stub_response({"data": []})
        assert isinstance(r, JSONResponse)
        assert r.status_code == 200
        assert r.headers["X-Faktura-Stub"] == "1"
        assert json.loads(r.body) == {"data": []}

    @pytest.mark.parametrize("status", [200, 201])
    def test_custom_status(self, status):
        assert stub_response({}, status=status).status_code == status

    def test_payload_passes_through_untouched(self):
        payload = {"nodes": [], "edges": [], "default_root": None, "текст": "значение"}
        assert json.loads(stub_response(payload).body.decode("utf-8")) == payload

    def test_json_content_type(self):
        assert stub_response({}).headers["content-type"].startswith("application/json")


# --- зависимости через маршруты ----------------------------------------------------------------

@pytest.fixture
def dep_app(app, h):
    """Приложение с тестовыми маршрутами, использующими зависимости."""

    def r_services(services=Depends(get_services)):
        return {"same": True, "type": type(services).__name__, "has_db": hasattr(services, "db")}

    def r_uow(uow=Depends(get_uow)):
        return {"admin_found": uow.users.get_by_email("admin@example.com") is not None}

    def r_user(user: CurrentUser = Depends(current_user)):
        return user.model_dump()

    def r_admin(user: CurrentUser = Depends(admin_user)):
        return {"id": user.id, "is_admin": user.is_admin}

    def r_idem(request: Request, user: CurrentUser = Depends(current_user)):
        return idempotent(request, user).model_dump()

    for path, fn in (("services", r_services), ("uow", r_uow), ("user", r_user), ("admin", r_admin),
                     ("idem", r_idem)):
        app.add_api_route(h.url(f"/_t/{path}"), fn, methods=["GET"])
    return app


class TestDependencies:
    def test_get_services_returns_the_given_instance(self, app, services, h):
        captured = {}

        def route(request: Request):
            captured["s"] = get_services(request)
            return {}

        app.add_api_route(h.url("/_t/svc"), route, methods=["GET"])
        TestClient(app).get(h.url("/_t/svc"))
        assert captured["s"] is services

    def test_get_uow_usable_and_committed(self, dep_app, h):
        r = TestClient(dep_app).get(h.url("/_t/uow"))
        assert r.status_code == 200 and r.json() == {"admin_found": True}

    def test_get_uow_commits_on_success_and_rolls_back_on_error(self, app, services, h):
        def create(uow=Depends(get_uow)):
            admin = uow.users.get_by_email(services.settings.admin_email)
            uow.users.create(workspace_id=admin.workspace_id, email="rollback@example.com", name="x",
                             password_hash="x", is_admin=False)
            raise AppError("conflict", "boom")

        def create_ok(uow=Depends(get_uow)):
            admin = uow.users.get_by_email(services.settings.admin_email)
            uow.users.create(workspace_id=admin.workspace_id, email="commit@example.com", name="x",
                             password_hash="x", is_admin=False)
            return {}

        app.add_api_route(h.url("/_t/rb"), create, methods=["POST"])
        app.add_api_route(h.url("/_t/ok"), create_ok, methods=["POST"])
        c = TestClient(app)
        assert c.post(h.url("/_t/rb")).status_code == 409
        assert c.post(h.url("/_t/ok")).status_code == 200
        with services.db.uow() as uow:
            assert uow.users.get_by_email("rollback@example.com") is None
            assert uow.users.get_by_email("commit@example.com") is not None

    def test_current_user_from_bearer(self, dep_app, admin_token, admin_creds, h):
        r = TestClient(dep_app).get(h.url("/_t/user"), headers=h.bearer(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["email"] == admin_creds[0] and body["is_admin"] is True
        assert body["id"] and body["workspace_id"]

    def test_current_user_without_token_401(self, dep_app, h):
        h.assert_error(TestClient(dep_app).get(h.url("/_t/user")), 401, "unauthorized")

    def test_current_user_expired_token(self, dep_app, admin_token, clock, h):
        clock.advance(minutes=20)
        h.assert_error(TestClient(dep_app).get(h.url("/_t/user"), headers=h.bearer(admin_token)), 401, "token_expired")

    def test_admin_user_allows_admin(self, dep_app, admin_token, h):
        r = TestClient(dep_app).get(h.url("/_t/admin"), headers=h.bearer(admin_token))
        assert r.status_code == 200 and r.json()["is_admin"] is True

    def test_admin_user_forbids_regular_user(self, dep_app, user_token, h):
        h.assert_error(TestClient(dep_app).get(h.url("/_t/admin"), headers=h.bearer(user_token)), 403, "forbidden")

    def test_admin_user_without_token_401_not_403(self, dep_app, h):
        h.assert_error(TestClient(dep_app).get(h.url("/_t/admin")), 401, "unauthorized")

    def test_admin_rights_are_read_from_db_not_stale_token(self, dep_app, services, client, user_creds, h):
        """Права снимаются без ожидания истечения access (15 мин)."""
        token = h.login_token(client, *user_creds)
        with services.db.uow() as uow:
            u = uow.users.get_by_email(user_creds[0])
            uow.users.update(u.id, is_admin=True)
        assert TestClient(dep_app).get(h.url("/_t/admin"), headers=h.bearer(token)).status_code == 200


class TestIdempotent:
    def test_without_header(self, dep_app, admin_token, h):
        r = TestClient(dep_app).get(h.url("/_t/idem"), headers=h.bearer(admin_token))
        assert r.json() == {"key": None, "replay": None}

    def test_first_call_with_key_has_no_replay(self, dep_app, admin_token, h):
        r = TestClient(dep_app).get(h.url("/_t/idem"), headers={**h.bearer(admin_token), "Idempotency-Key": "7c1e-aa"})
        assert r.json() == {"key": "7c1e-aa", "replay": None}

    def test_replay_returns_stored_response(self, dep_app, services, admin_token, h):
        c = TestClient(dep_app)
        user_id = c.get(h.url("/_t/user"), headers=h.bearer(admin_token)).json()["id"]
        stored = {"status": 202, "body": {"export_id": "exp_1"}}
        with services.db.uow() as uow:
            uow.idempotency.put(user_id, "key-1", stored)
        r = c.get(h.url("/_t/idem"), headers={**h.bearer(admin_token), "Idempotency-Key": "key-1"})
        assert r.json()["key"] == "key-1"
        assert r.json()["replay"] == stored

    def test_key_is_scoped_to_user(self, dep_app, services, admin_token, user_token, h):
        c = TestClient(dep_app)
        admin_id = c.get(h.url("/_t/user"), headers=h.bearer(admin_token)).json()["id"]
        with services.db.uow() as uow:
            uow.idempotency.put(admin_id, "shared-key", {"status": 200, "body": {}})
        r = c.get(h.url("/_t/idem"), headers={**h.bearer(user_token), "Idempotency-Key": "shared-key"})
        assert r.json()["replay"] is None

    def test_context_model(self):
        ctx = IdempotencyContext()
        assert ctx.key is None and ctx.replay is None
