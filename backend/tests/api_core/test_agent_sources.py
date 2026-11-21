"""Внешние источники агента (OpenAlex, CORE_API §4l): клиент, инструмент, маркер [[src:]], SSE, эндпоинты. Сеть - мок."""
from __future__ import annotations

import httpx

from faktura.agent.tools import Toolbox
from faktura.agent.verify import Verifier
from faktura.ask.context import load_ctx
from faktura.api.corpus.views import corpus_id_of
from faktura.sources.openalex import OpenAlex, SourcesError

from .conftest import API
from .test_agent import ScriptedLLM, actx, ask_sse, auth_b, call, done_of, say  # noqa: F401


def W(n: int, title: str, year: int, cited: int, doi: str | None = None) -> dict:
    return {"id": f"https://openalex.org/W{n}", "title": title, "publication_year": year, "cited_by_count": cited,
            "doi": f"https://doi.org/{doi}" if doi else None,
            "authorships": [{"author": {"display_name": f"Author {i}"}} for i in range(5)],
            "primary_location": {"landing_page_url": f"https://example.org/{n}", "source": {"display_name": "Journal X"}},
            "open_access": {"is_oa": True, "oa_url": f"https://example.org/{n}.pdf"},
            "related_works": ["https://openalex.org/W2", "https://openalex.org/W3"],
            "abstract_inverted_index": {"Perovskite": [0], "solar": [1], "cells": [2]}}


class FakeOA:
    """Транспорт-мок OpenAlex: запоминает параметры запросов."""

    def __init__(self, status: int = 200) -> None:
        self.seen: list[dict] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = dict(request.url.params)
        self.seen.append(q)
        if self.status != 200:
            return httpx.Response(self.status, json={})
        f = q.get("filter", "")
        if f.startswith("ids.openalex"):
            res = [W(2, "Related two", 2021, 10), W(3, "Related three", 2022, 5)]
        elif f.startswith("cites:"):
            res = [W(7, "Cites it", 2023, 99)]
        elif "search" in q:
            res = [W(8, "Found by query", 2020, 1, "10.1/found")]
        else:
            res = [W(1, "Paper itself", 2019, 300, "10.1/self")]
        return httpx.Response(200, json={"results": res})


def client_of(fake: FakeOA, **kw) -> OpenAlex:
    return OpenAlex(client=httpx.Client(transport=httpx.MockTransport(fake)), **kw)


def test_client_normalizes_caches_and_hides_key_and_errors():
    fake = FakeOA()
    oa = client_of(fake, key="K")
    raw = oa.find_work(doi="https://doi.org/10.1/SELF", title="Paper itself", year=2019)
    rel = oa.related(raw, 5)
    assert [w["openalex_id"] for w in rel] == ["W2", "W3"] and rel[0]["relation"] == "related"
    w = oa.cited_by(raw, 3)[0]
    assert w["relation"] == "cites" and w["cited_by_count"] == 99 and w["authors"] == ["Author 0", "Author 1", "Author 2"]
    assert w["venue"] == "Journal X" and w["oa_url"].endswith("7.pdf") and w["abstract"] == "Perovskite solar cells"
    s = oa.search("perovskite")[0]
    assert s["doi"] == "10.1/found" and s["url"] == "https://doi.org/10.1/found"
    assert fake.seen[0]["filter"] == "doi:10.1/self" and all(q.get("api_key") == "K" for q in fake.seen)
    assert not any("mailto" in q for q in fake.seen)  # по умолчанию без почты
    n = len(fake.seen)
    oa.search("perovskite")  # из кэша
    assert len(fake.seen) == n
    for status in (429, 500):
        try:
            client_of(FakeOA(status)).search("x")
            raise AssertionError("ожидалась SourcesError")
        except SourcesError:
            pass


def test_src_marker_verifier_numbers_from_metadata_only(client, auth, run_two, services):
    with services.db.uow() as uow:
        run = load_ctx(uow, uow.corpora.get(corpus_id_of(run_two["id"])))
    tb = Toolbox(run, services, None, client_of(FakeOA()))
    out = tb.call("external_sources", {"kind": "search", "query": "perovskite"})[0]
    assert out["works"][0]["id"] == "S1" and out["works"][0]["cited_by_count"] == 1
    assert tb.call("external_sources", {"kind": "search", "query": "perovskite"})[0]["works"][0]["id"] == "S1"  # тот же номер
    assert "error" in tb.call("external_sources", {"kind": "related", "pid": "D999"})[0]
    v = Verifier(tb, "q").run("Работа Found by query [[src:S1]] вышла в 2020 году.\n"
                              "Её цитируют 1 раз [[src:S1]].\n"
                              "Её цитируют 77 раз [[src:S1]].\n"
                              "Ещё одна работа [[src:S9]].")
    assert v.text == "Работа Found by query [1] вышла в 2020 году.\nЕё цитируют 1 раз [1]."
    assert v.used_srcs == ["S1"] and v.items_dropped == 2
    assert v.cites[0]["src_id"] == "S1" and v.cites[0]["url"] == "https://doi.org/10.1/found"


def test_agent_answer_sources_sse_and_endpoints(client, auth, auth_b, run_two, actx):
    fake = FakeOA()
    actx.sources_client = client_of(fake)
    rid = run_two["id"]
    llm = actx.llm_client = ScriptedLLM(
        call("external_sources", kind="related", pid="D001", n=2),
        say("Похожие работы:\n1. Related two [[src:S1]]\n2. Related three [[src:S2]]"))
    ev = ask_sse(client, auth, rid, "какие есть похожие статьи?")
    names = [e for e, _ in ev]
    assert names.index("cite") < names.index("sources") < names.index("suggestions") < names.index("done")
    sources = next(d for e, d in ev if e == "sources")["sources"]
    ans = done_of(ev)
    assert sources == ans["sources"] and [s["id"] for s in sources] == ["S1", "S2"] and sources[0]["title"] == "Related two"
    assert "[[" not in ans["text"] and "external_sources" in {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert fake.seen[0]["filter"].startswith(("doi:", "title.search:"))  # статья ищется по DOI, иначе по названию
    mid = next(d for e, d in ev if e == "done")["message_id"]
    r = client.get(f"{API}/runs/{rid}/messages/{mid}/sources", headers=auth).json()
    assert r["message_id"] == mid and r["question"] == "какие есть похожие статьи?" and len(r["sources"]) == 2
    assert r["generated_at"]
    # второй ответ чата с той же работой: на странице чата она одна, с двумя вопросами
    actx.llm_client = ScriptedLLM(call("external_sources", kind="related", pid="D001", n=1),
                                  say("Ещё: Related two [[src:S1]]"))
    ask_sse(client, auth, rid, "а ещё?")
    allv = client.get(f"{API}/runs/{rid}/sources", headers=auth).json()
    assert [s["openalex_id"] for s in allv["sources"]] == ["W2", "W3"]
    assert [c["question"] for c in allv["sources"][0]["cited_in"]] == ["какие есть похожие статьи?", "а ещё?"]
    assert allv["sources"][1]["cited_in"][0]["message_id"] == mid
    # чужой пользователь: 404
    assert client.get(f"{API}/runs/{rid}/messages/{mid}/sources", headers=auth_b).status_code == 404
    assert client.get(f"{API}/runs/{rid}/sources", headers=auth_b).status_code == 404


def test_disabled_sources_tool_not_offered_and_ask_shape(client, auth, run_two, actx):
    actx.sources_client = None
    llm = actx.llm_client = ScriptedLLM(say("Данных нет."))
    a = client.post(f"{API}/runs/{run_two['id']}/ask", json={"question": "что ещё почитать?"}, headers=auth).json()
    assert a["sources"] == [] and "external_sources" not in {t["function"]["name"] for t in llm.calls[0]["tools"]}
    actx.sources_client = client_of(FakeOA())
    actx.llm_client = ScriptedLLM(call("external_sources", kind="cites", pid="D001"), say("Цитируют: Cites it [[src:S1]]."))
    a = client.post(f"{API}/runs/{run_two['id']}/ask", json={"question": "кто цитирует?"}, headers=auth).json()
    assert a["sources"][0]["relation"] == "cites" and a["cites"][0]["src_id"] == "S1" and a["cites"][0]["label"].startswith("Источник")
