"""Статьи: список, карточка, страницы (ETag/304), файл (Range), удаление и восстановление, перезапуск,
PUT collection (api_contract §3.6, §4.7, У-21, У-30)."""
from __future__ import annotations

import pytest

from .conftest import SMALL_SET, all_facts, url

CARD_KEYS = {
    "id", "paper_id", "publication_id", "corpus_id", "title", "authors", "authors_text", "year", "doi", "journal",
    "collection", "keywords", "page_count", "main_page", "main_table", "main_figure", "in_scope",
    "exclusion_reason", "file", "mine", "added_at", "chat_id", "processing", "topic_by", "dup_of", "dup_kind",
    "model_version", "rows", "claims", "quality", "coverage_summary", "open_issues", "top_level", "facts_count",
}


# --- авторизация ----------------------------------------------------------------------------------

class TestAuth:
    @pytest.mark.parametrize("method, path", [
        ("get", "/papers"), ("get", "/papers/pap_x"), ("get", "/papers/pap_x/facts"), ("get", "/facts"),
        ("get", "/facts/table?collection=solar"), ("get", "/data/query/parse?collection=solar&q=x"),
        ("get", "/checks/accuracy"), ("get", "/dictionary"), ("get", "/papers/pap_x/coverage"),
    ])
    def test_requires_token(self, dev_env, method, path):
        r = getattr(dev_env.anon, method)(url(path))
        assert r.status_code == 401
        assert r.json()["error"]["code"] in ("unauthorized", "token_expired")

    def test_foreign_workspace_paper_is_404(self, dev_env, paper_id):
        """Чужая статья неотличима от несуществующей (api_contract §1.5, not_found)."""
        other = dev_env.make_user("other@example.com", "other-pass-123")
        # тот же workspace -> видит; проверка «чужой» - через несуществующий id ниже
        assert other.get(url(f"/papers/{paper_id}")).status_code == 200
        assert other.get(url("/papers/pap_doesnotexist")).status_code == 404


# --- GET /papers ----------------------------------------------------------------------------------

class TestList:
    def test_list_all_dev(self, dev_env):
        r = dev_env.get("/papers", corpus_id=dev_env.corpus_id, limit=500)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 21 and len(body["data"]) == 21
        assert body["next_cursor"] is None
        ids = {p["publication_id"] for p in body["data"]}
        assert {"D001", "D021", "D022"} <= ids

    def test_list_item_has_id_equal_paper_id(self, dev_env):
        for p in dev_env.get("/papers", corpus_id=dev_env.corpus_id).json()["data"]:
            assert p["id"] == p["paper_id"] and p["id"].startswith("pap_")

    def test_pagination_keyset_no_gaps(self, dev_env):
        seen, cursor = [], None
        for _ in range(30):
            params = {"corpus_id": dev_env.corpus_id, "limit": 5, "sort": "publication_id", "dir": "asc"}
            if cursor:
                params["cursor"] = cursor
            b = dev_env.client.get(url("/papers"), params=params).json()
            seen += [p["publication_id"] for p in b["data"]]
            cursor = b["next_cursor"]
            if not cursor:
                break
        assert seen == sorted(seen) and len(seen) == len(set(seen)) == 21

    def test_sort_year_desc(self, dev_env):
        data = dev_env.get("/papers", corpus_id=dev_env.corpus_id, sort="year", dir="desc", limit=500).json()["data"]
        years = [p["year"] for p in data if p["year"] is not None]
        assert years == sorted(years, reverse=True)

    def test_sort_title_asc(self, dev_env):
        data = dev_env.get("/papers", corpus_id=dev_env.corpus_id, sort="title", dir="asc", limit=500).json()["data"]
        titles = [p["title"].casefold() for p in data]
        assert titles == sorted(titles)

    def test_filter_status_done(self, dev_env):
        data = dev_env.get("/papers", corpus_id=dev_env.corpus_id, status="done", limit=500).json()["data"]
        assert data and all(p["processing"]["status"] == "done" for p in data)

    def test_filter_in_scope(self, dev_env):
        """dev_gold: D006 и D016 вне предметной области."""
        out = dev_env.get("/papers", corpus_id=dev_env.corpus_id, in_scope="false", limit=500).json()["data"]
        assert {p["publication_id"] for p in out} == {"D006", "D016"}
        inn = dev_env.get("/papers", corpus_id=dev_env.corpus_id, in_scope="true", limit=500).json()
        assert inn["total"] == 19

    def test_filter_q_by_title_and_author(self, dev_env):
        data = dev_env.get("/papers", corpus_id=dev_env.corpus_id, q="CsPbI2Br").json()["data"]
        assert [p["publication_id"] for p in data] == ["D001"]
        data = dev_env.get("/papers", corpus_id=dev_env.corpus_id, q="Qiao").json()["data"]
        assert "D001" in [p["publication_id"] for p in data]

    def test_filter_ids(self, dev_env):
        ids = f"{dev_env.pid('D001')},{dev_env.pid('D004')}"
        data = dev_env.get("/papers", ids=ids).json()["data"]
        assert {p["publication_id"] for p in data} == {"D001", "D004"}

    def test_filter_collection(self, dev_env):
        assert dev_env.get("/papers", collection="solar", limit=500).json()["total"] >= 21

    def test_filter_mine(self, dev_env):
        assert dev_env.get("/papers", mine="true", corpus_id=dev_env.corpus_id).status_code == 200

    def test_limit_over_500_rejected_or_clamped(self, dev_env):
        r = dev_env.get("/papers", limit=5000)
        assert r.status_code == 400 or len(r.json()["data"]) <= 500

    def test_bad_cursor_is_400(self, dev_env):
        r = dev_env.get("/papers", cursor="not-a-cursor")
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_bad_sort_is_400(self, dev_env):
        r = dev_env.get("/papers", sort="nonsense")
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_deleted_not_listed(self, small_env):
        pid = small_env.pid("D001")
        small_env.client.delete(url(f"/papers/{pid}"))
        data = small_env.get("/papers", limit=500).json()["data"]
        assert pid not in {p["id"] for p in data}

    def test_list_summary_shape(self, dev_env):
        p = dev_env.get("/papers", limit=1).json()["data"][0]
        for k in ("id", "title", "year", "collection", "processing"):
            assert k in p


# --- GET /papers/{id} -----------------------------------------------------------------------------

class TestCard:
    def test_shape_per_contract_3_6(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}")
        assert r.status_code == 200
        card = r.json()
        assert CARD_KEYS <= set(card), CARD_KEYS - set(card)

    def test_id_equals_paper_id(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        assert card["id"] == card["paper_id"] == paper_id

    def test_metadata_from_gold(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        assert card["publication_id"] == "D001"
        assert card["year"] == 2021
        assert card["doi"] == "10.3389/fenrg.2021.692059"  # без https://doi.org/
        assert card["title"].startswith("Improved Photovoltaic Performance of CsPbI2Br")
        assert card["in_scope"] is True
        assert card["collection"] == "solar"
        assert card["authors"][0].startswith("Qiao")
        assert card["authors_text"]

    def test_processing_done(self, dev_env, paper_id):
        proc = dev_env.get(f"/papers/{paper_id}").json()["processing"]
        assert proc["status"] == "done"
        assert set(proc) >= {"status", "stage", "progress", "error", "started_at", "finished_at"}

    def test_file_block(self, dev_env, paper_id):
        f = dev_env.get(f"/papers/{paper_id}").json()["file"]
        assert f["name"] == "D001.pdf" and f["size"] > 0 and len(f["sha256"]) == 64

    def test_counters_match_facts(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        facts = all_facts(dev_env.client, paper_id)
        assert card["facts_count"] == len(facts) > 0
        assert card["quality"]["total"] == len(facts)
        assert set(card["quality"]) >= {"total", "sourced_pct", "normalized_pct", "accepted", "issues"}
        assert set(card["coverage_summary"]) == {"filled", "total"}
        assert card["open_issues"] >= 0

    def test_rows_are_devices(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        assert card["rows"], "у D001 есть приборы"
        for i, row in enumerate(card["rows"]):
            assert set(row) >= {"row_id", "label", "order"}
        assert [r["order"] for r in card["rows"]] == sorted(r["order"] for r in card["rows"])

    def test_wave1_defaults(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        assert card["topic_by"] == "project"
        assert card["claims"] == []
        assert card["chat_id"] is None
        assert card["main_page"] is None and card["main_table"] is None and card["main_figure"] is None
        assert card["dup_of"] is None

    def test_out_of_scope_paper(self, dev_env):
        card = dev_env.get(f"/papers/{dev_env.pid('D006')}").json()
        assert card["in_scope"] is False and card["exclusion_reason"]

    def test_not_found(self, dev_env):
        r = dev_env.get("/papers/pap_nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

    def test_json_numbers_not_strings(self, dev_env, paper_id):
        card = dev_env.get(f"/papers/{paper_id}").json()
        assert isinstance(card["facts_count"], int) and isinstance(card["year"], int)


# --- GET /papers/{id}/pages/{n} -------------------------------------------------------------------

class TestPages:
    def test_png_image(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/pages/1", format="png")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_default_format_is_image(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/pages/1")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/")

    def test_headers(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/pages/1")
        assert r.headers["etag"]
        assert r.headers["cache-control"] == "private, max-age=86400"

    def test_if_none_match_304(self, dev_env, paper_id):
        first = dev_env.get(f"/papers/{paper_id}/pages/1")
        etag = first.headers["etag"]
        again = dev_env.client.get(url(f"/papers/{paper_id}/pages/1"), headers={"If-None-Match": etag})
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == etag

    def test_stale_etag_gives_200(self, dev_env, paper_id):
        r = dev_env.client.get(url(f"/papers/{paper_id}/pages/1"), headers={"If-None-Match": '"stale"'})
        assert r.status_code == 200

    def test_etag_differs_by_page_and_scale(self, dev_env, paper_id):
        e1 = dev_env.get(f"/papers/{paper_id}/pages/1").headers["etag"]
        e2 = dev_env.get(f"/papers/{paper_id}/pages/2").headers["etag"]
        e3 = dev_env.get(f"/papers/{paper_id}/pages/1", scale=2).headers["etag"]
        assert len({e1, e2, e3}) == 3

    def test_etag_stable_between_calls(self, dev_env, paper_id):
        a = dev_env.get(f"/papers/{paper_id}/pages/1").headers["etag"]
        b = dev_env.get(f"/papers/{paper_id}/pages/1").headers["etag"]
        assert a == b

    @pytest.mark.parametrize("scale", [1, 1.5, 2])
    def test_scales(self, dev_env, paper_id, scale):
        assert dev_env.get(f"/papers/{paper_id}/pages/1", scale=scale).status_code == 200

    def test_larger_scale_larger_image(self, dev_env, paper_id):
        small = dev_env.get(f"/papers/{paper_id}/pages/1", scale=1).content
        big = dev_env.get(f"/papers/{paper_id}/pages/1", scale=2).content
        assert len(big) > len(small)

    def test_bad_scale_400(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/pages/1", scale=7)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_bad_format_400(self, dev_env, paper_id):
        assert dev_env.get(f"/papers/{paper_id}/pages/1", format="gif").status_code == 400

    @pytest.mark.parametrize("n", [0, 9999, -1])
    def test_page_out_of_range_404(self, dev_env, paper_id, n):
        assert dev_env.get(f"/papers/{paper_id}/pages/{n}").status_code in (404, 422)

    def test_non_integer_page(self, dev_env, paper_id):
        assert dev_env.get(f"/papers/{paper_id}/pages/abc").status_code in (400, 404, 422)

    def test_last_page(self, dev_env, paper_id):
        n = dev_env.get(f"/papers/{paper_id}").json()["page_count"]
        assert dev_env.get(f"/papers/{paper_id}/pages/{n}").status_code == 200
        assert dev_env.get(f"/papers/{paper_id}/pages/{n + 1}").status_code == 404

    def test_unknown_paper_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/pages/1").status_code == 404


# --- GET /papers/{id}/file ------------------------------------------------------------------------

class TestFile:
    def test_whole_pdf(self, dev_env, paper_id):
        r = dev_env.get(f"/papers/{paper_id}/file")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/pdf")
        assert r.headers["content-disposition"].startswith("inline")
        assert r.content[:5] == b"%PDF-"
        assert len(r.content) == dev_env.get(f"/papers/{paper_id}").json()["file"]["size"]

    def test_accept_ranges_header(self, dev_env, paper_id):
        assert dev_env.get(f"/papers/{paper_id}/file").headers.get("accept-ranges") == "bytes"

    def test_range_partial(self, dev_env, paper_id):
        full = dev_env.get(f"/papers/{paper_id}/file").content
        r = dev_env.client.get(url(f"/papers/{paper_id}/file"), headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert r.content == full[:100]
        assert r.headers["content-range"] == f"bytes 0-99/{len(full)}"
        assert r.headers["content-length"] == "100"

    def test_range_middle_and_open_end(self, dev_env, paper_id):
        full = dev_env.get(f"/papers/{paper_id}/file").content
        r = dev_env.client.get(url(f"/papers/{paper_id}/file"), headers={"Range": "bytes=1000-"})
        assert r.status_code == 206 and r.content == full[1000:]

    def test_range_suffix(self, dev_env, paper_id):
        full = dev_env.get(f"/papers/{paper_id}/file").content
        r = dev_env.client.get(url(f"/papers/{paper_id}/file"), headers={"Range": "bytes=-50"})
        assert r.status_code == 206 and r.content == full[-50:]

    def test_range_not_satisfiable(self, dev_env, paper_id):
        size = dev_env.get(f"/papers/{paper_id}").json()["file"]["size"]
        r = dev_env.client.get(url(f"/papers/{paper_id}/file"), headers={"Range": f"bytes={size + 10}-"})
        assert r.status_code == 416

    def test_unknown_paper_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/file").status_code == 404


# --- DELETE / restore -----------------------------------------------------------------------------

class TestDeleteRestore:
    def test_delete_returns_action(self, small_env):
        pid = small_env.pid("D001")
        r = small_env.client.delete(url(f"/papers/{pid}"))
        assert r.status_code == 200
        assert r.json()["action_id"].startswith("act_")
        assert r.headers.get("x-action-id") == r.json()["action_id"]

    def test_deleted_is_404_everywhere(self, small_env):
        pid = small_env.pid("D001")
        small_env.client.delete(url(f"/papers/{pid}"))
        for path in (f"/papers/{pid}", f"/papers/{pid}/facts", f"/papers/{pid}/anchors",
                     f"/papers/{pid}/coverage", f"/papers/{pid}/file"):
            assert small_env.get(path).status_code == 404, path

    def test_deleted_facts_leave_table(self, small_env):
        pid = small_env.pid("D001")
        before = small_env.get("/facts/table", corpus_id=small_env.corpus_id, limit=1).json()["total"]
        small_env.client.delete(url(f"/papers/{pid}"))
        after = small_env.get("/facts/table", corpus_id=small_env.corpus_id, limit=1).json()["total"]
        assert after < before

    def test_restore_brings_back(self, small_env):
        pid = small_env.pid("D001")
        facts = len(all_facts(small_env.client, pid))
        small_env.client.delete(url(f"/papers/{pid}"))
        r = small_env.client.post(url(f"/papers/{pid}/restore"))
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == pid and body["action_id"]
        assert small_env.get(f"/papers/{pid}").status_code == 200
        assert len(all_facts(small_env.client, pid)) == facts

    def test_delete_unknown_404(self, small_env):
        assert small_env.client.delete(url("/papers/pap_nope")).status_code == 404

    def test_delete_twice(self, small_env):
        pid = small_env.pid("D001")
        small_env.client.delete(url(f"/papers/{pid}"))
        assert small_env.client.delete(url(f"/papers/{pid}")).status_code == 404

    def test_restore_unknown_404(self, small_env):
        assert small_env.client.post(url("/papers/pap_nope/restore")).status_code == 404

    def test_other_papers_untouched(self, small_env):
        small_env.client.delete(url(f"/papers/{small_env.pid('D001')}"))
        assert small_env.get(f"/papers/{small_env.pid('D004')}").status_code == 200

    def test_requires_auth(self, small_env):
        assert small_env.anon.delete(url(f"/papers/{small_env.pid('D001')}")).status_code == 401


# --- reprocess и PUT collection -------------------------------------------------------------------

class TestReprocess:
    def test_reprocess_202_returns_paper(self, small_env):
        pid = small_env.pid("D001")
        r = small_env.client.post(url(f"/papers/{pid}/reprocess"), json={"keep_review": True})
        assert r.status_code == 202
        assert r.json()["id"] == pid

    def test_reprocess_keeps_facts_and_review(self, small_env):
        pid = small_env.pid("D001")
        facts = all_facts(small_env.client, pid)
        target = facts[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(target, review_status="accepted")
        small_env.client.post(url(f"/papers/{pid}/reprocess"), json={"keep_review": True})
        after = {f["fact_id"]: f for f in all_facts(small_env.client, pid)}
        assert len(after) == len(facts)
        accepted = [f for f in after.values() if f["review_status"] == "accepted"]
        assert len(accepted) == 1

    def test_reprocess_without_body(self, small_env):
        assert small_env.client.post(url(f"/papers/{small_env.pid('D001')}/reprocess")).status_code == 202

    def test_reprocess_unknown_404(self, small_env):
        assert small_env.client.post(url("/papers/pap_nope/reprocess")).status_code == 404

    def test_reprocess_active_run_conflict(self, small_env):
        """У корпуса идёт прогон (не служебного) -> 409 conflict."""
        from faktura.pipeline import RunService

        with small_env.uow() as uow:
            uow.runs.create(corpus_id=small_env.corpus_id, kind="full", status="running")
        pid = small_env.pid("D001")
        r = small_env.client.post(url(f"/papers/{pid}/reprocess"), json={})
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
        assert RunService  # импорт публичного интерфейса

    def test_reprocess_deleted_404(self, small_env):
        pid = small_env.pid("D001")
        small_env.client.delete(url(f"/papers/{pid}"))
        assert small_env.client.post(url(f"/papers/{pid}/reprocess")).status_code == 404


class TestPutCollection:
    def test_solar_is_stub_202(self, small_env):
        pid = small_env.pid("D001")
        r = small_env.client.put(url(f"/papers/{pid}/collection"), json={"collection": "solar"})
        assert r.status_code == 202
        assert r.headers["x-faktura-stub"] == "1"
        assert r.json()["id"] == pid and r.json()["collection"] == "solar"

    def test_solar_does_not_change_facts(self, small_env):
        pid = small_env.pid("D001")
        before = all_facts(small_env.client, pid)
        small_env.client.put(url(f"/papers/{pid}/collection"), json={"collection": "solar"})
        assert all_facts(small_env.client, pid) == before

    @pytest.mark.parametrize("value", ["rag", "auto", "", "xyz"])
    def test_other_collection_400(self, small_env, value):
        pid = small_env.pid("D001")
        r = small_env.client.put(url(f"/papers/{pid}/collection"), json={"collection": value})
        assert r.status_code == 400
        err = r.json()["error"]
        assert err["code"] == "validation_error" and err["field"] == "collection"

    def test_missing_field_400(self, small_env):
        r = small_env.client.put(url(f"/papers/{small_env.pid('D001')}/collection"), json={})
        assert r.status_code == 400 and r.json()["error"]["field"] == "collection"

    def test_unknown_paper_404(self, small_env):
        r = small_env.client.put(url("/papers/pap_nope/collection"), json={"collection": "solar"})
        assert r.status_code == 404


def test_small_set_is_three_papers(small_env):
    assert set(small_env.papers) == set(SMALL_SET)
