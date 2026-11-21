"""SSE /runs/{id}/events (CORE_API.md §4e)."""
from __future__ import annotations

import json
from datetime import timedelta

from faktura.api.corpus import run_sse
from faktura.domain.entities import CorpusFileStatus, RunStatus

from .conftest import API


def parse(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        ev: dict = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                ev["comment"] = line
            elif ": " in line:
                k, v = line.split(": ", 1)
                ev[k] = json.loads(v) if k == "data" else v
        if ev:
            out.append(ev)
    return out


def stream(client, run_id, **kw):
    with client.stream("GET", f"{API}/runs/{run_id}/events", **kw) as r:
        assert r.status_code == 200, r.read()
        text = "".join(r.iter_text())
        return r, parse(text)


def test_finished_run_snapshot_then_done(client, auth, run_two):
    r, evs = stream(client, run_two["id"], headers=auth)
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
    names = [e["event"] for e in evs]
    assert names == ["file.progress", "file.progress", "run.progress", "run.done"]
    ids = [int(e["id"]) for e in evs]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    got = client.get(f"{API}/runs/{run_two['id']}", headers=auth).json()
    for e, f in zip(evs[:2], got["files"]):
        d = e["data"]
        assert {k: d[k] for k in ("id", "status", "progress", "eta_seconds", "pages", "started_at")} == \
            {k: f[k] for k in ("id", "status", "progress", "eta_seconds", "pages", "started_at")}
    for e in evs[2:]:
        d = e["data"]
        assert d["progress"] == got["progress"] == 100 and d["eta_seconds"] is None
        assert d["status"] == "done" and d["started_at"] == got["started_at"]


def test_not_found_unknown_and_foreign(client, auth, run_two, services):
    from faktura.domain.security import hash_password
    from faktura.storage import DEFAULT_WORKSPACE_ID

    for rid in ("run_nope", "nope"):
        r = client.get(f"{API}/runs/{rid}/events", headers=auth)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    with services.db.uow() as uow:
        uow.users.create(workspace_id=DEFAULT_WORKSPACE_ID, email="b@example.com", name="B",
                         password_hash=hash_password("b-password-123"), is_admin=False)
    r = client.post(f"{API}/auth/login", json={"email": "b@example.com", "password": "b-password-123"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = client.get(f"{API}/runs/{run_two['id']}/events", headers=other)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    # то же через query-токен
    r = client.get(f"{API}/runs/{run_two['id']}/events", params={"access_token": other["Authorization"][7:]})
    assert r.status_code == 404


def test_auth_header_query_and_none(client, auth, run_two):
    rid = run_two["id"]
    assert client.get(f"{API}/runs/{rid}/events").status_code == 401
    assert client.get(f"{API}/runs/{rid}/events?access_token=garbage").status_code == 401
    token = auth["Authorization"].split()[1]
    r, evs = stream(client, rid, params={"access_token": token})
    assert r.headers["content-type"].startswith("text/event-stream") and evs[-1]["event"] == "run.done"
    # query-токен не действует на остальных маршрутах
    assert client.get(f"{API}/runs/{rid}", params={"access_token": token}).status_code == 401


def test_running_run_updates_and_closes(client, auth, run_two, services, monkeypatch):
    monkeypatch.setattr(run_sse, "POLL_S", 0.3)
    monkeypatch.setattr(run_sse, "PING_S", 0.5)
    cid = "crp_" + run_two["id"][4:]
    with services.db.uow() as uow:
        run = uow.runs.list(cid, limit=1)[0]
        uow.runs.update(run.id, status=RunStatus.running)
        a, b = uow.files.list(cid)[:2]
        now = uow.now()
        uow.files.update_status(a.id, CorpusFileStatus.processing, stage="Извлечение",
                                started_at=now - timedelta(seconds=30), stage_started_at=now - timedelta(seconds=30))
        uow.files.update_status(b.id, CorpusFileStatus.queued, stage=None, started_at=None, stage_started_at=None)
    import threading, time

    def finish():
        time.sleep(1.2)
        with services.db.uow() as uow:
            uow.runs.update(run.id, status=RunStatus.done)
            for f in uow.files.list(cid)[:2]:
                uow.files.update_status(f.id, CorpusFileStatus.done, stage=None, progress=100)

    t = threading.Thread(target=finish)
    t.start()
    r, evs = stream(client, run_two["id"], headers=auth)
    t.join()
    names = [e.get("event") for e in evs]
    assert names[:3] == ["file.progress", "file.progress", "run.progress"]
    assert evs[0]["data"]["status"] == "running" and evs[0]["data"]["stage"] == "Извлечение"
    assert any("comment" in e for e in evs)  # keep-alive
    assert names[-1] == "run.done" and "file.done" in names
