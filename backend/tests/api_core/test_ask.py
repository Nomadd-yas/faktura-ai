"""«Вопросы по разбору» (CORE_API §4i), область solar: форма CaseAnswer, правки v2, чужой разбор, ML недоступен."""
from __future__ import annotations

import json

import pytest

from faktura.ask.client import AskUnavailable, HttpAskClient

from .conftest import API


class FakeAsk:
    """Фейковый ML: план и текст задаются в тесте, вызовы считаются. Реальный LLM в тестах не вызывается."""

    def __init__(self, plan=None, text=None, fail=False):
        self.plan_body, self.text, self.fail = plan, text, fail
        self.calls: list[tuple] = []

    def plan(self, question, run):
        self.calls.append(("plan", question, run))
        if self.fail:
            raise AskUnavailable("down")
        return self.plan_body, {"model": "fake", "tokens_in": 900, "tokens_out": 60, "cost": 0.0001}

    def write(self, question, intent, facts, draft):
        self.calls.append(("write", question, intent))
        if self.fail:
            raise AskUnavailable("down")
        return (self.text if self.text is not None else draft), {"model": "fake", "tokens_in": 300, "tokens_out": 80,
                                                                 "cost": 0.00005}


def ask(client, auth, rid, question, **kw):
    return client.post(f"{API}/runs/{rid}/ask", json={"question": question, **kw}, headers=auth)


@pytest.fixture
def auth_b(client, services) -> dict[str, str]:
    """Второй пользователь в том же рабочем пространстве, что и админ."""
    from faktura.domain.security import hash_password
    from faktura.storage import DEFAULT_WORKSPACE_ID

    with services.db.uow() as uow:
        uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email="b@example.com", name="B",
                         password_hash=hash_password("b-password-123"), is_admin=False)
    r = client.post(f"{API}/auth/login", json={"email": "b@example.com", "password": "b-password-123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def ctx(app):
    return app.state.faktura










def test_owner_only_and_validation(client, auth, auth_b, run_two, ctx):
    ctx.ask_client = None
    assert ask(client, auth, "run_nope", "Лучший КПД").status_code == 404
    assert client.get(f"{API}/runs/run_nope/ask/suggestions", headers=auth).status_code == 404
    assert client.post(f"{API}/runs/{run_two['id']}/ask", json={"question": "Лучший КПД"}).status_code == 401
    assert ask(client, auth, run_two["id"], "  ").status_code in (400, 422)
    r = ask(client, auth, run_two["id"], "я" * 501)
    assert r.status_code in (400, 422) and r.json()["error"]["code"] == "validation_error"
    assert ask(client, auth_b, run_two["id"], "Лучший КПД").status_code == 404  # чужой разбор не виден
    assert client.get(f"{API}/runs/{run_two['id']}/ask/suggestions", headers=auth_b).status_code == 404














def test_via_mock_ml_server_contract(client, auth, run_two, ctx, settings):
    """Мок ML (`faktura.ml.mock_server`) реализует /ask/plan и /ask/write: HttpAskClient работает через них."""
    from fastapi.testclient import TestClient

    from faktura.ml.mock_server import create_mock_app

    http = TestClient(create_mock_app(settings))
    ctx.ask_client = HttpAskClient("http://testserver", http=http)
    body = ask(client, auth, run_two["id"], "Лучший КПД").json()
    assert body["meta"]["planner"] == "llm" and body["meta"]["writer"] == "llm" and body["meta"]["model"] == "mock"
    assert body["cards"][0]["kind"] == "results"
    bad = http.post("/ask/plan", json={"question": "x"})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_request"




