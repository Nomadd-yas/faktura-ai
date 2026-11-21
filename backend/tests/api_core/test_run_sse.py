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






