"""Замер задержек API (спека BACKEND_ONLY §11): solar (21 dev PDF) + rag (с D014, ~28k строк results), мок-экстрактор.

Запуск из backend/:  uv run --python 3.12 --all-extras python tools/bench_api.py [--rag-all] [--reps 20] [--pg URL]
Цели: GET /runs/{id} < 100 мс, /papers/{domain}/{pid} < 200 мс, /results (страница 100) < 300 мс.
Данные кейсов только читаются (data/ не меняется): БД и хранилище - во временной папке.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
API = "/api/v1"
SOLAR = ROOT / "data" / "caseB_kejs5"
RAG = ROOT / "data" / "caseA_drive"
RAG_SMALL = ("D014", "D020", "D004", "D007", "D001")
TARGETS = {"GET /runs/{id}": 100, "papers": 200, "results": 300}


def build(tmp: Path, db_url: str | None):
    from fastapi.testclient import TestClient
    from faktura.api.base import create_app
    from faktura.config import Settings
    from faktura.pipeline import InlineExecutor, InMemoryEventBus, build_services
    from faktura.storage import seed_admin

    s = Settings(_env_file=None, case_dir=SOLAR, db_url=db_url or f"sqlite:///{(tmp / 'bench.db').as_posix()}",
                 storage_dir=tmp / "storage", cache_dir=tmp / "cache", jwt_secret="bench-secret-bench-secret-bench-0123",
                 admin_email="admin@example.com", admin_password="admin-password-1", cookie_secure=False,
                 extractor="mock", mock_gold_dir=SOLAR / "dev_gold", rag_case_dir=RAG,
                 rag_mock_gold_dir=RAG / "dev_gold", rag_check_script=RAG / "tools" / "check_submission.py")
    sv = build_services(s, executor=InlineExecutor(), bus=InMemoryEventBus())
    sv.db.create_all()
    seed_admin(sv.db, s)
    c = TestClient(create_app(s, sv))
    r = c.post(f"{API}/auth/login", json={"email": s.admin_email, "password": s.admin_password})
    return c, {"Authorization": f"Bearer {r.json()['access_token']}"}


def upload(c, h, paths: list[Path], **form) -> str:
    files = [("file", (p.name, p.read_bytes(), "application/pdf")) for p in paths]
    r = c.post(f"{API}/runs", files=files, data=form, headers=h)
    assert r.status_code == 202, r.text
    return r.json()["id"]


def seed_v2(c, h, run: str, domain: str, pids: list[str]) -> int:
    """Правки статусов/значений и состояния замечаний: чтобы счётчики v2 работали по-настоящему."""
    n = 0
    for pid in pids:
        p = c.get(f"{API}/papers/{domain}/{pid}", params={"run": run, "tables": "results", "limit": 5}, headers=h)
        if p.status_code != 200:
            continue
        body = p.json()
        res = body.get("results") or (body.get("rows") or [])
        if not res:
            r = c.get(f"{API}/results", params={"run": run, "pid": pid, "limit": 3}, headers=h).json()
            res = [x["row"] for x in r["data"]]
        for i, row in enumerate(res[:3]):
            rid = row["result_id"]
            payload = {"status": "rejected"} if i == 0 else {"status": "accepted", "corrected_value": 1.5}
            if c.patch(f"{API}/runs/{run}/papers/{domain}/{pid}/results/{rid}/review", json=payload,
                       headers=h).status_code == 200:
                n += 1
        iss = c.get(f"{API}/issues", params={"run": run, "pid": pid, "limit": 5}, headers=h).json()["data"]
        for i, x in enumerate(iss[:2]):
            iid = x["row"]["issue_id"]
            if c.patch(f"{API}/runs/{run}/papers/{domain}/{pid}/issues/{iid}", json={"state": "ok" if i else "fixed"},
                       headers=h).status_code == 200:
                n += 1
    return n


def timeit(fn, reps: int) -> tuple[float, float]:
    fn()  # прогрев
    ts = []
    for _ in range(reps):
        t = time.perf_counter()
        r = fn()
        ts.append((time.perf_counter() - t) * 1000)
        assert r.status_code == 200, r.text[:300]
    ts.sort()
    return statistics.median(ts), ts[max(0, int(len(ts) * 0.95) - 1)] if len(ts) < 20 else ts[int(len(ts) * 0.95) - 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--rag-all", action="store_true", help="все 21 rag PDF (по умолчанию - 5, с D014)")
    ap.add_argument("--pg", default=None, help="URL Postgres вместо SQLite")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        tmp = Path(td)
        c, h = build(tmp, a.pg)
        t = time.perf_counter()
        solar = upload(c, h, sorted((SOLAR / "dev").glob("D*.pdf")), domain="solar")
        rag_pids = [p.stem for p in sorted((RAG / "dev").glob("D*.pdf"))] if a.rag_all else list(RAG_SMALL)
        rag = upload(c, h, [RAG / "dev" / f"{p}.pdf" for p in rag_pids], domain="rag")
        print(f"build: {time.perf_counter() - t:.1f}s  solar={solar} rag={rag}")
        n = seed_v2(c, h, solar, "solar", ["D001", "D002", "D006", "D010", "D013"])
        n += seed_v2(c, h, rag, "rag", ["D014", "D020", "D004"])
        print(f"v2 edits: {n}")
        tot = c.get(f"{API}/results", params={"run": rag, "limit": 1}, headers=h).json()["total"]
        print(f"rag results rows: {tot}")
        g = lambda url, **q: (lambda: c.get(API + url, params=q, headers=h))  # noqa: E731
        cur = {}
        for k, run in (("solar", solar), ("rag", rag)):
            cur[k] = c.get(f"{API}/results", params={"run": run, "limit": 100}, headers=h).json()["next_cursor"]
        cases = [
            ("GET /runs", g("/runs")),
            ("GET /runs/{id} solar", g(f"/runs/{solar}")),
            ("GET /runs/{id} rag", g(f"/runs/{rag}")),
            ("GET /runs/{id}/summary solar", g(f"/runs/{solar}/summary")),
            ("GET /runs/{id}/summary rag", g(f"/runs/{rag}/summary")),
            ("GET /papers/solar/D001", g("/papers/solar/D001", run=solar)),
            ("GET /papers/rag/D014 summary", g("/papers/rag/D014", run=rag)),
            ("GET /papers/rag/D014 tables=results", g("/papers/rag/D014", run=rag, tables="results", limit=100)),
            ("GET /papers/rag/D014 tables=issues", g("/papers/rag/D014", run=rag, tables="issues", limit=100)),
            ("GET /results solar p1", g("/results", run=solar, limit=100)),
            ("GET /results solar p2 (cursor)", g("/results", run=solar, limit=100, cursor=cur["solar"])),
            ("GET /results rag p1", g("/results", run=rag, limit=100)),
            ("GET /results rag p2 (cursor)", g("/results", run=rag, limit=100, cursor=cur["rag"])),
            ("GET /issues solar", g("/issues", run=solar)),
            ("GET /issues rag", g("/issues", run=rag)),
            ("GET /issues/stats solar", g("/issues/stats", run=solar)),
            ("GET /issues/stats rag", g("/issues/stats", run=rag)),
        ]
        print(f"\n{'endpoint':42s} {'p50 ms':>9s} {'p95 ms':>9s}")
        for name, fn in cases:
            p50, p95 = timeit(fn, a.reps)
            print(f"{name:42s} {p50:9.1f} {p95:9.1f}")


if __name__ == "__main__":
    main()
