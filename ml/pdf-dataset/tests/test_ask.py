"""[faktura-patch] «Вопросы по разбору»: /ask/plan и /ask/write с фейковым LLM-клиентом. RouterAI не вызывается."""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import ask  # noqa: E402

RUN = {"domain": "rag", "papers_total": 2,
       "papers": [{"pid": "D001", "title": "RAG-CoT", "results": 120, "issues": 3, "in_scope": True},
                  {"pid": "D014", "title": "Knowledge selection", "results": 28649, "issues": 8, "in_scope": True}],
       "metrics": [{"name": "Answer F1", "n": 12074, "papers": 2}, {"name": "EM", "n": 852, "papers": 1}],
       "metrics_total": 2, "tables": ["publications", "configurations", "results", "issues"],
       "checks": [["plot_precision", 4]], "datasets": ["HotpotQA"], "tasks": ["open-domain QA"]}
PLAN = {"intent": "best", "metrics": ["Answer F1"], "pids": [], "material_class": [], "layers": [], "device": "",
        "aggregation": "", "origin": "primary", "min_value": None, "max_value": None, "rag_config": "",
        "rag_dataset": "HotpotQA", "rag_task": "", "rag_list": "", "table": "", "field": "", "check": "",
        "sort": "best", "limit": 5, "per_paper": True}


class FakeClient:
    """Записывает kwargs каждого запроса; отвечает заданным содержимым."""

    def __init__(self, content=None, usage=None, exc=None):
        self.calls = []
        self.content = json.dumps(PLAN) if content is None else content
        self.usage = usage if usage is not None else {"prompt_tokens": 1000, "completion_tokens": 100}
        self.exc = exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        usage = SimpleNamespace(model_dump=lambda: dict(self.usage))
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")], usage=usage)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("ASK_PROVIDER", "ROUTERAI_PROVIDER", "ASK_MODEL", "ROUTERAI_MODEL", "ASK_PRICE_IN", "ASK_PRICE_OUT"):
        monkeypatch.delenv(k, raising=False)


def test_plan_request_is_pinned_without_reasoning_and_strict():
    c = FakeClient()
    out = ask.plan("Лучший Answer F1 на HotpotQA", RUN, client=c)
    assert out["plan"] == PLAN
    (kw,) = c.calls
    eb = kw["extra_body"]
    assert eb["provider"] == {"order": ["wafer"], "allow_fallbacks": False}  # провайдер закреплён всегда
    assert eb["reasoning"] == {"enabled": False}  # рассуждение выключено
    rf = kw["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"] == ask.plan_schema()
    assert kw["temperature"] == 0.0 and kw["max_tokens"] == ask.PLAN_MAX_TOKENS
    user = kw["messages"][1]["content"]
    assert "Лучший Answer F1 на HotpotQA" in user and '"Answer F1"' in user
    assert out["meta"]["tokens_in"] == 1000 and out["meta"]["tokens_out"] == 100 and out["meta"]["provider"] == "wafer"


def test_provider_and_model_from_env_and_never_unpinned(monkeypatch):
    monkeypatch.setenv("ASK_PROVIDER", "Wafer, other")
    monkeypatch.setenv("ASK_MODEL", "vendor/tiny")
    c = FakeClient()
    ask.plan("q", RUN, client=c)
    assert c.calls[0]["extra_body"]["provider"] == {"order": ["wafer", "other"], "allow_fallbacks": False}
    assert c.calls[0]["model"] == "vendor/tiny"
    monkeypatch.setenv("ASK_PROVIDER", " , ")
    c2 = FakeClient()
    with pytest.raises(ask.AskError) as e:
        ask.plan("q", RUN, client=c2)
    assert e.value.code == "not_configured" and e.value.status == 503 and not c2.calls  # без закрепления - не вызываем


def test_cost_from_usage_cost_or_prices(monkeypatch):
    out = ask.plan("q", RUN, client=FakeClient(usage={"prompt_tokens": 2_000_000, "completion_tokens": 1_000_000}))
    assert out["meta"]["cost"] == pytest.approx(2 * 0.15 + 0.60)
    monkeypatch.setenv("ASK_PRICE_IN", "1")
    monkeypatch.setenv("ASK_PRICE_OUT", "2")
    out = ask.plan("q", RUN, client=FakeClient(usage={"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}))
    assert out["meta"]["cost"] == pytest.approx(3.0) and out["meta"]["currency"] == "RUB"
    out = ask.plan("q", RUN, client=FakeClient(usage={"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.00042}))
    assert out["meta"]["cost"] == pytest.approx(0.00042)


def test_write_request_and_output():
    c = FakeClient(content=json.dumps({"text": " Лучший Answer F1 — 62,8 % у D001 [1]. "}))
    facts = {"kind": "best", "metric": "Answer F1", "rows": [{"cite": "[1]", "pid": "D001", "value": "62,8 %"}]}
    out = ask.write("Лучший Answer F1", "best", facts, "Лучшее значение Answer F1: 62,8 % у D001 [1].", client=c)
    assert out["text"] == "Лучший Answer F1 — 62,8 % у D001 [1]."
    (kw,) = c.calls
    assert kw["extra_body"]["provider"]["allow_fallbacks"] is False and kw["extra_body"]["reasoning"] == {"enabled": False}
    assert kw["response_format"]["json_schema"]["schema"] == ask.WRITE_SCHEMA
    assert '"cite":"[1]"' in kw["messages"][1]["content"] and "DRAFT:" in kw["messages"][1]["content"]
    for bad in ("", json.dumps({"text": "  "}), "не json"):
        with pytest.raises(ask.AskError) as e:
            ask.write("q", "best", facts, "d", client=FakeClient(content=bad))
        assert e.value.code == "llm_bad_output" and e.value.retryable


def test_input_validation_and_upstream_errors():
    c = FakeClient()
    for q in ("", "   ", "я" * 501, None):
        with pytest.raises(ask.AskError) as e:
            ask.plan(q, RUN, client=c)
        assert e.value.status == 400
    with pytest.raises(ask.AskError) as e:
        ask.plan("q", {"x": "я" * ask.MAX_RUN_CHARS}, client=c)
    assert e.value.status == 413
    with pytest.raises(ask.AskError) as e:
        ask.write("q", "best", {"x": "я" * ask.MAX_FACTS_CHARS}, "d", client=c)
    assert e.value.status == 413
    assert not c.calls
    with pytest.raises(ask.AskError) as e:
        ask.plan("q", RUN, client=FakeClient(exc=TimeoutError("slow")))
    assert e.value.code == "ml_error" and e.value.retryable


def test_prompt_for_big_run_stays_small():
    """Описание разбора на 40 статей и 40 метрик - небольшой промпт: LLM не видит строки (D014 = 28 649 строк)."""
    run = dict(RUN)
    run["papers"] = [{"pid": f"D{i:03d}", "title": "Some paper title about retrieval augmented generation"[:70],
                      "results": 100 + i, "issues": i % 9, "in_scope": True} for i in range(40)]
    run["metrics"] = [{"name": f"Metric number {i}@{i % 10}", "n": 1000 - i, "papers": 5} for i in range(40)]
    c = FakeClient()
    ask.plan("Лучший Answer F1 на HotpotQA", run, client=c)
    chars = sum(len(m["content"]) for m in c.calls[0]["messages"])
    assert chars < 12000, chars


def test_schema_matches_backend():
    src = SVC.parents[1] / "backend" / "src"
    if not (src / "faktura" / "ask" / "plan.py").exists():
        pytest.skip("backend недоступен")
    sys.path.insert(0, str(src))
    from faktura.ask.plan import plan_schema
    assert plan_schema() == ask.plan_schema()


# ---------- HTTP ----------
@pytest.fixture
def http(monkeypatch):
    from fastapi.testclient import TestClient
    import api
    client = FakeClient()
    monkeypatch.setattr(ask, "make_client", lambda: client)
    return TestClient(api.app), client


def test_http_plan_and_write(http):
    tc, client = http
    r = tc.post("/ask/plan", json={"question": "Лучший Answer F1", "run": RUN})
    assert r.status_code == 200 and r.json()["plan"]["intent"] == "best" and r.json()["meta"]["tokens_in"] == 1000
    client.content = json.dumps({"text": "Ответ [1]."})
    r = tc.post("/ask/write", json={"question": "q", "intent": "best", "facts": {"a": 1}, "draft": "Ответ [1]."})
    assert r.status_code == 200 and r.json()["text"] == "Ответ [1]."


def test_http_errors(http, monkeypatch):
    tc, client = http
    assert tc.post("/ask/plan", content=b"{", headers={"content-type": "application/json"}).status_code == 400
    r = tc.post("/ask/plan", json={"question": "", "run": RUN})
    assert r.status_code == 400 and r.json()["code"] == "bad_request" and r.json()["retryable"] is False
    client.exc = RuntimeError("boom")
    r = tc.post("/ask/plan", json={"question": "q", "run": RUN})
    assert r.status_code == 502 and r.json()["code"] == "ml_error" and r.json()["retryable"] is True
    monkeypatch.undo()
    monkeypatch.delenv("ROUTERAI_API_KEY", raising=False)
    r = tc.post("/ask/plan", json={"question": "q", "run": RUN})
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


# --- /llm/chat ---------------------------------------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "filter", "description": "d", "parameters": {"type": "object"}}}]


class ToolClient(FakeClient):
    def _create(self, **kw):
        self.calls.append(kw)
        tc = SimpleNamespace(id="c1", function=SimpleNamespace(name="filter", arguments='{"table":"results"}'))
        msg = SimpleNamespace(content=None, tool_calls=[tc])
        usage = SimpleNamespace(model_dump=lambda: {"prompt_tokens": 500, "completion_tokens": 20, "cost": 0.0002})
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="tool_calls")], usage=usage)


def test_chat_pins_provider_reasoning_off_and_returns_tool_calls():
    c = ToolClient()
    out = ask.chat([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}], TOOLS, client=c)
    (kw,) = c.calls
    assert kw["extra_body"]["provider"] == {"order": ["wafer"], "allow_fallbacks": False}
    assert kw["extra_body"]["reasoning"] == {"enabled": False}
    assert kw["tools"] == TOOLS and kw["tool_choice"] == "auto" and kw["max_tokens"] == ask.CHAT_DEFAULT_TOKENS
    tc = out["message"]["tool_calls"][0]
    assert tc["function"] == {"name": "filter", "arguments": '{"table":"results"}'} and out["finish_reason"] == "tool_calls"
    assert out["meta"]["cost"] == 0.0002 and out["meta"]["tokens_in"] == 500


def test_chat_text_answer_and_reasoning_option(monkeypatch):
    c = FakeClient(content="Ответ")
    out = ask.chat([{"role": "user", "content": "q"}], None, reasoning=256, client=c)
    assert out["message"] == {"role": "assistant", "content": "Ответ"}
    assert c.calls[0]["extra_body"]["reasoning"] == {"max_tokens": 256} and "tools" not in c.calls[0]
    monkeypatch.setenv("ASK_AGENT_MODEL", "vendor/agent")
    c2 = FakeClient(content="x")
    ask.chat([{"role": "user", "content": "q"}], client=c2)
    assert c2.calls[0]["model"] == "vendor/agent"


def test_chat_refuses_without_pinned_provider_and_bad_input(monkeypatch):
    monkeypatch.setenv("ASK_PROVIDER", " , ")
    c = FakeClient()
    with pytest.raises(ask.AskError) as e:
        ask.chat([{"role": "user", "content": "q"}], client=c)
    assert e.value.code == "not_configured" and not c.calls
    monkeypatch.delenv("ASK_PROVIDER")
    for bad in ([], [{"role": "x", "content": "q"}], "q"):
        with pytest.raises(ask.AskError) as e:
            ask.chat(bad, client=c)
        assert e.value.status == 400
    with pytest.raises(ask.AskError):
        ask.chat([{"role": "user", "content": "q"}], max_tokens=99999, client=c)
    with pytest.raises(ask.AskError) as e:
        ask.chat([{"role": "user", "content": "x" * (ask.CHAT_MAX_CHARS + 1)}], client=c)
    assert e.value.status == 413 and not c.calls


def test_chat_provider_error_is_retryable_ml_error():
    with pytest.raises(ask.AskError) as e:
        ask.chat([{"role": "user", "content": "q"}], client=FakeClient(exc=TimeoutError("t")))
    assert e.value.code == "ml_error" and e.value.retryable
