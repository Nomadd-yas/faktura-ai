"""GET /facts/table: фильтры, сортировки, keyset, фасеты, язык запросов (spec/13 §13.7, api_contract §4.8)."""
from __future__ import annotations

import pytest

from .conftest import all_facts, table_pages, url

ROW_KEYS = {
    "fact_id", "param", "label", "metric_name", "raw_value", "raw_unit", "normalized_value", "unit", "display",
    "paper_id", "publication_id", "paper_title", "year", "row_id", "row_label", "source_page", "source_kind",
    "source_ref", "source_where", "anchor_id", "review_status", "extraction_status", "confidence", "aggregation",
    "result_origin", "exportable", "issues",
}


def table(env, **params):
    params.setdefault("corpus_id", env.corpus_id)
    r = env.client.get(url("/facts/table"), params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestShape:
    def test_response_keys(self, dev_env):
        body = table(dev_env, limit=5)
        assert set(body) >= {"data", "total", "next_cursor", "filters", "unparsed"}
        assert body["filters"] == [] and body["unparsed"] == ""
        assert "facets" not in body or body["facets"] is None

    def test_row_keys(self, dev_env):
        row = table(dev_env, limit=1)["data"][0]
        assert ROW_KEYS <= set(row), ROW_KEYS - set(row)

    def test_default_limit_100(self, dev_env):
        body = table(dev_env)
        assert len(body["data"]) == 100 and body["total"] > 100 and body["next_cursor"]

    def test_row_joins_paper(self, dev_env, paper_id):
        row = table(dev_env, paper_id=paper_id, limit=1)["data"][0]
        assert row["paper_id"] == paper_id and row["publication_id"] == "D001" and row["year"] == 2021
        assert row["paper_title"].startswith("Improved Photovoltaic")

    def test_issues_refs(self, dev_env):
        rows = table(dev_env, has_issues="true", limit=50)["data"]
        assert rows
        for r in rows:
            assert r["issues"]
            assert set(r["issues"][0]) >= {"issue_id", "check_name", "severity"}

    def test_row_equals_fact_card(self, dev_env, paper_id):
        row = table(dev_env, paper_id=paper_id, limit=1)["data"][0]
        card = dev_env.get(f"/facts/{row['fact_id']}").json()
        for k in ("param", "raw_value", "normalized_value", "display", "source_page", "source_kind",
                  "review_status", "confidence", "exportable", "anchor_id"):
            assert row[k] == card[k], k

    def test_scope_required(self, dev_env):
        r = dev_env.client.get(url("/facts/table"))
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_scope_collection_only(self, dev_env):
        assert dev_env.client.get(url("/facts/table"), params={"collection": "solar", "limit": 1}).status_code == 200

    def test_unknown_corpus_empty_or_404(self, dev_env):
        r = dev_env.client.get(url("/facts/table"), params={"corpus_id": "crp_nope"})
        assert r.status_code == 404 or r.json()["total"] == 0

    def test_requires_auth(self, dev_env):
        assert dev_env.anon.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id}).status_code == 401


class TestFilters:
    def test_param(self, dev_env):
        body = table(dev_env, param="pce", limit=1000)
        assert body["total"] == len(body["data"]) and {r["param"] for r in body["data"]} == {"pce"}

    def test_param_repeatable(self, dev_env):
        body = dev_env.client.get(url("/facts/table"), params=[("corpus_id", dev_env.corpus_id), ("param", "pce"),
                                                               ("param", "voc"), ("limit", 1000)]).json()
        assert {r["param"] for r in body["data"]} == {"pce", "voc"}

    def test_metric_name(self, dev_env):
        assert {r["metric_name"] for r in table(dev_env, metric_name="Jsc", limit=1000)["data"]} == {"Jsc"}

    def test_min_max(self, dev_env):
        rows = table(dev_env, param="pce", min=10, max=15, limit=1000)["data"]
        assert rows and all(10 <= r["normalized_value"] <= 15 for r in rows)

    def test_review_status_default_hides_rejected(self, small_env):
        pid = small_env.pid("D001")
        total = table(small_env, paper_id=pid, limit=1)["total"]
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="rejected")
        assert table(small_env, paper_id=pid, limit=1)["total"] == total - 1
        only = table(small_env, paper_id=pid, review_status="rejected")
        assert [r["fact_id"] for r in only["data"]] == [fid]

    def test_review_status_accepted(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="accepted")
        rows = table(small_env, review_status="accepted")["data"]
        assert [r["fact_id"] for r in rows] == [fid]

    def test_source_kind_and_origin(self, dev_env):
        rows = table(dev_env, source_kind="table", result_origin="primary", limit=1000)["data"]
        assert rows and {(r["source_kind"], r["result_origin"]) for r in rows} == {("table", "primary")}

    def test_extraction_status(self, dev_env):
        assert table(dev_env, extraction_status="present_not_extractable")["total"] == 6

    def test_paper_id_repeatable(self, dev_env):
        ids = [dev_env.pid("D001"), dev_env.pid("D004")]
        body = dev_env.client.get(url("/facts/table"), params=[("corpus_id", dev_env.corpus_id)] +
                                  [("paper_id", i) for i in ids] + [("limit", 1000)]).json()
        assert {r["paper_id"] for r in body["data"]} == set(ids)
        assert body["total"] == 47 + 31

    def test_year_from_to(self, dev_env):
        rows = table(dev_env, year_from=2024, year_to=2024, limit=1000)["data"]
        assert rows and {r["year"] for r in rows} == {2024}

    def test_year_from_only(self, dev_env):
        assert all(r["year"] >= 2025 for r in table(dev_env, year_from=2025, limit=1000)["data"])

    def test_has_issues_true_false_partition(self, dev_env):
        total = table(dev_env, limit=1)["total"]
        yes = table(dev_env, has_issues="true", limit=1)["total"]
        no = table(dev_env, has_issues="false", limit=1)["total"]
        assert yes + no == total and yes > 0 and no > 0

    def test_check_name_and_severity(self, dev_env):
        rows = table(dev_env, check_name="jv_consistency", limit=1000)["data"]
        assert all(any(i["check_name"] == "jv_consistency" for i in r["issues"]) for r in rows)
        sev = table(dev_env, severity="warn", limit=1000)["data"]
        assert all(any(i["severity"] == "warn" for i in r["issues"]) for r in sev)

    def test_confidence_range(self, dev_env):
        rows = table(dev_env, min_confidence=0.5, max_confidence=0.9, limit=1000)["data"]
        assert all(0.5 <= r["confidence"] <= 0.9 for r in rows)

    def test_text_search(self, dev_env, paper_id):
        rows = table(dev_env, text="SrCl2", paper_id=paper_id, limit=1000)["data"]
        assert rows

    def test_text_search_in_paper_title(self, dev_env):
        rows = table(dev_env, text="CsPbI2Br", limit=1000)["data"]
        assert rows and "D001" in {r["publication_id"] for r in rows}

    def test_text_no_match(self, dev_env):
        body = table(dev_env, text="zzzzqqqq")
        assert body["total"] == 0 and body["data"] == [] and body["next_cursor"] is None

    def test_filters_combine_with_and(self, dev_env):
        rows = table(dev_env, param="pce", year_from=2024, min=15, limit=1000)["data"]
        assert all(r["param"] == "pce" and r["year"] >= 2024 and r["normalized_value"] >= 15 for r in rows)

    def test_columns_param(self, dev_env):
        row = table(dev_env, limit=1, columns="fact_id,display")["data"][0]
        assert {"fact_id", "display"} <= set(row)

    def test_invalid_number_400(self, dev_env):
        assert dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id,
                                                               "min": "abc"}).status_code == 400

    def test_invalid_bool_400(self, dev_env):
        assert dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id,
                                                               "has_issues": "maybe"}).status_code == 400


class TestSort:
    @pytest.mark.parametrize("field", ["paper", "year", "param", "value", "confidence", "page", "issues",
                                       "updated_at"])
    def test_each_sort_field_ok(self, dev_env, field):
        assert dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id, "sort": field,
                                                               "limit": 5}).status_code == 200

    def test_value_desc(self, dev_env):
        rows = table(dev_env, param="pce", sort="value", dir="desc", limit=1000)["data"]
        vals = [r["normalized_value"] for r in rows]
        assert vals == sorted(vals, reverse=True)

    def test_value_asc(self, dev_env):
        vals = [r["normalized_value"] for r in table(dev_env, param="pce", sort="value", dir="asc",
                                                      limit=1000)["data"]]
        assert vals == sorted(vals)

    def test_year_then_param(self, dev_env):
        rows = table(dev_env, sort="year,param", dir="asc", limit=1000)["data"]
        keys = [(r["year"] or 0, r["param"]) for r in rows]
        assert keys == sorted(keys)

    def test_page_sort_within_paper(self, dev_env, paper_id):
        rows = table(dev_env, paper_id=paper_id, sort="page", dir="asc", limit=1000)["data"]
        pages = [r["source_page"] if r["source_page"] is not None else 10 ** 6 for r in rows]
        assert pages == sorted(pages)

    def test_confidence_nulls_last(self, dev_env):
        rows = table(dev_env, sort="confidence", dir="desc", limit=1000)["data"]
        seen_null = False
        for r in rows:
            if r["confidence"] is None:
                seen_null = True
            else:
                assert not seen_null, "пустые значения идут в конце"

    def test_issues_sort_desc_first_has_most(self, dev_env):
        rows = table(dev_env, sort="issues", dir="desc", limit=50)["data"]
        counts = [len(r["issues"]) for r in rows]
        assert counts == sorted(counts, reverse=True)

    def test_unknown_field_400(self, dev_env):
        r = dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id, "sort": "bogus"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_bad_dir_400(self, dev_env):
        assert dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id,
                                                               "dir": "sideways"}).status_code == 400


class TestKeyset:
    @pytest.mark.parametrize("sort, direction", [("paper", "asc"), ("value", "desc"), ("value", "asc"),
                                                 ("confidence", "desc"), ("year", "asc"), ("page", "asc")])
    def test_no_gaps_no_duplicates(self, dev_env, sort, direction):
        rows, pages = table_pages(dev_env.client, 97, corpus_id=dev_env.corpus_id, sort=sort, dir=direction)
        ids = [r["fact_id"] for r in rows]
        assert len(ids) == len(set(ids)) == pages[0]["total"]
        assert all(p["next_cursor"] for p in pages[:-1]) and pages[-1]["next_cursor"] is None

    def test_pages_equal_single_big_page(self, dev_env):
        rows, _ = table_pages(dev_env.client, 50, corpus_id=dev_env.corpus_id, sort="value", dir="desc")
        big = table(dev_env, sort="value", dir="desc", limit=1000)["data"]
        assert [r["fact_id"] for r in rows[:len(big)]] == [r["fact_id"] for r in big]

    def test_limit_one(self, dev_env, paper_id):
        rows, pages = table_pages(dev_env.client, 1, corpus_id=dev_env.corpus_id, paper_id=paper_id)
        assert len(rows) == 47 and len(pages) == 47

    def test_ties_in_sort_value_are_stable(self, dev_env):
        """Много фактов с одинаковым значением: ключ курсора включает fact_id."""
        rows, _ = table_pages(dev_env.client, 7, corpus_id=dev_env.corpus_id, param="ff", sort="value")
        ids = [r["fact_id"] for r in rows]
        assert len(ids) == len(set(ids))

    def test_cursor_stable_when_neighbour_changes(self, small_env):
        """Правка соседней строки между страницами не сдвигает страницу."""
        first = table(small_env, sort="paper", limit=20)
        fid = first["data"][-1]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="accepted")
        second = table(small_env, sort="paper", limit=20, cursor=first["next_cursor"])
        assert fid not in {r["fact_id"] for r in second["data"]}
        assert not {r["fact_id"] for r in first["data"]} & {r["fact_id"] for r in second["data"]}

    def test_limit_above_1000(self, dev_env):
        r = dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id, "limit": 5000})
        assert r.status_code == 400 or len(r.json()["data"]) <= 1000

    def test_limit_zero_or_negative_400(self, dev_env):
        for v in (0, -5):
            assert dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id,
                                                                   "limit": v}).status_code == 400

    def test_cursor_from_other_sort_rejected(self, dev_env):
        first = table(dev_env, sort="value", limit=5)
        r = dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id, "sort": "paper",
                                                            "limit": 5, "cursor": first["next_cursor"]})
        assert r.status_code == 400

    def test_garbage_cursor_400(self, dev_env):
        r = dev_env.client.get(url("/facts/table"), params={"corpus_id": dev_env.corpus_id, "cursor": "!!!"})
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"


class TestFacets:
    def test_present_only_when_requested(self, dev_env):
        assert not table(dev_env, limit=1).get("facets")
        assert table(dev_env, limit=1, facets="true")["facets"]

    def test_keys(self, dev_env):
        facets = table(dev_env, limit=1, facets="true")["facets"]
        assert {"param", "review_status", "severity", "year"} <= set(facets)

    def test_param_counts_sum_to_total(self, dev_env):
        body = table(dev_env, limit=1, facets="true")
        assert sum(body["facets"]["param"].values()) == body["total"]
        assert sum(body["facets"]["review_status"].values()) == body["total"]

    def test_year_counts_match_data(self, dev_env):
        facets = table(dev_env, limit=1, facets="true")["facets"]
        rows, _ = table_pages(dev_env.client, 1000, corpus_id=dev_env.corpus_id)
        from collections import Counter

        cnt = Counter(str(r["year"]) for r in rows if r["year"] is not None)
        assert {k: v for k, v in facets["year"].items()} == dict(cnt)

    def test_facets_respect_filters(self, dev_env):
        body = table(dev_env, param="pce", facets="true", limit=1)
        assert set(body["facets"]["param"]) == {"pce"}
        assert body["facets"]["param"]["pce"] == body["total"]

    def test_facets_do_not_depend_on_page(self, dev_env):
        a = table(dev_env, facets="true", limit=5)["facets"]
        b = table(dev_env, facets="true", limit=500)["facets"]
        assert a == b

    def test_severity_facet_counts_facts_with_issues(self, dev_env):
        body = table(dev_env, facets="true", limit=1)
        assert sum(body["facets"]["severity"].values()) >= 1
        assert set(body["facets"]["severity"]) <= {"error", "warn", "info"}

    def test_review_facet_changes_after_accept(self, small_env):
        fid = table(small_env, limit=1)["data"][0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="accepted")
        facets = table(small_env, facets="true", limit=1)["facets"]["review_status"]
        assert facets.get("accepted") == 1


class TestQueryLanguage:
    def test_num_and_year(self, dev_env):
        body = table(dev_env, q="КПД выше 18 после 2022", limit=1000)
        kinds = [f["kind"] for f in body["filters"]]
        assert kinds == ["num", "year"]
        num, year = body["filters"]
        assert (num["param"], num["op"], num["value"]) == ("pce", ">", 18)
        assert (year["op"], year["value"]) == (">", 2022)
        assert body["unparsed"] == ""

    def test_q_selects_samples_above_threshold(self, dev_env):
        """Остаются факты образцов, у которых PCE строго больше 18, в статьях после 2022."""
        body = table(dev_env, q="КПД выше 18 после 2022", limit=1000)
        assert body["total"] > 0
        assert all(r["year"] > 2022 for r in body["data"])
        pce = table(dev_env, param="pce", limit=1000)["data"]
        ok = {(r["paper_id"], r["row_id"]) for r in pce if r["normalized_value"] > 18 and r["year"] > 2022}
        got = {(r["paper_id"], r["row_id"]) for r in body["data"]}
        assert got == ok

    def test_q_only_unparsed(self, dev_env):
        body = table(dev_env, q="абракадабра")
        assert body["filters"] == [] and body["unparsed"] == "абракадабра"
        assert body["total"] == table(dev_env, limit=1)["total"], "нераспознанное не сужает выборку"

    def test_q_partially_unparsed(self, dev_env):
        body = table(dev_env, q="КПД выше 18 абракадабра")
        assert [f["kind"] for f in body["filters"]] == ["num"] and body["unparsed"] == "абракадабра"

    def test_q_empty(self, dev_env):
        body = table(dev_env, q="")
        assert body["filters"] == [] and body["unparsed"] == ""

    def test_q_issues(self, dev_env):
        body = table(dev_env, q="противоречия", limit=1000)
        assert [f["kind"] for f in body["filters"]] == ["issues"]
        assert body["data"]

    def test_q_topic(self, dev_env):
        body = table(dev_env, q="перовскит", limit=1000)
        assert [f["kind"] for f in body["filters"]] == ["topic"] and body["total"] > 0

    def test_q_with_explicit_filters(self, dev_env):
        a = table(dev_env, q="КПД выше 18", param="voc", limit=1000)
        assert {r["param"] for r in a["data"]} <= {"voc"}

    def test_q_filters_have_label_and_match(self, dev_env):
        for f in table(dev_env, q="КПД выше 18 после 2022", limit=1)["filters"]:
            assert f["id"] and f["label"] and f["match"]

    def test_q_year_only_matches_data(self, dev_env):
        body = table(dev_env, q="до 2021", limit=1000)
        assert body["data"] and all(r["year"] <= 2021 for r in body["data"])

    def test_q_nothing_matches(self, dev_env):
        body = table(dev_env, q="КПД выше 99")
        assert body["total"] == 0 and body["filters"][0]["kind"] == "num"

    def test_q_works_with_keyset(self, dev_env):
        rows, pages = table_pages(dev_env.client, 40, corpus_id=dev_env.corpus_id, q="КПД выше 15 после 2021",
                                  sort="value", dir="desc")
        assert len({r["fact_id"] for r in rows}) == len(rows) == pages[0]["total"]
        assert all(p["filters"] == pages[0]["filters"] for p in pages)
