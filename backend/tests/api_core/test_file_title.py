"""Поле title у файлов разбора (RunFile): GET /runs/{id}, GET /runs (короткий список), POST /runs, SSE file.*."""
from .conftest import API
from .test_agent import sse_events


def test_file_title_from_publications_everywhere(client, auth, run_two):
    rid = run_two["id"]
    assert all("title" in f for f in run_two["files"])  # ответ POST /runs: в очереди - null
    assert all(f["title"] is None for f in run_two["files"] if f["status"] in ("queued", "running", "error"))
    full = client.get(f"{API}/runs/{rid}", headers=auth).json()
    done = [f for f in full["files"] if f["status"] == "done"]
    assert done and all(isinstance(f["title"], str) and f["title"] for f in done)
    ds = client.get(f"{API}/runs/{rid}/dataset.json", headers=auth).json()
    pubs = ds.get("tables", ds).get("publications") if isinstance(ds, dict) else None
    titles = {p["publication_id"]: " ".join(str(p.get("title") or "").split()) for p in pubs or []}
    assert titles and all(f["title"] == titles[f["id"]] for f in done)
    short = next(r for r in client.get(f"{API}/runs", headers=auth).json()["data"] if r["id"] == rid)
    assert {f["id"]: f["title"] for f in short["files"]} == {f["id"]: f["title"] for f in full["files"]}
    ev = sse_events(client.get(f"{API}/runs/{rid}/events", headers=auth).text)
    files = [d for e, d in ev if e in ("file.progress", "file.done")]
    assert files and all("title" in d for d in files)
    assert {d["id"]: d["title"] for d in files if d.get("status") == "done"} == {f["id"]: f["title"] for f in done}
