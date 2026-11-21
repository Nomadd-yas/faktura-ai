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


def test_case_answer_shape_best_pce_matches_best_values(client, auth, run_two, ctx):
    ctx.ask_client = None  # без ML: правиловый планировщик
    rid = run_two["id"]
    r = ask(client, auth, rid, "Лучший КПД")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"text", "cards", "cites", "suggestions", "meta"} <= set(body)
    card = body["cards"][0]
    assert card["kind"] == "results" and card["rows"]
    best = client.get(f"{API}/runs/{rid}/summary", headers=auth).json()["best_values"]["PCE"]
    assert card["rows"][0] == {"pid": best["pid"], "rid": best["result_id"]}
    c0 = body["cites"][0]
    assert c0["pid"] == best["pid"] and c0["rid"] == best["result_id"] and c0["label"]
    assert "[1]" in body["text"] and best["pid"] in body["text"]
    assert body["meta"]["planner"] == "rules" and body["meta"]["degraded"] is False
    assert 1 <= len(body["suggestions"]) <= 3 and "Лучший КПД" not in body["suggestions"]


def test_issues_of_paper(client, auth, run_two, ctx):
    ctx.ask_client = None
    rid = run_two["id"]
    run = client.get(f"{API}/runs/{rid}", headers=auth).json()
    f = max(run["files"], key=lambda x: x["open_issue_count"])
    assert f["open_issue_count"] > 0
    body = ask(client, auth, rid, f"Замечания у {f['id']}").json()
    card = body["cards"][0]
    assert card["kind"] == "issues" and len(card["items"]) == f["open_issue_count"]
    assert {i["pid"] for i in card["items"]} == {f["id"]}
    assert any(c.get("iid") for c in body["cites"])
    iss = client.get(f"{API}/issues", params={"run": rid, "pid": f["id"], "limit": 500}, headers=auth).json()
    have = {d["row"]["issue_id"] for d in iss["data"]}
    assert {i["iid"] for i in card["items"]} <= have


def test_schema_question(client, auth, run_two, ctx):
    ctx.ask_client = None
    rid = run_two["id"]
    body = ask(client, auth, rid, "Что в таблице devices").json()
    kinds = [c["kind"] for c in body["cards"]]
    assert kinds[0] == "schema" and "tableRows" in kinds
    sch = body["cards"][0]
    assert sch["table"] == "devices" and any(f["name"] == "device_id" and f["required"] for f in sch["fields"])
    assert set(sch["fields"][0]) >= {"name", "ru", "required", "desc"}
    tr = next(c for c in body["cards"] if c["kind"] == "tableRows")
    assert tr["domain"] == "solar" and tr["cols"] and tr["rows"][0]["id"].startswith("DEV") or tr["rows"]
    one = ask(client, auth, rid, "Что значит extraction_status").json()
    assert one["cards"][0]["kind"] == "schema" and one["cards"][0]["fields"][0]["name"] == "extraction_status"


def test_rejected_rows_excluded_and_correction_used(client, auth, run_two, ctx):
    ctx.ask_client = None
    rid = run_two["id"]
    first = ask(client, auth, rid, "Лучший КПД").json()
    top = first["cards"][0]["rows"][0]
    url = f"{API}/runs/{rid}/papers/solar/{top['pid']}/results/{top['rid']}/review"
    r = client.patch(url, json={"status": "rejected"}, headers=auth)
    assert r.status_code == 200, r.text
    second = ask(client, auth, rid, "Лучший КПД").json()
    assert top not in second["cards"][0]["rows"]
    assert all(c.get("rid") != top["rid"] or c["pid"] != top["pid"] for c in second["cites"])
    # исправленное значение попадает в ответ
    new = second["cards"][0]["rows"][0]
    client.patch(f"{API}/runs/{rid}/papers/solar/{new['pid']}/results/{new['rid']}/review",
                 json={"status": "accepted", "corrected_value": 77.7}, headers=auth)
    third = ask(client, auth, rid, "Лучший КПД").json()
    assert third["cards"][0]["rows"][0] == new and "77,7" in third["text"] and "исправлени" in third["text"]


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


def test_suggestions_endpoint(client, auth, run_two, ctx):
    ctx.ask_client = None
    s = client.get(f"{API}/runs/{run_two['id']}/ask/suggestions", headers=auth).json()
    assert 2 <= len(s) <= 4 and "Лучший КПД" in s and any(x.startswith("Замечания у D00") for x in s)


def test_llm_plan_and_writer_with_fake(client, auth, run_two, ctx):
    plan = {"intent": "best", "metrics": ["Voc"], "pids": [], "material_class": [], "layers": [], "device": "",
            "aggregation": "", "origin": "primary", "min_value": None, "max_value": None, "rag_config": "",
            "rag_dataset": "", "rag_task": "", "rag_list": "", "table": "", "field": "", "check": "", "sort": "best",
            "limit": 3, "per_paper": True, "evil": "x"}
    fake = FakeAsk(plan=plan)
    ctx.ask_client = fake
    body = ask(client, auth, run_two["id"], "какое самое большое напряжение?").json()
    assert body["meta"]["planner"] == "llm" and body["meta"]["writer"] == "llm"
    assert body["meta"]["tokens_in"] == 1200 and body["meta"]["cost"] == pytest.approx(0.00015)
    assert body["cards"][0]["kind"] == "results" and "Voc" in body["cards"][0]["title"]
    kinds = [c[0] for c in fake.calls]
    assert kinds == ["plan", "write"]
    run_desc = fake.calls[0][2]  # планировщик не получает строк результатов и полного списка кодов
    assert "pids" not in run_desc and run_desc["domain"] == "solar" and run_desc["metrics"]
    assert "normalized_value" not in json.dumps(run_desc)
    assert len(json.dumps(run_desc)) < 6000


def test_writer_with_invented_number_falls_back_to_template(client, auth, run_two, ctx):
    plan = {"intent": "best", "metrics": ["PCE"], "limit": 2}
    ctx.ask_client = FakeAsk(plan=plan, text="Лучший КПД составляет 99,9 % [1].")
    body = ask(client, auth, run_two["id"], "Лучший КПД").json()
    assert body["meta"]["writer"] == "template" and "99,9" not in body["text"]


def test_ml_down_falls_back_to_rules(client, auth, run_two, ctx):
    ctx.ask_client = FakeAsk(fail=True)
    body = ask(client, auth, run_two["id"], "Лучший КПД").json()
    assert body["meta"]["planner"] == "rules" and body["meta"]["degraded"] is True
    assert body["cards"][0]["kind"] == "results" and body["text"]


def test_llm_unknown_intent_uses_rules_and_foreign_pids_dropped(client, auth, run_two, ctx):
    ctx.ask_client = FakeAsk(plan={"intent": "unknown"})
    assert ask(client, auth, run_two["id"], "Лучший КПД").json()["cards"][0]["kind"] == "results"
    ctx.ask_client = FakeAsk(plan={"intent": "issues", "pids": ["D999"]})
    body = ask(client, auth, run_two["id"], "замечания у D999").json()
    assert body["cards"] == [] and "D999" in body["text"] and "нет в этом разборе" in body["text"]


def test_unknown_question_gets_help_text(client, auth, run_two, ctx):
    ctx.ask_client = None
    body = ask(client, auth, run_two["id"], "расскажи анекдот").json()
    assert body["cards"] == [] and "Не удалось понять" in body["text"] and body["suggestions"]


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


def test_http_client_errors_become_unavailable():
    import httpx

    def handler(request):
        return httpx.Response(503, json={"code": "x"})

    c = HttpAskClient("http://ml", http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AskUnavailable):
        c.plan("q", {})

    def boom(request):
        raise httpx.ReadTimeout("t")

    c2 = HttpAskClient("http://ml", http=httpx.Client(transport=httpx.MockTransport(boom)))
    with pytest.raises(AskUnavailable):
        c2.write("q", "best", {}, "d")


def test_rate_limit(client, auth, run_two, ctx):
    ctx.ask_client = None
    ctx.chat_limiter.limit = 2
    assert ask(client, auth, run_two["id"], "Лучший КПД").status_code == 200
    assert ask(client, auth, run_two["id"], "Лучший КПД").status_code == 200
    assert ask(client, auth, run_two["id"], "Лучший КПД").status_code == 429
