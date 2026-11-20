"""SSE: sse_stream, EventTokens, GET /events, POST /events/token (REQ-078, К-22, У-22, У-23).

Контракт: api_contract §4.13; spec/04 §4.20. sse_stream тестируется напрямую (без сети), /events - через
TestClient при settings.sse_max_events=3, чтобы поток закрывался сам.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from faktura.api.corpus import EventTokens, sse_stream
from faktura.domain.entities import CurrentUser

API = "/api/v1"
WS = "ws_test"


def _bus():
    from faktura.pipeline import InMemoryEventBus

    return InMemoryEventBus(buffer=100)


def _collect(bus, ws=WS, last=None, **kw) -> list[str]:
    async def run():
        return [frame async for frame in sse_stream(bus, ws, last, **kw)]

    return asyncio.run(asyncio.wait_for(run(), 15))


def _parse(frame: str) -> dict:
    out: dict = {}
    for line in frame.strip("\n").split("\n"):
        if line.startswith(":"):
            out["comment"] = line[1:].strip()
        elif ": " in line:
            k, v = line.split(": ", 1)
            out[k] = v
    if "data" in out:
        out["data"] = json.loads(out["data"])
    return out


def _publish_run_events(bus, ws=WS) -> list[str]:
    ids = [
        bus.publish(ws, "run.progress", {"run_id": "run_1", "corpus_id": "crp_1", "progress": 41,
                                         "counts": {"total": 3, "done": 1}}),
        bus.publish(ws, "run.file", {"run_id": "run_1", "corpus_id": "crp_1", "file_id": "cf_1", "status": "done"}),
        bus.publish(ws, "run.done", {"run_id": "run_1", "corpus_id": "crp_1", "status": "done"}),
    ]
    return ids


# ---- sse_stream ------------------------------------------------------------------------------------------------
def test_sse_stream_frame_format_and_order():
    bus = _bus()
    ids = _publish_run_events(bus)
    frames = _collect(bus, last="0", max_events=3)
    assert len(frames) == 3
    for fr in frames:
        assert fr.startswith("id: ") and fr.endswith("\n\n") and "\nevent: " in fr and "\ndata: " in fr
    parsed = [_parse(f) for f in frames]
    assert [p["event"] for p in parsed] == ["run.progress", "run.file", "run.done"]
    assert [p["id"] for p in parsed] == ids
    assert parsed[0]["data"]["progress"] == 41 and parsed[2]["data"]["status"] == "done"


def test_sse_stream_catches_up_after_last_event_id():
    bus = _bus()
    ids = _publish_run_events(bus)
    frames = _collect(bus, last=ids[0], max_events=2)
    assert [_parse(f)["event"] for f in frames] == ["run.file", "run.done"]


def test_sse_stream_last_event_id_of_last_event_yields_only_new():
    bus = _bus()
    ids = _publish_run_events(bus)
    # новое событие приходит после подключения
    async def run():
        out = []
        async for fr in sse_stream(bus, WS, ids[-1], max_events=1):
            out.append(fr)
        return out

    async def main():
        task = asyncio.create_task(run())
        await asyncio.sleep(0.1)
        bus.publish(WS, "paper.done", {"paper_id": "pap_1"})
        return await asyncio.wait_for(task, 10)

    frames = asyncio.run(main())
    assert _parse(frames[0])["event"] == "paper.done"


def test_sse_stream_isolated_by_workspace():
    bus = _bus()
    _publish_run_events(bus, ws="ws_other")
    bus.publish(WS, "paper.done", {"paper_id": "pap_mine"})
    frames = _collect(bus, last="0", max_events=1)
    assert _parse(frames[0])["data"] == {"paper_id": "pap_mine"}


def test_sse_stream_ping_when_idle_then_closes():
    bus = _bus()
    frames = _collect(bus, ping_s=0.05, idle_timeout_s=0.4)
    assert frames and all(f.startswith(": ping") and f.endswith("\n\n") for f in frames)


def test_sse_stream_max_events_zero_or_one_boundary():
    bus = _bus()
    _publish_run_events(bus)
    assert len(_collect(bus, last="0", max_events=1)) == 1


def test_sse_stream_history_lost_emits_stream_reset():
    bus = _bus()                                   # буфер 100
    for i in range(150):
        bus.publish(WS, "paper.updated", {"paper_id": f"pap_{i}", "fields": []})
    frames = _collect(bus, last="1", max_events=1)  # id=1 давно вытеснен из кольцевого буфера
    p = _parse(frames[0])
    assert p["event"] == "stream.reset" and p["data"]["reason"] == "history_lost"


def test_sse_stream_data_is_single_line_json_with_cyrillic():
    bus = _bus()
    bus.publish(WS, "paper.failed", {"paper_id": "pap_1", "code": "pdf_broken", "message": "PDF повреждён\nстрока 2"})
    frame = _collect(bus, last="0", max_events=1)[0]
    data_lines = [ln for ln in frame.split("\n") if ln.startswith("data:")]
    assert len(data_lines) == 1
    assert "PDF повреждён" in json.loads(data_lines[0][5:].strip())["message"]


# ---- EventTokens -------------------------------------------------------------------------------------------------
def _user() -> CurrentUser:
    return CurrentUser(id="usr_1", workspace_id=WS, email="a@example.com", is_admin=True)


def test_event_token_issue_and_consume_once():
    et = EventTokens()
    tok = et.issue(_user())
    assert tok.startswith("evt_") and len(tok) > 12
    u = et.consume(tok)
    assert u is not None and u.id == "usr_1" and u.workspace_id == WS
    assert et.consume(tok) is None                 # одноразовый


def test_event_tokens_unique_and_unknown_is_none():
    et = EventTokens()
    assert et.issue(_user()) != et.issue(_user())
    assert et.consume("evt_unknown") is None
    assert et.consume("") is None


def test_event_token_expires_after_60s(monkeypatch):
    et = EventTokens()
    tok = et.issue(_user())
    real_time, real_mono = time.time, time.monotonic
    monkeypatch.setattr(time, "time", lambda: real_time() + 61)
    monkeypatch.setattr(time, "monotonic", lambda: real_mono() + 61)
    assert et.consume(tok) is None


# ---- HTTP: POST /events/token, GET /events -----------------------------------------------------------------
def test_events_token_endpoint_shape(client, auth):
    r = client.post(f"{API}/events/token", headers=auth)
    assert r.status_code == 200
    assert r.json()["token"].startswith("evt_") and r.json()["expires_in"] == 60


def test_events_token_requires_auth(client):
    assert client.post(f"{API}/events/token").status_code == 401


def test_events_without_credentials_401(client):
    assert client.get(f"{API}/events").status_code == 401


def test_events_with_bad_token_401(client):
    r = client.get(f"{API}/events?token=evt_bogus")
    assert r.status_code == 401


def test_events_token_reuse_401(make_client):
    client, services, auth = make_client(sse_max_events=1)
    services.bus.publish(auth_ws(client, auth), "paper.done", {"paper_id": "pap_1"})
    tok = client.post(f"{API}/events/token", headers=auth).json()["token"]
    first = client.get(f"{API}/events?token={tok}", headers={"Last-Event-ID": "0"})
    assert first.status_code == 200
    again = client.get(f"{API}/events?token={tok}", headers={"Last-Event-ID": "0"})
    assert again.status_code == 401


def auth_ws(client, auth) -> str:
    return client.get(f"{API}/auth/me", headers=auth).json().get("workspace_id", "ws_default")


def test_events_closes_itself_at_sse_max_events_3(make_client):
    client, services, auth = make_client(sse_max_events=3)
    ws = auth_ws(client, auth)
    for i in range(5):
        services.bus.publish(ws, "paper.updated", {"paper_id": f"pap_{i}", "fields": []})
    r = client.get(f"{API}/events", headers={**auth, "Last-Event-ID": "0"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.text.count("event: paper.updated") == 3


def test_events_stream_with_query_token_and_headers(make_client):
    client, services, auth = make_client(sse_max_events=1)
    ws = auth_ws(client, auth)
    services.bus.publish(ws, "run.done", {"run_id": "run_1", "status": "done"})
    tok = client.post(f"{API}/events/token", headers=auth).json()["token"]
    r = client.get(f"{API}/events?token={tok}", headers={"Last-Event-ID": "0"})
    assert r.status_code == 200 and "event: run.done" in r.text
    assert r.headers.get("cache-control", "").startswith("no-cache")


def test_events_workspace_isolation(make_client):
    client, services, auth = make_client(sse_max_events=1)
    services.bus.publish("ws_foreign", "run.done", {"run_id": "run_x"})
    services.bus.publish(auth_ws(client, auth), "run.done", {"run_id": "run_mine"})
    r = client.get(f"{API}/events", headers={**auth, "Last-Event-ID": "0"})
    assert "run_mine" in r.text and "run_x" not in r.text


@pytest.mark.data
def test_run_publishes_progress_file_done_events(make_client, h, dev_zip3):
    client, services, auth = make_client()
    ws = auth_ws(client, auth)
    c = client.post(f"{API}/corpora", json={"name": "Ev", "collection": "solar"}, headers=auth).json()
    h.upload_zip(client, auth, c["corpus_id"], dev_zip3)
    run = h.start_run(client, auth, c["corpus_id"]).json()
    h.wait_run(client, auth, run["run_id"])
    frames = _collect(services.bus, ws=ws, last="0", ping_s=0.05, idle_timeout_s=0.5)
    kinds = {_parse(f).get("event") for f in frames}
    assert {"run.file", "run.done"} <= kinds
    done = next(_parse(f) for f in frames if _parse(f).get("event") == "run.done")
    assert done["data"]["run_id"] == run["run_id"] and done["data"]["counts"]["done"] == 3
