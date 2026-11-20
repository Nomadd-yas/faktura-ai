"""Факты: GET /facts, /facts/{id}, /papers/{id}/facts, Fact.exportable (api_contract §3.7, §4.8, У-13, У-35)."""
from __future__ import annotations

import pytest

from .conftest import all_facts, url

FACT_KEYS = {
    "fact_id", "paper_id", "publication_id", "row_id", "row_label", "param", "param_label", "metric_name", "target",
    "raw_value", "raw_unit", "normalized_value", "normalized_unit", "metric_value", "metric_unit", "normalized_text",
    "norm_status", "extraction_status", "review_status", "corrected_value", "corrected_raw", "value", "display",
    "confidence", "added_by", "result_origin", "cited_publication_id", "aggregation", "spread_value", "n_devices",
    "time_point", "time_unit", "measurement_id", "material_id", "scan_direction", "duplicate_of", "source_page",
    "source_section", "source_table", "source_figure", "source_fragment", "source_kind", "source_ref", "source_where",
    "quote_found", "anchor_id", "cross", "note", "exportable", "issue_ids", "model_version", "created_at",
    "updated_at",
}


def draft(**kw):
    from faktura.domain.entities import FactDraft

    base = dict(ref="m1", target={"entity": "results", "id": "RM1", "field": "metric_value"}, param="pce",
                metric_name="PCE", raw_value="12.5", raw_unit="%", source_kind="manual", added_by="manual")
    base.update(kw)
    return FactDraft(**base)


# --- GET /papers/{id}/facts -----------------------------------------------------------------------

class TestPaperFacts:
    def test_form(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/facts").json()
        assert set(body) == {"data"}
        assert FACT_KEYS <= set(body["data"][0]), FACT_KEYS - set(body["data"][0])

    def test_count_equals_gold_results(self, dev_env, paper_id):
        """Мок отдаёт все 47 записей D001 (в том числе дубли)."""
        facts = all_facts(dev_env.client, paper_id)
        assert len(facts) == 47 and all(f["paper_id"] == paper_id for f in facts)

    def test_includes_rejected_by_default(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="rejected")
        facts = all_facts(small_env.client, pid)
        assert fid in {f["fact_id"] for f in facts}
        assert next(f for f in facts if f["fact_id"] == fid)["review_status"] == "rejected"

    def test_ids_unique_and_prefixed(self, dev_env, paper_id):
        ids = [f["fact_id"] for f in all_facts(dev_env.client, paper_id)]
        assert len(ids) == len(set(ids)) and all(i.startswith("fct_") for i in ids)

    def test_no_pagination_keys(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/facts").json()
        assert "next_cursor" not in body and "total" not in body

    def test_unknown_paper_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/facts").status_code == 404


# --- GET /facts/{id} ------------------------------------------------------------------------------

class TestFactCard:
    def test_fact_from_gold_d001_pce(self, dev_env, paper_id):
        """D001 R004: PCE 12.89 %, стр. 5, Table 1."""
        facts = all_facts(dev_env.client, paper_id)
        f = next(f for f in facts if f["target"]["id"] == "R004")
        assert f["metric_name"] == "PCE" and f["param"] == "pce"
        assert f["raw_value"] == "12.89" and f["raw_unit"] == "%"
        assert f["normalized_value"] == 12.89 and f["normalized_unit"] == "%"
        assert f["source_page"] == 5 and f["source_table"] == "TABLE 1"
        assert f["source_fragment"] == "Table 1, строка pristine"
        assert f["extraction_status"] == "extracted" and f["result_origin"] == "primary"
        assert f["target"] == {"entity": "result", "id": "R004", "field": "metric_value"}

    def test_card_equals_list_entry(self, dev_env, paper_id):
        f = all_facts(dev_env.client, paper_id)[3]
        assert dev_env.get(f"/facts/{f['fact_id']}").json() == f

    def test_value_display(self, dev_env, paper_id):
        f = next(f for f in all_facts(dev_env.client, paper_id) if f["target"]["id"] == "R004")
        assert f["value"] == 12.89 and f["display"] == "12,89 %"

    def test_value_prefers_corrected(self, small_env):
        pid = small_env.pid("D001")
        f = all_facts(small_env.client, pid)[0]
        with small_env.uow() as uow:
            uow.facts.update_review(f["fact_id"], review_status="accepted", corrected_value=7, corrected_raw="7")
        card = small_env.get(f"/facts/{f['fact_id']}").json()
        assert card["value"] == 7 and card["corrected_value"] == 7 and card["review_status"] == "accepted"

    def test_numbers_are_json_numbers(self, dev_env, paper_id):
        for f in all_facts(dev_env.client, paper_id):
            for k in ("normalized_value", "metric_value", "value", "confidence"):
                assert f[k] is None or isinstance(f[k], (int, float)), (k, f[k])

    def test_no_float_noise(self, dev_env, paper_id):
        f = next(f for f in all_facts(dev_env.client, paper_id) if f["raw_value"] == "1197")
        assert f["normalized_value"] == 1.197
        assert "1.1970000" not in dev_env.client.get(url(f"/facts/{f['fact_id']}")).text

    def test_issue_ids_reference_real_issues(self, dev_env, paper_id):
        issues = {i["issue_id"] for i in dev_env.get(f"/papers/{paper_id}/issues").json()["data"]}
        for f in all_facts(dev_env.client, paper_id):
            assert set(f["issue_ids"]) <= issues

    def test_text_values_in_normalized_text(self, dev_env):
        """Для текстовых фактов normalized_text != None, normalized_value None."""
        for pid in dev_env.papers.values():
            for f in all_facts(dev_env.client, pid):
                if f["normalized_text"] is not None:
                    assert f["normalized_value"] is None

    def test_404(self, dev_env):
        r = dev_env.get("/facts/fct_nope")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"

    def test_deleted_paper_fact_404(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        small_env.client.delete(url(f"/papers/{pid}"))
        assert small_env.get(f"/facts/{fid}").status_code == 404


# --- Fact.exportable ------------------------------------------------------------------------------

class TestExportable:
    def test_auto_extracted_exportable(self, dev_env, paper_id):
        f = next(f for f in all_facts(dev_env.client, paper_id) if f["extraction_status"] == "extracted")
        assert f["exportable"] is True

    def test_rejected_not_exportable(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="rejected")
        assert small_env.get(f"/facts/{fid}").json()["exportable"] is False

    def test_restore_makes_exportable_again(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="rejected")
            uow.facts.update_review(fid, review_status="new")
        assert small_env.get(f"/facts/{fid}").json()["exportable"] is True

    def test_absent_not_exportable(self, small_env):
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            fact = uow.facts.add_manual(pid, draft(ref="abs", extraction_status="absent", raw_value=None,
                                                   raw_unit=None, source_page=3, source_fragment="quote",
                                                   source_kind="text", added_by="auto"))
        assert small_env.get(f"/facts/{fact.id}").json()["exportable"] is False

    def test_present_not_extractable_has_flag_consistent(self, dev_env):
        """У эталона 6 записей present_not_extractable: они выгружаются (У-13 про absent)."""
        found = [f for pid in dev_env.papers.values() for f in all_facts(dev_env.client, pid)
                 if f["extraction_status"] == "present_not_extractable"]
        assert found and all(f["exportable"] is True for f in found)

    def test_manual_without_quote_not_exportable(self, small_env):
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            fact = uow.facts.add_manual(pid, draft(source_page=None, source_fragment=None))
        body = small_env.get(f"/facts/{fact.id}").json()
        assert body["exportable"] is False and body["added_by"] == "manual"

    def test_manual_with_page_and_quote_exportable(self, small_env):
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            fact = uow.facts.add_manual(pid, draft(source_page=5, source_fragment="PCE = 12.5%"))
        assert small_env.get(f"/facts/{fact.id}").json()["exportable"] is True

    def test_manual_with_page_and_ref_exportable(self, small_env):
        """У-35: достаточно source.page и source.ref (таблица/рисунок)."""
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            fact = uow.facts.add_manual(pid, draft(source_page=5, source_fragment=None, source_table="Table 1"))
        assert small_env.get(f"/facts/{fact.id}").json()["exportable"] is True

    def test_manual_with_quote_without_page_not_exportable(self, small_env):
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            fact = uow.facts.add_manual(pid, draft(source_page=None, source_fragment="PCE = 12.5%"))
        assert small_env.get(f"/facts/{fact.id}").json()["exportable"] is False

    def test_exportable_filter_in_facts(self, small_env):
        pid = small_env.pid("D001")
        with small_env.uow() as uow:
            uow.facts.add_manual(pid, draft(source_page=None, source_fragment=None))
        no = small_env.get("/facts", paper_id=pid, exportable="false", limit=500).json()
        assert no["total"] == 1 and no["data"][0]["exportable"] is False


# --- GET /facts -----------------------------------------------------------------------------------

class TestFactsList:
    def test_form(self, dev_env):
        body = dev_env.get("/facts", corpus_id=dev_env.corpus_id, limit=10).json()
        assert set(body) >= {"data", "total", "next_cursor"}
        assert len(body["data"]) == 10 and body["total"] > 10 and body["next_cursor"]

    def test_default_hides_rejected(self, small_env):
        pid = small_env.pid("D001")
        total = small_env.get("/facts", paper_id=pid, limit=500).json()["total"]
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="rejected")
        assert small_env.get("/facts", paper_id=pid, limit=500).json()["total"] == total - 1
        shown = small_env.get("/facts", paper_id=pid, review_status="rejected", limit=500).json()
        assert [f["fact_id"] for f in shown["data"]] == [fid]

    def test_param_filter(self, dev_env):
        data = dev_env.get("/facts", param="pce", corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        assert data and {f["param"] for f in data} == {"pce"}

    def test_param_repeatable(self, dev_env):
        data = dev_env.client.get(url("/facts"), params=[("param", "pce"), ("param", "voc"),
                                                         ("corpus_id", dev_env.corpus_id), ("limit", 500)]).json()["data"]
        assert {f["param"] for f in data} == {"pce", "voc"}

    def test_min_max_on_value(self, dev_env):
        data = dev_env.get("/facts", param="pce", min=15, max=18, corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        assert data and all(15 <= f["value"] <= 18 for f in data)

    def test_paper_id_repeatable(self, dev_env):
        ids = [dev_env.pid("D001"), dev_env.pid("D004")]
        data = dev_env.client.get(url("/facts"), params=[("paper_id", i) for i in ids] + [("limit", 500)]).json()["data"]
        assert {f["paper_id"] for f in data} == set(ids)

    def test_row_id(self, dev_env, paper_id):
        data = dev_env.get("/facts", paper_id=paper_id, row_id="DV1", limit=500).json()["data"]
        assert data and {f["row_id"] for f in data} == {"DV1"}

    def test_source_kind(self, dev_env):
        data = dev_env.get("/facts", source_kind="table", corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        assert data and {f["source_kind"] for f in data} == {"table"}

    def test_min_confidence(self, dev_env):
        data = dev_env.get("/facts", min_confidence=0.95, corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        assert all(f["confidence"] >= 0.95 for f in data)

    def test_added_by(self, small_env):
        with small_env.uow() as uow:
            uow.facts.add_manual(small_env.pid("D001"), draft())
        data = small_env.get("/facts", added_by="manual", corpus_id=small_env.corpus_id).json()["data"]
        assert len(data) == 1 and data[0]["added_by"] == "manual"

    def test_has_issues(self, dev_env):
        with_issues = dev_env.get("/facts", has_issues="true", corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        assert with_issues and all(f["issue_ids"] for f in with_issues)

    def test_extraction_status(self, dev_env):
        data = dev_env.get("/facts", extraction_status="present_not_extractable", corpus_id=dev_env.corpus_id,
                           limit=500).json()
        assert data["total"] == 6

    def test_q_text(self, dev_env, paper_id):
        data = dev_env.get("/facts", q="SrCl2", paper_id=paper_id, limit=500).json()["data"]
        assert data and all("srcl2" in (f["source_fragment"] or "").lower() or "srcl2" in (f["display"] or "").lower()
                            or "srcl2" in (f["raw_value"] or "").lower() for f in data)

    def test_metric_name_and_result_origin(self, dev_env):
        data = dev_env.get("/facts", metric_name="PCE", result_origin="cited", corpus_id=dev_env.corpus_id,
                           limit=500).json()["data"]
        assert data and all(f["metric_name"] == "PCE" and f["result_origin"] == "cited" for f in data)

    @pytest.mark.parametrize("sort", ["paper", "row", "param", "value", "confidence", "created_at", "risk"])
    def test_sorts_accepted(self, dev_env, sort):
        assert dev_env.get("/facts", sort=sort, corpus_id=dev_env.corpus_id, limit=5).status_code == 200

    def test_sort_value_orders(self, dev_env):
        data = dev_env.get("/facts", sort="value", dir="asc", param="pce", corpus_id=dev_env.corpus_id,
                           limit=500).json()["data"]
        vals = [f["value"] for f in data]
        assert vals == sorted(vals)

    def test_sort_confidence_desc(self, dev_env):
        data = dev_env.get("/facts", sort="confidence", dir="desc", corpus_id=dev_env.corpus_id, limit=500).json()["data"]
        confs = [f["confidence"] for f in data if f["confidence"] is not None]
        assert confs == sorted(confs, reverse=True)

    def test_unknown_sort_400(self, dev_env):
        r = dev_env.get("/facts", sort="bogus", corpus_id=dev_env.corpus_id)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_cursor_walk_no_gaps(self, dev_env):
        ids, cursor = [], None
        while True:
            params = {"corpus_id": dev_env.corpus_id, "limit": 200, "sort": "paper"}
            if cursor:
                params["cursor"] = cursor
            b = dev_env.client.get(url("/facts"), params=params).json()
            ids += [f["fact_id"] for f in b["data"]]
            cursor = b["next_cursor"]
            if not cursor:
                break
        assert len(ids) == len(set(ids)) == b["total"]

    def test_bad_cursor_400(self, dev_env):
        assert dev_env.get("/facts", cursor="zzz", corpus_id=dev_env.corpus_id).status_code == 400

    def test_limit_clamped(self, dev_env):
        r = dev_env.get("/facts", limit=100000, corpus_id=dev_env.corpus_id)
        assert r.status_code == 400 or len(r.json()["data"]) <= 500
