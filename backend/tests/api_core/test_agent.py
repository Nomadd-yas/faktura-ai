"""Агентный чат внутри разбора (CORE_API §4j): цикл с инструментами, проверка чисел, SSE, сообщения, стоп, оценка.
Реальный LLM не вызывается: модель - сценарий (ScriptedLLM) или мок ML-сервера."""
from __future__ import annotations

import json
import sys
import threading
import time
import types

import pytest

from faktura.agent import store
from faktura.agent.tools import Toolbox
from faktura.agent.verify import Verifier
from faktura.api.corpus.views import corpus_id_of
from faktura.ask.client import AskUnavailable
from faktura.ask.context import PaperData, RunCtx, load_ctx

from .conftest import API


# ---------------- сценарная модель ----------------
def call(name, cid="c1", **args):
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def say(text):
    return {"role": "assistant", "content": text}


def rows_of(messages):
    return json.loads(messages[-1]["content"])["rows"]


def num(v):
    return str(v).replace(".", ",")


class ScriptedLLM:
    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []

    def chat(self, messages, tools=None, *, max_tokens=1200, tool_choice=None, timeout_s=None):
        self.calls.append({"messages": list(messages), "tools": tools, "max_tokens": max_tokens})
        t = self.turns.pop(0) if self.turns else say("Данных нет.")
        if isinstance(t, Exception):
            raise t
        msg = t(messages) if callable(t) else t
        return {"message": msg, "finish_reason": "stop",
                "meta": {"model": "fake", "provider": "fake", "tokens_in": 1000, "tokens_out": 50, "cost": 0.0002}}


@pytest.fixture
def actx(app):
    c = app.state.faktura
    c.ask_client = None
    c.llm_client = None
    return c


@pytest.fixture
def auth_b(client, services) -> dict[str, str]:
    from faktura.domain.security import hash_password
    from faktura.storage import DEFAULT_WORKSPACE_ID

    with services.db.uow() as uow:
        uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email="b@example.com", name="B",
                         password_hash=hash_password("b-password-123"), is_admin=False)
    r = client.post(f"{API}/auth/login", json={"email": "b@example.com", "password": "b-password-123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def sse_events(text):
    out = []
    for block in text.strip().split("\n\n"):
        if block.startswith(":") or not block.strip():
            continue
        ev = next(x[7:] for x in block.splitlines() if x.startswith("event: "))
        data = json.loads(next(x[6:] for x in block.splitlines() if x.startswith("data: ")))
        out.append((ev, data))
    return out


def ask_sse(client, auth, rid, text):
    r = client.post(f"{API}/runs/{rid}/messages", json={"text": text}, headers={**auth, "Accept": "text/event-stream"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return sse_events(r.text)


def done_of(events):
    return next(d for e, d in events if e == "done")["answer"]


def messages(client, auth, rid):
    return client.get(f"{API}/runs/{rid}/messages", headers=auth).json()["data"]


def wait_status(client, auth, rid, mid, want, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = next(x for x in messages(client, auth, rid) if x["id"] == mid)
        if m["status"] in want:
            return m
        time.sleep(0.05)
    raise AssertionError("ответ не дошёл до статуса")


def best_pce(client, auth, rid):
    s = client.get(f"{API}/runs/{rid}/summary", headers=auth).json()["best_values"]["PCE"]
    return s


# ---------------- основной цикл ----------------
def test_tool_loop_filter_then_verified_answer_with_footnotes(client, auth, run_two, actx):
    def final(messages):
        a, b = rows_of(messages)[:2]
        return say(f"Выше 15 % у перовскитов: {num(a['value'])} % [[fact:{a['fact_id']}]] и "
                   f"{num(b['value'])} % [[fact:{b['fact_id']}]]. Источник - стр. {a['source_page']}.")

    llm = actx.llm_client = ScriptedLLM(
        call("filter", table="results", where=[{"col": "metric_name", "op": "=", "value": "PCE"},
                                                {"col": "value", "op": ">", "value": 15}], order_by="-value", limit=2),
        final)
    ev = ask_sse(client, auth, run_two["id"], "КПД выше 15 % у перовскитов")
    names = [e for e, _ in ev]
    # порядок: step* -> text* -> card* -> cite* -> suggestions -> done
    order = {"step": 0, "text": 1, "card": 2, "cite": 3, "suggestions": 4, "done": 5}
    assert [order[n] for n in names] == sorted(order[n] for n in names) and names[-1] == "done"
    steps = [d["text"] for e, d in ev if e == "step"]
    assert steps[0] == "Разбираю вопрос" and any(s.startswith("Запрос к таблице results") and "value > 15" in s for s in steps)
    assert "Сверяю с источниками" in steps
    ans = done_of(ev)
    assert "[[" not in ans["text"] and "[1]" in ans["text"] and "[2]" in ans["text"]
    assert "".join(d["text"] for e, d in ev if e == "text") == ans["text"]
    assert [c["n"] for c in ans["cites"]] == [1, 2]
    c0 = ans["cites"][0]
    assert c0["domain"] == "solar" and c0["pid"].startswith("D00") and c0["result_id"].startswith("R") and c0["page"]
    assert c0["fragment"] and c0["issue_id"] is None
    assert ans["cards"][0]["kind"] == "results" and len(ans["cards"][0]["rows"]) == 2
    assert [d["cite"] for e, d in ev if e == "cite"] == ans["cites"]
    assert ans["suggestions"] and "КПД выше 15 % у перовскитов" not in ans["suggestions"]
    # модель получила инструменты и системную подсказку на английском с описанием разбора
    c1 = llm.calls[0]
    assert {t["function"]["name"] for t in c1["tools"]} >= {"schema", "filter", "group", "top", "get_row", "get_issues",
                                                              "comparable", "search_text", "read_page", "clarify"}
    assert c1["messages"][0]["role"] == "system" and "ONLY from tool results" in c1["messages"][0]["content"]
    assert c1["messages"][-1] == {"role": "user", "content": "КПД выше 15 % у перовскитов"}


def test_unverified_number_dropped_rest_kept(client, auth, run_two, actx, services):
    actx.llm_client = ScriptedLLM(
        call("group", table="results", by=["metric_name", "unit"]),
        say("Значений PCE в разборе: 16 [[agg:a1]].\nСреднее PCE равно 99,9 % [[agg:a1]].\nВсего PCE: 16 [[agg:a1]].\nЛучший КПД 20 % у D001."))
    ans = done_of(ask_sse(client, auth, run_two["id"], "Сколько значений PCE"))
    assert "16" in ans["text"] and "99,9" not in ans["text"] and "20 %" not in ans["text"]  # неверное и без маркера - прочь
    assert len(ans["cites"]) == 1 and ans["cites"][0]["agg_id"] == "a1" and "fallback" not in ans
    assert ans["cites"][0]["result_ids"] and "16" in ans["cites"][0]["text"]
    mid = [m for m in messages(client, auth, run_two["id"]) if m["role"] == "assistant"][0]["id"]
    with services.db.uow() as uow:
        m = store.get(uow, corpus_id_of(run_two["id"]), mid)
    ver = [x for x in m["tool_log"] if "verification" in x][0]["verification"]
    assert ver["dropped"] == 2 and {d["item"][:8] for d in ver["items_dropped"]} == {"Среднее ", "Лучший К"}
    assert m["meta"]["mode"] == "agent" and m["meta"]["tool_calls"] == 1 and m["cost"] == pytest.approx(0.0004) and m["cost_currency"] == "RUB"


def test_mostly_unverified_falls_back_to_base_answer(client, auth, run_two, actx):
    actx.llm_client = ScriptedLLM(say("Лучший КПД 99,9 % у D001.\nВторой результат 98 %."))
    ans = done_of(ask_sse(client, auth, run_two["id"], "Лучший КПД"))
    assert ans["fallback"] == "verification" and "Ответ собран по шаблонам" in ans["text"]
    best = best_pce(client, auth, run_two["id"])
    assert ans["cards"][0]["kind"] == "results" and ans["cites"][0]["result_id"] == best["result_id"]
    assert ans["cites"][0]["n"] == 1 and ans["cites"][0]["pid"] == best["pid"] and ans["cites"][0]["fragment"]
    assert "99,9" not in ans["text"]


def test_llm_down_and_no_llm_fall_back_to_base(client, auth, run_two, actx):
    actx.llm_client = ScriptedLLM(AskUnavailable("down"))
    ans = done_of(ask_sse(client, auth, run_two["id"], "Лучший КПД"))
    assert ans["fallback"] == "llm_unavailable" and ans["cites"]
    actx.llm_client = None  # агент выключен: базовый ответ без пометки
    ans2 = done_of(ask_sse(client, auth, run_two["id"], "Лучший КПД"))
    assert "fallback" not in ans2 and "шаблонам" not in ans2["text"] and ans2["cites"]


def test_clarify_flow_and_history(client, auth, run_two, actx):
    rid = run_two["id"]
    llm = actx.llm_client = ScriptedLLM(call("clarify", question="Какой показатель сравнить?",
                                             options=["КПД (PCE)", "Voc", "Jsc"]))
    ev = ask_sse(client, auth, rid, "Сравни приборы")
    done = next(d for e, d in ev if e == "done")
    assert done["clarify"] is True and done["answer"]["clarify"] is True
    assert done["answer"]["text"] == "Какой показатель сравнить?" and done["answer"]["suggestions"] == ["КПД (PCE)", "Voc", "Jsc"]
    assert not done["answer"]["cites"] and [e for e, _ in ev if e == "text"] == ["text"]
    m = [x for x in messages(client, auth, rid) if x["role"] == "assistant"][0]
    assert m["clarify"] is True and m["kind"] == "answer" and m["status"] == "done"
    # ответ на уточнение: прошлый обмен (включая вопрос-уточнение) уходит модели
    ask_sse(client, auth, rid, "КПД (PCE)")
    msgs = llm.calls[-1]["messages"]
    assert [x["role"] for x in msgs[1:]] == ["user", "assistant", "user"]
    assert msgs[1]["content"] == "Сравни приборы" and msgs[2]["content"] == "Какой показатель сравнить?"
    assert msgs[3]["content"] == "КПД (PCE)"


def test_history_keeps_last_six_qa_truncated(client, auth, run_two, actx):
    long = "Данных нет. " + "очень длинный ответ " * 100
    actx.llm_client = llm = ScriptedLLM(*[say(long) for _ in range(8)], say("ок"))
    for i in range(8):
        ask_sse(client, auth, run_two["id"], f"вопрос {i}")
    ask_sse(client, auth, run_two["id"], "последний")
    msgs = llm.calls[-1]["messages"][1:]
    assert len(msgs) == 13 and msgs[0]["content"] == "вопрос 2" and msgs[-1]["content"] == "последний"
    assert all(len(m["content"]) <= 601 for m in msgs)


def test_tool_call_limit_forces_final_answer(client, auth, run_two, actx):
    llm = actx.llm_client = ScriptedLLM(*[call("schema", cid=f"c{i}") for i in range(12)], say("Данных нет."))
    ans = done_of(ask_sse(client, auth, run_two["id"], "что угодно"))
    assert len(llm.calls) == 13 and llm.calls[-1]["tools"] is None and llm.calls[0]["tools"] is not None
    assert ans["text"] == "Данных нет."


def test_parallel_tool_calls_over_limit_get_error_result(client, auth, run_two, actx):
    many = {"role": "assistant", "content": "", "tool_calls": [
        {"id": f"c{i}", "type": "function", "function": {"name": "schema", "arguments": "{}"}} for i in range(15)]}
    llm = actx.llm_client = ScriptedLLM(many, say("Данных нет."))
    done_of(ask_sse(client, auth, run_two["id"], "q"))
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 15 and "limit reached" in tool_msgs[-1]["content"] and "limit" not in tool_msgs[0]["content"]


def test_partial_run_note(client, auth, run_two, actx, monkeypatch):
    from faktura.agent import service

    monkeypatch.setattr(service, "_partial", lambda uow, corpus: {"done": 2, "total": 5})
    actx.llm_client = ScriptedLLM(say("Данных нет."))
    ans = done_of(ask_sse(client, auth, run_two["id"], "q"))
    assert ans["partial"] == {"done": 2, "total": 5} and "ещё идёт: ответ по 2 из 5" in ans["text"]


# ---------------- сообщения, 202, стоп, оценка ----------------
def test_get_messages_after_answer(client, auth, run_two, actx):
    rid = run_two["id"]
    m0 = messages(client, auth, rid)
    assert [m["kind"] for m in m0] == ["files", "run"] and m0[0]["role"] == "user" and m0[1]["role"] == "system"
    assert set(m0[0]["files"]) == {"D001", "D002"} and m0[0]["at"]
    actx.llm_client = ScriptedLLM(call("top", table="results", col="value", n=1,
                                       where=[{"col": "metric_name", "op": "=", "value": "PCE"}]),
                                  lambda ms: say(f"Лучший КПД {num(rows_of(ms)[0]['value'])} % [[fact:{rows_of(ms)[0]['fact_id']}]]."))
    ans = done_of(ask_sse(client, auth, rid, "Лучший КПД"))
    m = messages(client, auth, rid)
    assert [x["kind"] for x in m] == ["files", "run", "text", "answer"]
    assert m[2]["role"] == "user" and m[2]["text"] == "Лучший КПД"
    assert m[3]["role"] == "assistant" and m[3]["status"] == "done" and m[3]["answer"] == ans and m[3]["rating"] is None
    assert ans["cites"][0]["result_id"].startswith("R") and ans["cites"][0]["pid"] in ("D001", "D002")


def test_202_mode_stream_url_replay_with_access_token(client, auth, run_two, actx):
    actx.llm_client = ScriptedLLM(say("Данных нет."))
    r = client.post(f"{API}/runs/{run_two['id']}/messages", json={"text": "вопрос"}, headers=auth)
    assert r.status_code == 202
    body = r.json()
    assert body["stream_url"] == f"{API}/runs/{run_two['id']}/messages/{body['message_id']}/stream"
    token = auth["Authorization"].split()[1]
    s = client.get(body["stream_url"], params={"access_token": token})
    assert s.status_code == 200 and sse_events(s.text)[-1][0] == "done"
    assert client.get(body["stream_url"]).status_code == 401
    wait_status(client, auth, run_two["id"], body["message_id"], {"done"})
    again = client.get(body["stream_url"], headers=auth)  # после завершения: воспроизведение из сохранённого ответа
    assert sse_events(again.text)[-1][1]["answer"]["text"] == "Данных нет."


def test_stop_running_answer(client, auth, run_two, actx):
    rid = run_two["id"]
    entered, release = threading.Event(), threading.Event()

    def slow(messages):
        entered.set()
        assert release.wait(10)
        return call("schema")

    llm = actx.llm_client = ScriptedLLM(slow, say("не должно дойти"))
    r = client.post(f"{API}/runs/{rid}/messages", json={"text": "долгий вопрос"}, headers=auth)
    mid = r.json()["message_id"]
    assert entered.wait(10)
    busy = client.post(f"{API}/runs/{rid}/messages", json={"text": "ещё"}, headers=auth)  # пока ответ пишется
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "turn_busy"
    s = client.post(f"{API}/runs/{rid}/messages/{mid}/stop", headers=auth)
    assert s.status_code == 200 and s.json()["status"] == "stopped"
    release.set()
    token = auth["Authorization"].split()[1]
    ev = sse_events(client.get(r.json()["stream_url"], params={"access_token": token}).text)
    assert ev[-1][0] == "done" and ev[-1][1]["status"] == "stopped"
    assert len(llm.calls) == 1  # цикл остановился между шагами: инструмент не вызван, второго хода нет
    m = wait_status(client, auth, rid, mid, {"stopped"})
    assert m["status"] == "stopped" and m["answer"]["text"] == "Ответ остановлен."
    again = client.post(f"{API}/runs/{rid}/messages/{mid}/stop", headers=auth)  # повторный стоп безвреден
    assert again.status_code == 200 and again.json()["status"] == "stopped"


def test_stop_finished_answer_keeps_it(client, auth, run_two, actx):
    actx.llm_client = ScriptedLLM(say("Данных нет."))
    ask_sse(client, auth, run_two["id"], "q")
    mid = [m for m in messages(client, auth, run_two["id"]) if m["role"] == "assistant"][0]["id"]
    s = client.post(f"{API}/runs/{run_two['id']}/messages/{mid}/stop", headers=auth).json()
    assert s["status"] == "done" and s["answer"]["text"] == "Данных нет."


def test_rating(client, auth, run_two, actx):
    rid = run_two["id"]
    actx.llm_client = ScriptedLLM(say("Данных нет."))
    ask_sse(client, auth, rid, "q")
    mid = [m for m in messages(client, auth, rid) if m["role"] == "assistant"][0]["id"]
    url = f"{API}/runs/{rid}/messages/{mid}/rating"
    assert client.put(url, json={"rating": "up"}, headers=auth).json()["rating"] == "up"
    r = client.put(url, json={"rating": "down", "reason": "Неверная цифра"}, headers=auth).json()
    assert r["rating"] == "down" and r["rating_reason"] == "Неверная цифра"
    assert [m for m in messages(client, auth, rid) if m["id"] == mid][0]["rating"] == "down"
    assert client.put(url, json={"rating": "up", "reason": "Неверная цифра"}, headers=auth).status_code == 400
    assert client.put(url, json={"rating": "meh"}, headers=auth).status_code == 400
    assert client.put(url, json={"rating": "down", "reason": "скучно"}, headers=auth).status_code == 400
    cleared = client.put(url, json={"rating": None}, headers=auth).json()
    assert cleared["rating"] is None and cleared["rating_reason"] is None


def test_validation_rate_limit_and_scope_isolation(client, auth, auth_b, run_two, actx):
    rid = run_two["id"]
    assert client.post(f"{API}/runs/{rid}/messages", json={"text": "  "}, headers=auth).status_code == 400
    r = client.post(f"{API}/runs/{rid}/messages", json={"text": "я" * 501}, headers=auth)
    assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"
    assert client.post(f"{API}/runs/{rid}/messages", json={"text": "q"}).status_code == 401
    actx.llm_client = ScriptedLLM(say("Данных нет."))
    ask_sse(client, auth, rid, "свой вопрос")
    mid = [m for m in messages(client, auth, rid) if m["role"] == "assistant"][0]["id"]
    # чужой разбор не виден никому, кроме владельца
    for method, url, kw in [("get", f"/runs/{rid}/messages", {}), ("post", f"/runs/{rid}/messages", {"json": {"text": "q"}}),
                            ("get", f"/runs/{rid}/messages/{mid}/stream", {}), ("post", f"/runs/{rid}/messages/{mid}/stop", {}),
                            ("put", f"/runs/{rid}/messages/{mid}/rating", {"json": {"rating": "up"}}),
                            ("get", f"/runs/{rid}/suggestions", {})]:
        assert getattr(client, method)(API + url, headers=auth_b, **kw).status_code == 404, url
    assert client.get(f"{API}/runs/run_nope/messages", headers=auth).status_code == 404
    assert client.post(f"{API}/runs/{rid}/messages/msg_nope/stop", headers=auth).status_code == 404
    actx.chat_limiter.limit = 1
    r = client.post(f"{API}/runs/{rid}/messages", json={"text": "ещё"}, headers=auth)
    assert r.status_code == 429


def test_suggestions_do_not_repeat_asked(client, auth, run_two, actx):
    rid = run_two["id"]
    first = client.get(f"{API}/runs/{rid}/suggestions", headers=auth).json()["data"]
    assert "Лучший КПД" in first and 3 <= len(first) <= 5
    ask_sse(client, auth, rid, "лучший кпд!")
    after = client.get(f"{API}/runs/{rid}/suggestions", headers=auth).json()["data"]
    assert "Лучший КПД" not in after and len(after) >= 2


# ---------------- данные: отклонённые, исправленные ----------------
def test_rejected_invisible_corrected_value_verified(client, auth, run_two, actx):
    rid = run_two["id"]
    top = best_pce(client, auth, rid)
    fid = f"{top['pid']}/{top['result_id']}"
    url = f"{API}/runs/{rid}/papers/solar/{top['pid']}/results/{top['result_id']}/review"
    assert client.patch(url, json={"status": "rejected"}, headers=auth).status_code == 200
    llm = actx.llm_client = ScriptedLLM(
        call("top", table="results", col="value", n=5, where=[{"col": "metric_name", "op": "=", "value": "PCE"}]),
        say(f"Лучший КПД {num(top['value'])} % [[fact:{fid}]]."))  # ссылка на отклонённый факт
    ans = done_of(ask_sse(client, auth, rid, "Лучший КПД"))
    seen = json.loads(llm.calls[1]["messages"][-1]["content"])["rows"]
    assert fid not in [r["fact_id"] for r in seen] and seen  # инструмент отклонённое не видит
    assert ans["fallback"] == "verification"  # а маркер на него не проходит проверку
    new = seen[0]
    pid, rid_ = new["fact_id"].split("/")
    client.patch(f"{API}/runs/{rid}/papers/solar/{pid}/results/{rid_}/review",
                 json={"status": "accepted", "corrected_value": 77.7}, headers=auth)
    actx.llm_client = ScriptedLLM(
        call("top", table="results", col="value", n=1, where=[{"col": "metric_name", "op": "=", "value": "PCE"}]),
        lambda ms: say(f"Лучший КПД {num(rows_of(ms)[0]['value'])} % [[fact:{rows_of(ms)[0]['fact_id']}]]."))
    ans = done_of(ask_sse(client, auth, rid, "Лучший КПД ещё раз"))
    assert "77,7" in ans["text"] and ans["cites"][0]["result_id"] == rid_
    # старое значение (до правки) не проходит проверку
    actx.llm_client = ScriptedLLM(
        call("top", table="results", col="value", n=1, where=[{"col": "metric_name", "op": "=", "value": "PCE"}]),
        lambda ms: say(f"Лучший КПД {num(new['value'])} % [[fact:{rows_of(ms)[0]['fact_id']}]]."))
    ans = done_of(ask_sse(client, auth, rid, "Лучший КПД в третий раз"))
    assert ans["fallback"] == "verification"


# ---------------- текст статей через textindex ----------------
@pytest.fixture
def fake_textindex(monkeypatch):
    mod = types.ModuleType("faktura.textindex")
    mod.seen = []
    text = "The champion device reached a power conversion efficiency of 23.5% under AM1.5G."

    def search_text(uow, corpus_id, query, *, pids=None, k=8):
        mod.seen.append(("search", corpus_id, query, tuple(pids or ()), k))
        return [{"chunk_id": "chk_1", "pid": "D001", "page": 4, "text": text, "score": 0.9},
                {"chunk_id": "chk_x", "pid": "D999", "page": 1, "text": "чужая статья", "score": 0.8}]

    def read_page(uow, corpus_id, pid, page):
        mod.seen.append(("page", pid, page))
        return {"pid": pid, "page": page, "text": text, "pages": 9} if pid == "D001" else None

    mod.search_text, mod.read_page = search_text, read_page
    monkeypatch.setitem(sys.modules, "faktura.textindex", mod)
    return mod


def test_text_search_and_read_page_tools_with_chunk_cite(client, auth, run_two, actx, fake_textindex):
    quote = "power conversion efficiency of 23.5%"
    llm = actx.llm_client = ScriptedLLM(
        call("search_text", query="champion device efficiency"),
        call("read_page", pid="D001", page=4),
        say(f"В тексте D001 сказано: КПД 23,5 % [[chunk:chk_1|{quote}]]."))
    ev = ask_sse(client, auth, run_two["id"], "Где написано про КПД лучшего прибора")
    ans = done_of(ev)
    s = json.loads(llm.calls[1]["messages"][-1]["content"])["results"]
    assert [x["chunk_id"] for x in s] == ["chk_1"]  # хит чужой статьи отброшен
    kind, corpus_id, q, pids, k = fake_textindex.seen[0]
    assert q == "champion device efficiency" and pids == ("D001", "D002") and corpus_id == corpus_id_of(run_two["id"])
    assert fake_textindex.seen[1] == ("page", "D001", 4)
    assert ans["cites"][0] == {"n": 1, "domain": "solar", "pid": "D001", "result_id": None, "issue_id": None, "page": 4,
                               "fragment": quote, "chunk_id": "chk_1"}
    texts = [d["text"] for e, d in ev if e == "step"]
    assert "Ищу в тексте статей: champion device efficiency" in texts and "Читаю страницу 4 статьи D001" in texts
    # выдуманная цитата -> пункт отброшен
    actx.llm_client = ScriptedLLM(call("search_text", query="x"), say("Прибор дал КПД 23,5 % [[chunk:chk_1|efficiency was 99%]]."))
    assert done_of(ask_sse(client, auth, run_two["id"], "q2"))["fallback"] == "verification"
    # chunk, которого инструмент не выдавал
    actx.llm_client = ScriptedLLM(say("Прибор дал КПД 23,5 % [[chunk:chk_1]]."))
    assert done_of(ask_sse(client, auth, run_two["id"], "q3"))["fallback"] == "verification"


def test_text_tools_without_textindex_return_nothing(client, auth, run_two, actx, monkeypatch):
    monkeypatch.setitem(sys.modules, "faktura.textindex", None)  # import даёт ImportError
    llm = actx.llm_client = ScriptedLLM(call("search_text", query="x"), call("read_page", pid="D001", page=1), say("Данных нет."))
    assert done_of(ask_sse(client, auth, run_two["id"], "q"))["text"] == "Данных нет."
    assert json.loads(llm.calls[1]["messages"][-1]["content"])["results"] == []
    assert json.loads(llm.calls[2]["messages"][-1]["content"])["found"] is False


# ---------------- мок ML-сервера ----------------
def test_mock_ml_llm_chat_and_end_to_end(client, auth, run_two, actx):
    from fastapi.testclient import TestClient

    from faktura.agent.llm import HttpLlmClient
    from faktura.ml.mock_server import create_mock_app

    ml = TestClient(create_mock_app())
    first = ml.post("/llm/chat", json={"messages": [{"role": "user", "content": "q"}], "tools": [{"type": "function"}]}).json()
    assert first["message"]["tool_calls"][0]["function"]["name"] == "filter" and first["meta"]["cost"] == 0.0 and first["meta"]["currency"] == "RUB"
    assert ml.post("/llm/chat", json={"messages": []}).status_code == 400
    actx.llm_client = HttpLlmClient("http://testserver", http=ml)
    ans = done_of(ask_sse(client, auth, run_two["id"], "Покажи любое значение"))
    assert "[1]" in ans["text"] and ans["cites"][0]["result_id"] and "fallback" not in ans


def test_http_llm_client_errors_become_unavailable():
    from faktura.agent.llm import HttpLlmClient

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            if self._d is ValueError:
                raise ValueError
            return self._d

    class Http:
        def __init__(self, r):
            self.r, self.last = r, None

        def post(self, url, json=None, timeout=None):
            self.last = (url, json)
            if isinstance(self.r, Exception):
                raise self.r
            return self.r

    for bad in (Resp(502, {}), Resp(200, ValueError), Resp(200, []), Resp(200, {"x": 1}), TimeoutError()):
        with pytest.raises(AskUnavailable):
            HttpLlmClient("http://ml/", http=Http(bad)).chat([{"role": "user", "content": "q"}])
    http = Http(Resp(200, {"message": {"role": "assistant", "content": "ok"}}))
    out = HttpLlmClient("http://ml/", http=http).chat([{"role": "user", "content": "q"}], [{"type": "function"}], max_tokens=50)
    assert out["message"]["content"] == "ok" and http.last[0] == "http://ml/llm/chat"
    assert http.last[1]["max_tokens"] == 50 and http.last[1]["tools"] and http.last[1]["temperature"] == 0.0


# ---------------- инструменты и проверка на синтетических данных ----------------
def _rag_ctx():
    def res(rid, name, val, unit, page="3", k="", **kw):
        return {"result_id": rid, "metric_name": name, "metric_value": val, "normalized_value": val, "normalized_unit": unit,
                "metric_unit": unit, "source_page": page, "source_fragment": f"{name} = {val}", "extraction_status": "extracted",
                "metric_k": k, "dataset_id": "DS1", "task_id": "T1", **kw}

    tables = {"publications": [{"publication_id": "P1", "title": "Paper one", "year": "2023", "in_scope": "true"}],
              "datasets": [{"dataset_id": "DS1", "dataset_name": "HotpotQA", "data_split": "dev"}],
              "tasks": [{"task_id": "T1", "task_type": "multi-hop QA"}],
              "results": [res("R1", "Recall", "0.74", "fraction", k="5"), res("R2", "EM", "41.5", "%"),
                          res("R3", "EM", "39.0", "%", duplicate_of="R2"), res("R4", "Latency", "120", "ms")],
              "issues": [{"issue_id": "I1", "check_name": "plot_precision", "description": "Values differ by 2 points",
                          "source_page": "4"}]}
    t2 = {"publications": [{"publication_id": "P2", "title": "Paper two", "year": "2024", "in_scope": "true"}],
          "results": [res("R1", "EM", "50.0", "%", dataset_id="DS2")], "datasets": [{"dataset_id": "DS2", "dataset_name": "NQ"}]}
    return RunCtx("rag", [PaperData("D001", "P1", tables, tables["issues"], set()), PaperData("D002", "P2", t2, [], set())])


def test_tools_filter_group_top_scope_and_errors():
    tb = Toolbox(_rag_ctx())
    r = tb.filter(table="results", where=[{"col": "metric_name", "op": "=", "value": "EM"}])
    assert r["total"] == 2 and {x["fact_id"] for x in r["rows"]} == {"D001/R2", "D002/R1"}  # дубликат скрыт
    assert tb.filter(table="results", where=[{"col": "value", "op": ">=", "value": 50}])["total"] == 2  # 50.0 и 120
    assert tb.filter(table="results", where=[{"col": "metric_k", "op": "is_null", "value": True}])["total"] == 3
    assert tb.filter(table="results", where=[{"col": "metric_name", "op": "in", "value": ["Recall", "EM"]}])["total"] == 3
    assert tb.filter(table="results", where=[{"col": "dataset_name", "op": "contains", "value": "hotpot"}])["total"] == 3
    assert tb.filter(table="results", where=[{"col": "pid", "op": "!=", "value": "D001"}])["total"] == 1
    assert tb.filter(table="results", limit=100000)["returned"] <= 200 and Toolbox._limit(99999) == 200
    g = tb.group(table="results", by=["metric_name", "unit"], agg=[{"col": "value", "fn": "mean"}])
    em = next(x for x in g["groups"] if x["metric_name"] == "EM")
    mean = next(a for a in em["aggs"] if a["fn"] == "mean")
    assert mean["value"] == 45.75 and mean["agg_id"] in tb.aggs and tb.aggs[mean["agg_id"]]["ids"] == ["D001/R2", "D002/R1"]
    assert em["ids"] == ["D001/R2", "D002/R1"] and "warning" not in em
    assert "warning" in tb.group(table="results", by=[], agg=[{"col": "value", "fn": "max"}])["groups"][0]  # смесь единиц
    top = tb.top(table="results", col="value", n=1, where=[{"col": "metric_name", "op": "=", "value": "EM"}])
    assert top["rows"][0]["fact_id"] == "D002/R1"
    low = tb.top(table="results", col="value", n=1, dir="asc", where=[{"col": "metric_name", "op": "=", "value": "EM"}])
    assert low["rows"][0]["fact_id"] == "D001/R2"
    assert "unknown column" in tb.call("filter", {"table": "results", "where": [{"col": "nope", "op": "=", "value": 1}]})[0]["error"]
    assert "unknown op" in tb.call("filter", {"table": "results", "where": [{"col": "pid", "op": "~", "value": 1}]})[0]["error"]
    assert "not in this run" in tb.call("filter", {"table": "results", "pids": ["D777"]})[0]["error"]
    assert "unknown tool" in tb.call("rm_rf", {})[0]["error"]
    gr = tb.get_row(fact_id="D001/R1")["row"]
    assert gr["source_page"] == "3" and gr["source_fragment"] == "Recall = 0.74" and gr["title"] == "Paper one"
    assert tb.get_row(fact_id="D001/R999")["found"] is False
    iss = tb.get_issues(pids=["D001"])
    assert iss["total"] == 1 and iss["rows"][0]["id"] == "D001/I1"
    sch = tb.schema()
    assert sch["domain"] == "rag" and {t["table"] for t in sch["tables"]} >= {"results", "issues", "datasets"}
    assert any(m["metric"] == "Recall" and m["units"] == ["fraction"] for m in sch["metrics"])
    cmp_ = tb.comparable(fact_ids=["D001/R2", "D002/R1"])
    assert cmp_["comparable"] is False and "dataset" in cmp_["differs_in"]
    assert tb.comparable(fact_ids=["D001/R2", "D001/R2"])["comparable"] is True


def test_verifier_rules():
    tb = Toolbox(_rag_ctx())
    tb.group(table="results", by=["metric_name", "unit"], agg=[{"col": "value", "fn": "mean"}])
    v = Verifier(tb, "Какой Recall на HotpotQA при k = 7?")

    def ok(t):
        return v.check_item(t) is None

    assert ok("Recall равен 0,74 [[fact:D001/R1]].") and ok("Recall равен 74 % [[fact:D001/R1]].")  # доля = процент
    assert ok("Recall@5 равен 0.74 [[fact:D001/R1]]") and ok("k = 5 [[fact:D001/R1]]")
    assert not ok("Recall равен 0,75 [[fact:D001/R1]].") and not ok("Recall равен 0,74.")  # неверное / без маркера
    assert not ok("EM равен 41,5 [[fact:D001/R99]].")  # факта нет в области
    assert ok("EM равен 41,5 % [[fact:D001/R2]]") and ok("EM около 42 % [[fact:D001/R2]]")
    assert not ok("EM 41,5 % [[fact:D002/R1]]")
    assert not ok("EM равен 4,15 % [[fact:D001/R2]]") and not ok("Latency 0,12 с [[fact:D001/R4]]")  # единицы сам не меняет
    assert ok("Latency 120 ms [[fact:D001/R4]]") and not ok("Latency 120000 [[fact:D001/R4]]")
    assert ok("В 2023 году, на стр. 5, в табл. 2 и рис. 3 это 1-й результат; k = 7 из вопроса.")  # исключения без маркеров
    assert ok("Версия GPT-4 и Recall@10 это названия, а 1) пункт списка.") is False  # Recall@10 без маркера: число 10 проверяется
    assert not ok("Всего 3 значения.")
    ag = next(a for a in tb.aggs.values() if a["fn"] == "mean" and a["value"] == 45.75)["id"]
    assert ok(f"Среднее EM 45,75 % [[agg:{ag}]] по 2 значениям") and not ok("Среднее EM 45,75 % [[agg:a99]]")
    assert not ok(f"Среднее EM 46,75 % [[agg:{ag}]]")
    assert ok("Замечание на стр. 4: Values differ [[issue:D001/I1]]") and not ok("Замечание [[issue:D001/I9]]")
    assert ok("Сплит dev [[row:datasets/D001/DS1]]") and not ok("Цитата 5 [[chunk:nope]]")
    # режим mark: пункт остаётся с пометкой, сноски у него нет
    vm = Verifier(tb, "q", "mark").run("Recall 0,99 [[fact:D001/R1]].")
    assert vm.text.endswith("(не подтверждено)") and "[1]" not in vm.text and vm.unverified and not vm.cites
    # режим drop: заголовок списка без пунктов уходит вместе с ними
    vd = Verifier(tb, "q").run("Значения:\n- EM 99 [[fact:D001/R2]]\n- Recall 0,74 [[fact:D001/R1]]")
    assert vd.text == "Значения:\n- Recall 0,74 [1]" and vd.items_total == 2 and vd.items_dropped == 1 and not vd.too_many_dropped
    vd2 = Verifier(tb, "q").run("Значения:\n- EM 99 [[fact:D001/R2]]")
    assert vd2.text == "" and vd2.too_many_dropped
    # одна сноска на повторные ссылки; [n] подряд склеиваются
    vr = Verifier(tb, "q").run("Recall 0,74 [[fact:D001/R1]] и EM 41,5 % [[fact:D001/R2]][[fact:D001/R1]].")
    assert vr.text == "Recall 0,74 [1] и EM 41,5 % [2][1]." and [c["n"] for c in vr.cites] == [1, 2]


# ---------------- ответы о статьях: страницы, пересказ, контекст статьи ----------------
QUOTE = "power conversion efficiency of 23.5%"
SUMMARY = ("Статья посвящена повышению эффективности солнечных элементов. Авторы предлагают новую архитектуру прибора. "
           f"Лучший прибор достиг КПД 23,5 % [[page:D001/1|{QUOTE}]]. Измерения проводились при стандартном спектре. "
           "Выводы: подход масштабируется на большие площади.")


def test_summary_flow_page_cite_prose_kept_and_pid_context(client, auth, run_two, actx, fake_textindex):
    llm = actx.llm_client = ScriptedLLM(call("read_page", pid="D001", page=1), say(SUMMARY + " Среднее по 7 приборам 99 %."))
    r = client.post(f"{API}/runs/{run_two['id']}/messages", json={"text": "о чём эта статья?", "pid": "D001"},
                    headers={**auth, "Accept": "text/event-stream"})
    ev = sse_events(r.text)
    ans = done_of(ev)
    assert "fallback" not in ans and ans["meta"]["planner"] == "agent"
    assert ans["text"].count("[1]") == 1 and "Измерения проводились" in ans["text"] and "Выводы" in ans["text"]  # проза цела
    assert "99 %" not in ans["text"]  # число без маркера отброшено
    assert ans["cites"] == [{"n": 1, "domain": "solar", "pid": "D001", "result_id": None, "issue_id": None, "page": 1,
                             "fragment": QUOTE}]
    sysmsg = llm.calls[0]["messages"][0]["content"]
    assert "looking at paper D001" in sysmsg and "QUESTIONS ABOUT A PAPER" in sysmsg and "[[page:" in sysmsg
    assert [d["text"] for e, d in ev if e == "step"][1] == "Читаю страницу 1 статьи D001"


def test_page_marker_quote_must_be_on_page_and_prose_never_falls_back(client, auth, run_two, actx, fake_textindex):
    actx.llm_client = ScriptedLLM(say("Авторы изучают материалы. Прибор дал КПД 23,5 % [[page:D001/1|efficiency was 99%]]."))
    a = done_of(ask_sse(client, auth, run_two["id"], "q"))
    assert "Авторы изучают материалы." in a["text"] and "fallback" not in a  # 1 из 2 отброшено - не откат
    actx.llm_client = ScriptedLLM(say("Прибор дал КПД 23,5 % [[page:D001/77|x]]."))  # такой страницы нет
    assert done_of(ask_sse(client, auth, run_two["id"], "q2"))["fallback"] == "verification"
    tb = Toolbox(_rag_ctx())
    assert Verifier(tb, "q").check_item("Это важно [[page:D001/1|abc]]") is not None  # без индекса страницы нет
    # пересказ: много прозы и два пункта с неверными числами - откат не нужен
    vv = Verifier(tb, "q").run("Первое. Второе. Третье. Четвёртое. Число 5 без маркера. Число 6 без маркера.")
    assert not vv.too_many_dropped and vv.items_total == 6 and vv.items_dropped == 2


def test_single_paper_run_prompt_never_clarifies(client, auth, pdfs, actx):
    from .conftest import upload

    run = upload(client, auth, [pdfs["D001"]]).json()
    llm = actx.llm_client = ScriptedLLM(say("Статья про солнечные элементы."))
    ask_sse(client, auth, run["id"], "расскажи об этой статье")
    s = llm.calls[0]["messages"][0]["content"]
    assert "exactly ONE paper: D001" in s and "never ask which paper" in s and "looking at paper" not in s


def test_pid_validation_and_ask_through_agent_frontend_shape(client, auth, run_two, actx, fake_textindex):
    rid = run_two["id"]
    for url, body in ((f"{API}/runs/{rid}/messages", {"text": "q", "pid": "D999"}),
                      (f"{API}/runs/{rid}/ask", {"question": "q", "pid": "D999"})):
        r = client.post(url, json=body, headers=auth)
        assert r.status_code == 400 and r.json()["error"]["field"] == "pid", r.text
    llm = actx.llm_client = ScriptedLLM(call("read_page", pid="D001", page=1), say(SUMMARY))
    r = client.post(f"{API}/runs/{rid}/ask", json={"question": "о чём статья?", "pid": "D001"}, headers=auth)
    assert r.status_code == 200, r.text
    a = r.json()
    assert set(a) >= {"text", "cards", "cites", "suggestions", "meta"} and "[1]" in a["text"]
    assert a["cites"][0] == {"n": 1, "pid": "D001", "page": "1", "label": f"D001 · стр. 1 · «{QUOTE}»",
                             "fragment": QUOTE}
    assert "looking at paper D001" in llm.calls[0]["messages"][0]["content"]
    assert a["meta"]["cost"] == pytest.approx(0.0004) and a["meta"]["cost_currency"] == "RUB"
    assert a["meta"]["tool_calls"] == 1 and a["meta"]["model"] == "fake" and a["meta"]["dropped"] == 0
    saved = [m for m in messages(client, auth, rid) if m["role"] == "assistant"]  # /ask пишет в текущий чат
    assert len(saved) == 1 and saved[0]["source"] == "ask" and saved[0]["answer"]["text"] == a["text"]
    assert a["thread_id"] == saved[0]["thread_id"] and a["message_id"] == saved[0]["id"]
    # clarify проходит: текст + варианты в suggestions
    actx.llm_client = ScriptedLLM(call("clarify", question="Какой показатель?", options=["КПД", "Voc"]))
    c = client.post(f"{API}/runs/{rid}/ask", json={"question": "лучшее"}, headers=auth).json()
    assert c["clarify"] is True and c["text"] == "Какой показатель?" and c["suggestions"] == ["КПД", "Voc"]
    # агент выключен -> прежний базовый планировщик
    actx.llm_client = None
    assert client.post(f"{API}/runs/{rid}/ask", json={"question": "лучшее"}, headers=auth).json()["meta"]["planner"]


def test_meta_in_done_and_stored_cost_currency(client, auth, run_two, actx, services):
    actx.llm_client = ScriptedLLM(call("schema"), say("Структура получена."))
    ev = ask_sse(client, auth, run_two["id"], "какие таблицы?")
    meta = done_of(ev)["meta"]
    assert meta["tool_calls"] == 1 and meta["cost"] == pytest.approx(0.0004) and meta["cost_currency"] == "RUB"
    assert meta["model"] == "fake" and meta["planner"] == "agent" and meta["dropped"] == 0 and meta["seconds"] >= 0
    mid = [m for m in messages(client, auth, run_two["id"]) if m["role"] == "assistant"][0]
    assert mid["answer"]["meta"]["cost_currency"] == "RUB"
    with services.db.uow() as uow:
        m = store.get(uow, corpus_id_of(run_two["id"]), mid["id"])
    assert m["cost"] == pytest.approx(0.0004) and m["cost_currency"] == "RUB" and m["cost_usd"] is None


# ---------------- правила, калькулятор, предложения правок ----------------
def test_safe_eval_accepts_arithmetic_only():
    from faktura.agent.calc import CalcError, safe_eval

    assert safe_eval("a*b/100 - (c + 1)", {"a": 2, "b": 50, "c": 0}) == 0
    assert safe_eval("-a + 2.5", {"a": 1}) == 1.5
    for bad in ("__import__('os').system('x')", "a.real", "abs(a)", "a**2", "x + 1", "a[0]", "a if a else 1", "[a]",
                "'s'", "lambda: 1", "a; b", "a/0", "", "a" * 300, "(a for a in b)", "True + 1", "a == 1"):
        with pytest.raises(CalcError):
            safe_eval(bad, {"a": 1, "b": 1})


def test_rules_and_calc_tools_and_verifier_calc_marker():
    tb = Toolbox(_rag_ctx())
    r = tb.rules()
    assert r["domain"] == "rag" and {x["check_name"] for x in r["rules"]} >= {"metric_scale", "recall_k"}
    assert all("title" in x and "description" in x for x in r["rules"])
    assert tb.rules(check="recall")["rules"][0]["check_name"] == "recall_k"
    res, _ = tb.call("calc", {"expr": "a*100 + b", "inputs": {"a": "D001/R1", "b": 2}})
    assert res["calc_id"] == "c1" and res["value"] == 76.0
    assert res["inputs"]["a"] == {"fact_id": "D001/R1", "value": 0.74, "unit": "fraction"}
    for bad in ({"expr": "a + zz", "inputs": {"a": 1}}, {"expr": "__import__('os')", "inputs": {"a": 1}},
                {"expr": "a.b", "inputs": {"a": 1}}, {"expr": "abs(a)", "inputs": {"a": 1}},
                {"expr": "a", "inputs": {"a": "D001/R99"}}, {"expr": "a", "inputs": {"a b": 1}}, {"expr": "a", "inputs": {}}):
        assert "error" in tb.call("calc", bad)[0]
    v = Verifier(tb, "q")
    assert v.check_item("Ожидаемое значение 76,0 [[calc:c1]]") is None and v.check_item("Ожидаемо 76 [[calc:c1]]") is None
    assert v.check_item("Ожидаемо 75 [[calc:c1]]") is not None and v.check_item("Ожидаемо 76 [[calc:c9]]") is not None
    c = v.run("Расчёт даёт 76,0 [[calc:c1]].").cites[0]
    assert c["calc_id"] == "c1" and c["expr"] == "a*100 + b" and c["fact_ids"] == ["D001/R1"]
    assert "a*100 + b = 76" in c["fragment"]
    # propose_fix: только предложение, текущее значение подставляется из строки
    res, _ = tb.call("propose_fix", {"items": [{"fact_id": "D001/R1", "suggested": 74, "reason": "доля записана как проценты"}]})
    assert res["ok"] and tb.fixes == [{"pid": "D001", "rid": "R1", "metric": "Recall", "current": 0.74, "suggested": 74.0,
                                       "unit": "fraction", "reason": "доля записана как проценты"}]
    bad_fix = tb.call("propose_fix", {"items": [{"fact_id": "D001/R99", "suggested": 1, "reason": "x"}]})[0]
    assert "error" in bad_fix


def test_consistency_question_rules_calc_fix_card_end_to_end(client, auth, run_two, actx):
    state = {}

    def calc_turn(messages):
        rows = rows_of(messages)
        state["a"], state["b"] = rows[0]["fact_id"], rows[1]["fact_id"]
        return call("calc", cid="c2", expr="a/b*100", inputs={"a": state["a"], "b": state["b"]})

    def fix_turn(messages):
        state["calc"] = json.loads(messages[-1]["content"])
        return call("propose_fix", cid="c3", items=[{"fact_id": state["a"], "suggested": 18.2,
                                                    "reason": "значение перепутано с другим прибором"}])

    def final(messages):
        c = state["calc"]
        return say(f"По формуле ожидается {num(round(c['value'], 2))} [[calc:{c['calc_id']}]]; вероятнее всего ошибочно значение "
                   "из таблицы, предлагаю исправить.")

    llm = actx.llm_client = ScriptedLLM(
        call("rules", check="jv_consistency"),
        call("filter", cid="c1", table="results", where=[{"col": "metric_name", "op": "=", "value": "PCE"}], limit=2),
        calc_turn, fix_turn, final)
    ans = done_of(ask_sse(client, auth, run_two["id"], "как исправить jv_consistency, чтобы значения сошлись?"))
    rules = json.loads(llm.calls[1]["messages"][-1]["content"])["rules"]
    assert rules[0]["check_name"] == "jv_consistency" and "Voc·Jsc·FF" in rules[0]["title"] and rules[0]["thresholds"]
    assert "fallback" not in ans and "[1]" in ans["text"] and ans["cites"][0]["calc_id"] == "c1"
    fix = next(c for c in ans["cards"] if c["kind"] == "fix")
    item = fix["items"][0]
    assert item["suggested"] == 18.2 and item["rid"] and item["pid"] and "reason" in item and "current" in item
    assert any(c["kind"] == "results" for c in ans["cards"])  # входы расчёта показаны карточкой значений
    # неверное число при существующем calc_id - пункт отброшен, проза цела
    actx.llm_client = ScriptedLLM(call("calc", expr="2*3", inputs={"x": 1}), say("Ожидается 7 [[calc:c1]]. Это важно."))
    a2 = done_of(ask_sse(client, auth, run_two["id"], "q"))
    assert "7" not in a2["text"] and "Это важно" in a2["text"]
