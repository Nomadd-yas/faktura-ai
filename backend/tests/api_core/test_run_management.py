"""Новое от фронта (BACKEND_ONLY §7а): rerun-as, PATCH/DELETE/restart/stop, PDF и статья для out, паспорт."""
from __future__ import annotations

import io
import json
import threading
import zipfile

from .conftest import API, upload


def err(r, status, code):
    assert r.status_code == status, r.text
    assert r.json()["error"]["code"] == code, r.text
    assert r.json()["error"]["message"]


def mk(client, auth, paths, **form):
    r = upload(client, auth, paths, **form)
    assert r.status_code == 202, r.text
    return r.json()


def _cid(rid: str) -> str:
    return "crp_" + rid[4:]


def _set_run_status(services, rid, status):
    with services.db.uow() as uow:
        run = uow.runs.list(_cid(rid), limit=1)[0]
        uow.runs.update(run.id, status=status, finished_at=None)


# --- PDF и статья для out -----------------------------------------------------------------------











# --- rerun-as -----------------------------------------------------------------------------------



def test_rerun_as_title_and_solar_suffix(client, auth, pdfs):
    src = mk(client, auth, [pdfs["D001"], pdfs["D002"]], domain="rag", title="RAG-набор")
    ok = client.post(f"{API}/runs/{src['id']}/rerun-as", json={"domain": "solar", "file_ids": ["D001"]}, headers=auth)
    assert ok.status_code == 202 and ok.json()["title"] == "RAG-набор · солнечные"
    ok = client.post(f"{API}/runs/{src['id']}/rerun-as",
                     json={"domain": "solar", "file_ids": ["D001", "D002"], "title": "Свой"}, headers=auth)
    assert ok.status_code == 202 and ok.json()["title"] == "Свой" and ok.json()["counts"]["files"] == 2






# --- PATCH / DELETE -----------------------------------------------------------------------------

def test_patch_pin_rename_and_order(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]], title="A")
    b = mk(client, auth, [pdfs["D002"]], title="B")

    def ids():
        return [r["id"] for r in client.get(f"{API}/runs", headers=auth).json()["data"]]

    assert ids() == [b["id"], a["id"]]
    r = client.patch(f"{API}/runs/{a['id']}", json={"pinned": True}, headers=auth)
    assert r.status_code == 200 and r.json()["pinned"] is True and r.json()["title"] == "A"
    assert ids() == [a["id"], b["id"]]
    r = client.patch(f"{API}/runs/{a['id']}", json={"title": "  Новое  "}, headers=auth)
    assert r.json()["title"] == "Новое" and r.json()["pinned"] is True and "files" in r.json()
    assert client.get(f"{API}/runs/{a['id']}", headers=auth).json()["title"] == "Новое"
    assert client.patch(f"{API}/runs/{a['id']}", json={"pinned": False}, headers=auth).json()["pinned"] is False
    assert ids() == [b["id"], a["id"]]




def test_delete_soft(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"]])
    b = mk(client, auth, [pdfs["D002"]])
    assert client.delete(f"{API}/runs/{a['id']}", headers=auth).status_code == 204
    assert [r["id"] for r in client.get(f"{API}/runs", headers=auth).json()["data"]] == [b["id"]]
    for path in ("", "/summary", "/submission.zip", "/passport.json"):
        err(client.get(f"{API}/runs/{a['id']}{path}", headers=auth), 404, "not_found")
    err(client.delete(f"{API}/runs/{a['id']}", headers=auth), 404, "not_found")
    err(client.delete(f"{API}/runs/run_nope", headers=auth), 404, "not_found")
    err(client.get(f"{API}/papers/solar/D001", params={"run": a["id"]}, headers=auth), 404, "not_found")




# --- restart / stop -----------------------------------------------------------------------------













def test_other_users_run_is_404(client, auth, pdfs, services):
    a = mk(client, auth, [pdfs["D001"]])
    with services.db.uow() as uow:
        uow.conn.exec_driver_sql("UPDATE corpora SET created_by = 'usr_other'")
    for method, path, kw in [("patch", "", {"json": {"pinned": True}}), ("delete", "", {}),
                             ("post", "/restart", {}), ("post", "/stop", {}),
                             ("post", "/rerun-as", {"json": {"domain": "rag", "file_ids": ["D001"]}}),
                             ("get", "/passport.json", {}), ("get", "/passport.md", {})]:
        err(getattr(client, method)(f"{API}/runs/{a['id']}{path}", headers=auth, **kw), 404, "not_found")


# --- паспорт ------------------------------------------------------------------------------------

def test_passport_json_equals_zip(client, auth, pdfs):
    a = mk(client, auth, [pdfs["D001"], pdfs["D006"]])
    z = client.get(f"{API}/runs/{a['id']}/submission.zip", headers=auth)
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    pj = client.get(f"{API}/runs/{a['id']}/passport.json", headers=auth)
    assert pj.status_code == 200 and pj.headers["content-type"].startswith("application/json")
    assert pj.content == zf.read("passport.json")
    pm = client.get(f"{API}/runs/{a['id']}/passport.md", headers=auth)
    assert pm.status_code == 200 and pm.content == zf.read("PASSPORT.md")
    p = pj.json()
    assert p["title"] == "Паспорт данных «Фактура AI»" and p["version"] == "1" and p["format"]
    assert set(p["versions"]) == {"model", "rules", "dictionary"} and p["versions"]["rules"]
    assert [d["id"] for d in p["domains"]] == ["solar"]
    d = p["domains"][0]
    assert [g["id"] for g in d["guides"]] == ["inclusion", "comparability", "extraction"]
    assert [e["id"] for e in d["entities"]] == ["publications", "materials", "devices", "layers", "measurements",
                                               "results", "issues"]
    assert d["rules"] and d["normalization"]
    err(client.get(f"{API}/runs/run_nope/passport.json", headers=auth), 404, "not_found")
    assert client.get(f"{API}/runs/{a['id']}/passport.json").status_code == 401


