"""Данные: /data/query/parse (P1), /data/rows (P2, настоящий разбор), заглушки /data/filter-options,
/data/compare, /papers/{id}/links, /papers/{id}/cited-by (api_contract §4.14, У-15, У-21)."""
from __future__ import annotations

import pytest

from .conftest import url


def stub_header(r) -> str | None:
    return r.headers.get("x-faktura-stub")


# --- GET /data/query/parse ------------------------------------------------------------------------

class TestQueryParse:
    def test_spec_example(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="solar", q="КПД выше 18 после 2022")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"filters", "unparsed", "metric"}
        assert [f["kind"] for f in body["filters"]] == ["num", "year"]
        assert body["filters"][0] == {"id": "num:pce:min", "label": "КПД > 18 %", "match": "КПД выше 18",
                                      "kind": "num", "param": "pce", "op": ">", "value": 18}
        assert body["filters"][1]["id"] == "year:min" and body["filters"][1]["value"] == 2022
        assert body["unparsed"] == "" and body["metric"] == "pce"

    def test_not_a_stub(self, dev_env):
        assert stub_header(dev_env.get("/data/query/parse", collection="solar", q="КПД выше 18")) is None

    def test_unparsed_non_strict_200(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="solar", q="абракадабра")
        assert r.status_code == 200
        assert r.json()["filters"] == [] and r.json()["unparsed"] == "абракадабра"

    def test_strict_with_unparsed_400(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="solar", q="абракадабра", strict="true")
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["code"] == "query_unparsed" and err["message"]

    def test_strict_partially_parsed_ok_or_400(self, dev_env):
        """«ничего не распознано» -> 400; частично распознанная строка - решение реализации, но не 5xx."""
        r = dev_env.get("/data/query/parse", collection="solar", q="КПД выше 18 абракадабра", strict="true")
        assert r.status_code in (200, 400)
        if r.status_code == 400:
            assert r.json()["error"]["code"] == "query_unparsed"

    def test_strict_fully_parsed_ok(self, dev_env):
        assert dev_env.get("/data/query/parse", collection="solar", q="КПД выше 18", strict="true").status_code == 200

    def test_strict_false_explicit(self, dev_env):
        assert dev_env.get("/data/query/parse", collection="solar", q="xyz", strict="false").status_code == 200

    def test_empty_q(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="solar", q="")
        assert r.status_code == 200 and r.json() == {"filters": [], "unparsed": "", "metric": None}

    def test_missing_q_defaults_empty_or_400(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="solar")
        assert r.status_code in (200, 400)

    def test_missing_collection_400(self, dev_env):
        r = dev_env.get("/data/query/parse", q="КПД выше 18")
        assert r.status_code == 400 and r.json()["error"]["field"] == "collection"

    def test_rag_collection_400(self, dev_env):
        r = dev_env.get("/data/query/parse", collection="rag", q="BM25")
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_url_encoded_cyrillic(self, dev_env):
        r = dev_env.client.get(url("/data/query/parse?collection=solar&q=%D0%9A%D0%9F%D0%94%20%D0%B2%D1%8B%D1%88%D0%B5%2018"))
        assert r.json()["filters"][0]["param"] == "pce"

    def test_matches_facts_table_filters(self, dev_env):
        q = "перовскит КПД выше 18 после 2022"
        parsed = dev_env.get("/data/query/parse", collection="solar", q=q).json()
        table = dev_env.get("/facts/table", corpus_id=dev_env.corpus_id, q=q, limit=1).json()
        assert parsed["filters"] == table["filters"] and parsed["unparsed"] == table["unparsed"]

    def test_requires_auth(self, dev_env):
        assert dev_env.anon.get(url("/data/query/parse"), params={"collection": "solar", "q": "x"}).status_code == 401


# --- GET /data/rows -------------------------------------------------------------------------------

class TestRows:
    EMPTY_KEYS = {"query", "filters", "unparsed", "metric", "data", "total", "next_cursor", "stats"}

    def test_stub_shape(self, dev_env):
        r = dev_env.get("/data/rows", collection="solar")
        assert r.status_code == 200
        body = r.json()
        assert set(body) == self.EMPTY_KEYS
        assert body["data"] == [] and body["total"] == 0 and body["next_cursor"] is None
        assert body["stats"] == {"rows": 0, "papers": 0, "metric": None}

    def test_stub_header(self, dev_env):
        assert stub_header(dev_env.get("/data/rows", collection="solar")) == "1"

    def test_real_parse_of_query(self, dev_env):
        q = "перовскит КПД выше 18 после 2022"
        body = dev_env.get("/data/rows", collection="solar", q=q).json()
        assert body["query"] == q
        assert [f["kind"] for f in body["filters"]] == ["topic", "num", "year"]
        assert body["unparsed"] == "" and body["metric"] == "pce"
        assert body["data"] == []

    def test_unparsed_reported(self, dev_env):
        body = dev_env.get("/data/rows", collection="solar", q="КПД выше 18 абракадабра").json()
        assert body["unparsed"] == "абракадабра"

    def test_empty_q(self, dev_env):
        body = dev_env.get("/data/rows", collection="solar").json()
        assert body["query"] == "" and body["filters"] == []

    def test_collection_required(self, dev_env):
        r = dev_env.get("/data/rows")
        assert r.status_code == 400 and r.json()["error"]["field"] == "collection"

    def test_filters_equal_parse_endpoint(self, dev_env):
        q = "КПД выше 18 после 2022"
        rows = dev_env.get("/data/rows", collection="solar", q=q).json()
        parsed = dev_env.get("/data/query/parse", collection="solar", q=q).json()
        assert rows["filters"] == parsed["filters"] and rows["metric"] == parsed["metric"]

    def test_accepts_sort_dir_limit_and_scope(self, dev_env):
        r = dev_env.get("/data/rows", collection="solar", sort="year", dir="asc", limit=10,
                        paper_ids=dev_env.pid("D001"))
        assert r.status_code == 200

    def test_limit_default_500_max(self, dev_env):
        assert dev_env.get("/data/rows", collection="solar", limit=501).status_code in (200, 400)


# --- GET /data/filter-options ---------------------------------------------------------------------

class TestFilterOptions:
    def test_stub(self, dev_env):
        r = dev_env.get("/data/filter-options", collection="solar")
        assert r.status_code == 200 and r.json() == {"data": []}
        assert stub_header(r) == "1"

    def test_without_collection(self, dev_env):
        assert dev_env.get("/data/filter-options").status_code in (200, 400)

    def test_requires_auth(self, dev_env):
        assert dev_env.anon.get(url("/data/filter-options")).status_code == 401


# --- GET /data/compare ----------------------------------------------------------------------------

class TestCompare:
    def test_stub_shape(self, dev_env):
        ids = f"{dev_env.pid('D001')},{dev_env.pid('D004')}"
        r = dev_env.get("/data/compare", ids=ids)
        assert r.status_code == 200 and stub_header(r) == "1"
        body = r.json()
        assert set(body) == {"collection", "metric", "columns", "params", "conditions", "comparable", "conclusions"}
        assert body["columns"] == [] and body["params"] == [] and body["conditions"] == [] \
            and body["conclusions"] == []
        assert body["comparable"] == {"ok": True, "reasons": []}
        assert body["collection"] == "solar"

    def test_three_ids_ok(self, dev_env):
        ids = ",".join(dev_env.pid(p) for p in ("D001", "D004", "D021"))
        assert dev_env.get("/data/compare", ids=ids).status_code == 200

    def test_metric_param_echoed(self, dev_env):
        ids = f"{dev_env.pid('D001')},{dev_env.pid('D004')}"
        assert dev_env.get("/data/compare", ids=ids, metric="voc").json()["metric"] in ("voc", "pce", None)

    @pytest.mark.parametrize("count", [0, 1, 4])
    def test_wrong_number_of_ids_400(self, dev_env, count):
        ids = ",".join(list(dev_env.papers.values())[:count])
        r = dev_env.get("/data/compare", ids=ids)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_missing_ids_400(self, dev_env):
        assert dev_env.get("/data/compare").status_code == 400

    def test_duplicate_ids(self, dev_env):
        p = dev_env.pid("D001")
        assert dev_env.get("/data/compare", ids=f"{p},{p}").status_code in (200, 400)


# --- GET /papers/{id}/links и /cited-by -----------------------------------------------------------

class TestLinks:
    def test_links_stub(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/links")
        assert r.status_code == 200 and stub_header(r) == "1"
        assert r.json() == {"references": [], "summary": {"refs": 0, "unparsed": 0, "cited_by": 0},
                            "cited_by": [], "entities": []}

    def test_cited_by_stub(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/cited-by")
        assert r.status_code == 200 and r.json() == {"data": []} and stub_header(r) == "1"

    @pytest.mark.parametrize("suffix", ["links", "cited-by"])
    def test_unknown_paper_404(self, dev_env, suffix):
        r = dev_env.get(f"/papers/pap_nope/{suffix}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    @pytest.mark.parametrize("suffix", ["links", "cited-by"])
    def test_requires_auth(self, dev_env, paper_id, suffix):
        assert dev_env.anon.get(url(f"/papers/{paper_id}/{suffix}")).status_code == 401


def test_real_endpoints_have_no_stub_header(dev_env, paper_id):
    """Заголовок X-Faktura-Stub только у заглушек (У-15)."""
    for path in (f"/papers/{paper_id}", f"/papers/{paper_id}/coverage", f"/papers/{paper_id}/facts",
                 "/facts/table?collection=solar", "/dictionary", "/papers"):
        assert stub_header(dev_env.client.get(url(path))) is None, path
