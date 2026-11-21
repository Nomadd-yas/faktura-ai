"""Цитирования статей разбора (§4m) и источники всех чатов (§4l): подставной клиент OpenAlex, без сети."""
from __future__ import annotations

from faktura.sources import citations
from faktura.sources.openalex import SourcesError, normalize

from .conftest import API
from .test_agent import actx, auth_b  # noqa: F401 - фикстуры


def _w(wid, title, cites=0):
    return {"id": f"https://openalex.org/{wid}", "title": title, "publication_year": 2024, "cited_by_count": cites,
            "doi": f"https://doi.org/10.1/{wid.lower()}", "authorships": [{"author": {"display_name": "A. Author"}}],
            "related_works": ["https://openalex.org/W9"]}


class FakeOA:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, 0

    def find_work(self, doi=None, title=None, year=None):
        self.calls += 1
        if self.fail:
            raise SourcesError("down")
        return _w("W1", title or "Paper", 42)

    def cited_by(self, work, n=5):
        return [normalize(_w(f"C{i}", f"Citing {i}", 100 - i), "cites") for i in range(12)][:n]

    def related(self, work, n=5):
        return [normalize(_w("W9", "Related"), "related")]


def test_citations_this_cites_related_cached_and_warning_when_down(client, auth, auth_b, run_two, actx):
    citations.clear_cache()
    rid = run_two["id"]
    actx.sources_client = fake = FakeOA()
    r = client.get(f"{API}/runs/{rid}/citations", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["warning"] is None and body["generated_at"]
    assert {d["pid"] for d in body["data"]} <= {"D001", "D002"} and body["data"]
    src = body["data"][0]["sources"]
    rel = [s["relation"] for s in src]
    assert rel.count("this") == 1 and rel.count("cites") == 10 and rel.count("related") == 1
    assert src[0]["cited_by_count"] == 42 and all(s["pid"] == body["data"][0]["pid"] for s in src)
    assert set(src[0]) >= {"openalex_id", "title", "authors", "year", "venue", "doi", "url", "oa_url", "relation"}
    calls = fake.calls
    client.get(f"{API}/runs/{rid}/citations", headers=auth)
    assert fake.calls == calls  # суточный кэш по DOI/названию
    citations.clear_cache()
    actx.sources_client = FakeOA(fail=True)
    down = client.get(f"{API}/runs/{rid}/citations", headers=auth).json()
    assert down["warning"] == "Научные базы сейчас недоступны" and all(d["sources"] == [] for d in down["data"])
    assert client.get(f"{API}/runs/{rid}/citations", headers=auth_b).status_code == 404


def test_sources_without_thread_collect_all_chats(client, auth, run_two, actx, services):
    from faktura.agent import store
    from faktura.api.corpus.views import corpus_id_of

    rid = run_two["id"]
    cid = corpus_id_of(rid)
    w = {"openalex_id": "W1", "title": "T", "relation": "related"}
    with services.db.uow() as uow:
        for q in ("первый", "второй"):
            t = store.create_thread(uow, cid, None)
            qm = store.add(uow, cid, None, "user", "text", text=q, thread_id=t["id"])
            a = store.add(uow, cid, None, "assistant", "answer", status="running", reply_to=qm["id"], thread_id=t["id"])
            store.finish(uow, a["id"], status="done", answer={"text": "x", "sources": [w]}, clarify=False, meta={},
                         tool_log=[])
    for params in ({}, {"thread": "all"}):
        b = client.get(f"{API}/runs/{rid}/sources", params=params, headers=auth).json()
        assert b["thread_id"] is None and len(b["sources"]) == 1
        assert sorted(c["question"] for c in b["sources"][0]["cited_in"]) == ["второй", "первый"]
        assert all(c["thread_id"] for c in b["sources"][0]["cited_in"])
