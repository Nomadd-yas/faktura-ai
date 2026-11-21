"""История чатов разбора (CORE_API §4k): несколько чатов, имя по первому вопросу, история модели в пределах чата,
/ask пишет в текущий чат, возобновление идущего ответа, переименование, удаление, старые сообщения без чата."""
from __future__ import annotations

import threading

import sqlalchemy as sa

from faktura.agent import store
from faktura.storage import schema as S

from .conftest import API
from .test_agent import ScriptedLLM, actx, auth_b, say, sse_events  # noqa: F401 - фикстуры

LONG_Q = "Какой самый высокий КПД среди всех статей разбора и в каких условиях освещения он был получен " * 2
LONG_Q = LONG_Q.strip()


def ask(client, auth, rid, question, **kw):
    r = client.post(f"{API}/runs/{rid}/ask", json={"question": question, **kw}, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


def threads(client, auth, rid):
    return client.get(f"{API}/runs/{rid}/threads", headers=auth).json()["data"]


def test_ask_keeps_history_in_current_thread_and_new_thread_starts_clean(client, auth, run_two, actx):
    rid = run_two["id"]
    assert threads(client, auth, rid) == []
    assert client.get(f"{API}/runs/{rid}/messages", headers=auth).json()["thread_id"] is None
    llm = actx.llm_client = ScriptedLLM(say("Данных нет."), say("Тоже нет."), say("Новый чат."))
    a1 = ask(client, auth, rid, LONG_Q)
    a2 = ask(client, auth, rid, "а для Voc?")
    assert a1["thread_id"] == a2["thread_id"]
    # второй вопрос видит первый обмен
    assert [m["content"] for m in llm.calls[1]["messages"][1:]] == [LONG_Q, "Данных нет.", "а для Voc?"]
    a3 = ask(client, auth, rid, "с чистого листа", thread_id="new")
    assert a3["thread_id"] != a1["thread_id"] and len(llm.calls[2]["messages"]) == 2
    ts = threads(client, auth, rid)
    assert [t["id"] for t in ts] == [a3["thread_id"], a1["thread_id"]]  # свежие сверху
    assert ts[1]["title"] == store.auto_title(LONG_Q) and len(ts[1]["title"]) <= store.TITLE_MAX
    assert ts[1]["questions"] == 2 and ts[1]["last_message"]["role"] == "assistant"
    assert ts[1]["last_message"]["text"] == "Тоже нет." and ts[1]["running_message_id"] is None
    # без thread_id - текущий (последний) чат; явный id - продолжение старого
    assert ask(client, auth, rid, "ещё")["thread_id"] == a3["thread_id"]
    actx.llm_client = llm = ScriptedLLM(say("Продолжаю."))
    assert ask(client, auth, rid, "вернёмся", thread_id=a1["thread_id"])["thread_id"] == a1["thread_id"]
    assert len(llm.calls[0]["messages"]) == 1 + 4 + 1
    assert threads(client, auth, rid)[0]["id"] == a1["thread_id"]


def test_thread_full_view_messages_filter_and_suggestions(client, auth, run_two, actx):
    rid = run_two["id"]
    actx.llm_client = ScriptedLLM(say("Ответ первый."), say("Ответ второй."))
    t1 = ask(client, auth, rid, "первый")["thread_id"]
    t2 = ask(client, auth, rid, "второй", thread_id="new")["thread_id"]
    full = client.get(f"{API}/runs/{rid}/threads/{t1}", headers=auth).json()
    assert full["id"] == t1 and full["stream_url"] is None
    kinds = [(m["kind"], m.get("text") or (m.get("answer") or {}).get("text")) for m in full["messages"]]
    assert kinds[2:] == [("text", "первый"), ("answer", "Ответ первый.")] and kinds[0][0] == "files"
    assert full["messages"][3]["source"] == "ask" and full["messages"][3]["thread_id"] == t1
    m2 = client.get(f"{API}/runs/{rid}/messages", params={"thread": t2}, headers=auth).json()
    assert m2["thread_id"] == t2 and [m.get("text") for m in m2["data"] if m["role"] == "user"][1:] == ["второй"]
    assert client.get(f"{API}/runs/{rid}/messages", headers=auth).json()["thread_id"] == t2
    sug = client.get(f"{API}/runs/{rid}/suggestions", params={"thread": t1}, headers=auth)
    assert sug.status_code == 200 and "первый" not in sug.json()["data"]
    assert client.get(f"{API}/runs/{rid}/threads/thr_nope", headers=auth).status_code == 404


def test_messages_sse_into_thread_and_resume_running_answer(client, auth, run_two, actx):
    rid = run_two["id"]
    gate, started = threading.Event(), threading.Event()

    def slow(_ms):
        started.set()
        gate.wait(10)
        return say("Готово.")

    actx.llm_client = ScriptedLLM(slow, say("Параллельно."))
    r = client.post(f"{API}/runs/{rid}/messages", json={"text": "долгий", "thread_id": "new"}, headers=auth)
    assert r.status_code == 202
    body = r.json()
    tid, mid = body["thread_id"], body["message_id"]
    assert started.wait(10)
    # «вернулся к чату»: видно, что ответ пишется, и куда подключиться
    full = client.get(f"{API}/runs/{rid}/threads/{tid}", headers=auth).json()
    assert full["running_message_id"] == mid and full["stream_url"] == body["stream_url"]
    assert threads(client, auth, rid)[0]["running_message_id"] == mid
    # тот же чат занят, другой - свободен
    busy = client.post(f"{API}/runs/{rid}/messages", json={"text": "ещё", "thread_id": tid}, headers=auth)
    assert busy.status_code == 409
    other = client.post(f"{API}/runs/{rid}/messages", json={"text": "в другом", "thread_id": "new"},
                        headers={**auth, "Accept": "text/event-stream"})
    assert other.status_code == 200 and other.headers["x-thread-id"] not in (tid, None)
    gate.set()
    s = client.get(full["stream_url"], headers=auth)
    assert sse_events(s.text)[-1][1]["answer"]["text"] == "Готово."
    assert client.get(f"{API}/runs/{rid}/threads/{tid}", headers=auth).json()["running_message_id"] is None


def test_create_rename_delete_and_validation(client, auth, run_two, actx):
    rid = run_two["id"]
    r = client.post(f"{API}/runs/{rid}/threads", json={"title": "  Сравнение   КПД "}, headers=auth)
    assert r.status_code == 201 and r.json()["title"] == "Сравнение КПД" and r.json()["questions"] == 0
    tid = r.json()["id"]
    actx.llm_client = ScriptedLLM(say("Ок."))
    ask(client, auth, rid, "вопрос не меняет имя", thread_id=tid)
    assert threads(client, auth, rid)[0]["title"] == "Сравнение КПД"
    empty = client.post(f"{API}/runs/{rid}/threads", headers=auth).json()
    assert empty["title"] == "Новый чат"
    p = client.patch(f"{API}/runs/{rid}/threads/{tid}", json={"title": "Моё"}, headers=auth)
    assert p.status_code == 200 and p.json()["title"] == "Моё" and p.json()["questions"] == 1
    for bad in ({"title": "   "}, {"title": "x" * 201}):
        e = client.patch(f"{API}/runs/{rid}/threads/{tid}", json=bad, headers=auth)
        assert e.status_code == 400 and e.json()["error"]["field"] == "title"
    assert client.delete(f"{API}/runs/{rid}/threads/{tid}", headers=auth).status_code == 204
    assert client.delete(f"{API}/runs/{rid}/threads/{tid}", headers=auth).status_code == 404
    assert [t["id"] for t in threads(client, auth, rid)] == [empty["id"]]
    assert client.post(f"{API}/runs/{rid}/ask", json={"question": "q", "thread_id": tid}, headers=auth).status_code == 404


def test_other_user_cannot_see_threads(client, auth, auth_b, run_two, actx):
    rid = run_two["id"]
    actx.llm_client = ScriptedLLM(say("Ок."))
    tid = ask(client, auth, rid, "мой вопрос")["thread_id"]
    for method, url in (("get", f"{API}/runs/{rid}/threads"), ("get", f"{API}/runs/{rid}/threads/{tid}"),
                        ("delete", f"{API}/runs/{rid}/threads/{tid}")):
        assert getattr(client, method)(url, headers=auth_b).status_code == 404


def test_legacy_messages_without_thread_become_one_chat(client, auth, run_two, actx, services):
    rid = run_two["id"]
    actx.llm_client = ScriptedLLM(say("Старый ответ."), say("Новый."))
    ask(client, auth, rid, "старый вопрос")
    with services.db.uow() as uow:  # как до появления чатов
        uow.conn.execute(sa.update(S.run_messages).values(thread_id=None))
        uow.conn.execute(sa.delete(S.run_threads))
    ts = threads(client, auth, rid)
    assert len(ts) == 1 and ts[0]["title"] == "старый вопрос" and ts[0]["questions"] == 1
    assert threads(client, auth, rid)[0]["id"] == ts[0]["id"]  # идемпотентно
    assert ask(client, auth, rid, "продолжаю")["thread_id"] == ts[0]["id"]
