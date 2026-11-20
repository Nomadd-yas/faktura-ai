"""P2-R: /checks/accuracy, POST /admin/gold, GET /dictionary, POST /admin/reprocess
(api_contract §4.14, У-20; spec/04 §4.13; spec/14 §14.7)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from faktura.domain.tables import EVAL_METRICS

from .conftest import all_facts, url

ACCURACY_KEYS = {"collection", "gold_dataset", "model_version", "total", "normalized", "linked", "by_param",
                 "by_source", "by_paper", "errors"}
TOTAL_KEYS = {"gold", "extracted", "correct", "p", "r", "f1"}
SOURCE_KINDS = {"table", "text", "figure", "abstract", "supplementary"}


def gold_results(case_dir: Path) -> list[dict]:
    with (case_dir / "dev_gold" / "results.csv").open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def check_form(body: dict) -> None:
    assert ACCURACY_KEYS <= set(body), ACCURACY_KEYS - set(body)
    assert TOTAL_KEYS <= set(body["total"])
    for row in body["by_param"]:
        assert {"param", "label", *TOTAL_KEYS} <= set(row)
    for row in body["by_source"]:
        assert {"kind", "label", *TOTAL_KEYS} <= set(row) and row["kind"] in SOURCE_KINDS
    for row in body["by_paper"]:
        assert {"paper_id", "short", *TOTAL_KEYS} <= set(row)
    for row in body["errors"]:
        assert {"kind", "kind_label", "paper_id", "row_id", "param", "fact_id", "gold", "got", "note"} <= set(row)
        assert row["kind"] in ("miss", "extra", "value", "unit", "link")


# --- GET /checks/accuracy -------------------------------------------------------------------------

class TestAccuracy:
    def test_no_gold_404(self, dev_env):
        """Эталон не загружен -> 404 not_found «Эталонная разметка не загружена»."""
        r = dev_env.get("/checks/accuracy", collection="solar")
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "not_found" and "Эталон" in err["message"]

    def test_requires_auth(self, dev_env):
        assert dev_env.anon.get(url("/checks/accuracy")).status_code == 401

    def test_gold_via_uow_gives_form_and_perfect_scores(self, acc_env, case_dir):
        """Эталон загружен через uow.gold.load_from_dir; на моке (отдаёт dev_gold) P = R = 1, linked: null."""
        assert acc_env.get("/checks/accuracy", collection="solar").status_code == 404
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        r = acc_env.get("/checks/accuracy", collection="solar", corpus_id=acc_env.corpus_id)
        assert r.status_code == 200
        body = r.json()
        check_form(body)
        assert body["collection"] == "solar"
        assert body["linked"] is None, "у эталона нет bbox -> linked: null"
        t = body["total"]
        assert t["p"] == t["r"] == t["f1"] == 1.0
        assert t["gold"] == t["extracted"] == t["correct"] > 0
        assert body["errors"] == []
        assert body["normalized"] == 1.0

    def test_only_11_metrics_counted(self, acc_env, case_dir):
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        body = acc_env.get("/checks/accuracy", collection="solar").json()
        labels = {r["param"].lower() for r in body["by_param"]}
        assert labels <= {m.lower() for m in EVAL_METRICS}
        expected = sum(1 for r in gold_results(case_dir) if r["metric_name"] in EVAL_METRICS)
        assert 0 < body["total"]["gold"] <= expected

    def test_by_param_sums_to_total(self, acc_env, case_dir):
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        body = acc_env.get("/checks/accuracy", collection="solar").json()
        for key in ("gold", "extracted", "correct"):
            assert sum(r[key] for r in body["by_param"]) == body["total"][key]
            assert sum(r[key] for r in body["by_source"]) == body["total"][key]
            assert sum(r[key] for r in body["by_paper"]) == body["total"][key]

    def test_by_paper_ids_are_real(self, acc_env, case_dir):
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        body = acc_env.get("/checks/accuracy", collection="solar").json()
        assert {r["paper_id"] for r in body["by_paper"]} <= set(acc_env.papers.values())

    def test_error_after_corrupting_a_fact(self, acc_env, case_dir):
        """Исправленное значение - расхождение с эталоном: P и R падают, в errors - kind 'value'."""
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        f = next(f for f in all_facts(acc_env.client, acc_env.pid("D001")) if f["metric_name"] == "PCE")
        with acc_env.uow() as uow:
            uow.facts.update_review(f["fact_id"], review_status="accepted", corrected_value=1, corrected_raw="1")
        body = acc_env.get("/checks/accuracy", collection="solar").json()
        assert body["total"]["correct"] == body["total"]["gold"] - 1
        assert body["total"]["p"] < 1.0 and body["total"]["r"] < 1.0
        assert any(e["kind"] == "value" and e["fact_id"] == f["fact_id"] for e in body["errors"])

    def test_non_admin_can_read(self, acc_env, case_dir):
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        reader = acc_env.make_user("acc-reader@example.com")
        assert reader.get(url("/checks/accuracy"), params={"collection": "solar"}).status_code == 200

    def test_unknown_collection(self, acc_env):
        r = acc_env.get("/checks/accuracy", collection="rag")
        assert r.status_code in (400, 404)

    def test_unknown_corpus_404(self, acc_env, case_dir):
        with acc_env.uow() as uow:
            uow.gold.load_from_dir("dev", case_dir / "dev_gold")
        assert acc_env.get("/checks/accuracy", corpus_id="crp_nope").status_code == 404

    def test_via_admin_gold_same_result(self, small_env):
        """Тот же результат, если эталон загружен через POST /admin/gold {dataset:'dev', dir:'dev_gold'}."""
        assert small_env.get("/checks/accuracy").status_code == 404
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"})
        assert r.status_code == 200
        body = small_env.get("/checks/accuracy", collection="solar").json()
        check_form(body)
        assert body["linked"] is None
        # обработаны только 3 из 21 статей: precision по ним = 1, полнота - по всему эталону либо по обработанным
        assert body["total"]["p"] == 1.0 and body["total"]["correct"] == body["total"]["extracted"]


# --- POST /admin/gold -----------------------------------------------------------------------------

class TestAdminGold:
    def test_load_dir(self, small_env):
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"})
        assert r.status_code == 200
        assert r.json() == {"dataset": "dev", "papers": 21, "results": 659}

    def test_not_a_stub(self, small_env):
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"})
        assert r.headers.get("x-faktura-stub") is None

    def test_reload_is_idempotent(self, small_env):
        a = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"}).json()
        b = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"}).json()
        assert a == b
        with small_env.uow() as uow:
            assert uow.gold.get("dev") is not None

    def test_non_admin_403(self, small_env):
        reader = small_env.make_user()
        r = reader.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"})
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"
        with small_env.uow() as uow:
            assert uow.gold.get("dev") is None, "эталон не должен загрузиться"

    def test_anon_401(self, small_env):
        assert small_env.anon.post(url("/admin/gold"), json={"dataset": "dev", "dir": "dev_gold"}).status_code == 401

    def test_missing_dir_400_or_404(self, small_env):
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": "no_such_dir"})
        assert r.status_code in (400, 404)

    def test_dir_outside_case_dir_rejected(self, small_env, tmp_path):
        """Каталог берётся из корня кейса на сервере; произвольные пути и «..» не принимаются."""
        for bad in ("..", "../../..", str(tmp_path), "dev_gold/../../"):
            r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": bad})
            assert r.status_code in (400, 403, 404), bad

    def test_missing_dataset_400(self, small_env):
        r = small_env.client.post(url("/admin/gold"), json={"dir": "dev_gold"})
        assert r.status_code == 400 and r.json()["error"]["field"] == "dataset"

    def test_neither_dir_nor_items_400(self, small_env):
        assert small_env.client.post(url("/admin/gold"), json={"dataset": "dev"}).status_code == 400

    def test_incomplete_dir_400(self, small_env, tmp_path):
        """Каталог без 7 CSV нельзя принять за эталон."""
        case = small_env.settings.case_dir
        empty = case / "tools"  # есть, но без CSV
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "dir": empty.name})
        assert r.status_code in (400, 404)

    def test_items_variant_is_stub(self, small_env):
        r = small_env.client.post(url("/admin/gold"), json={"dataset": "dev", "items": []})
        assert r.status_code in (200, 201, 202) and r.headers.get("x-faktura-stub") == "1"

    def test_non_json_body_400(self, small_env):
        r = small_env.client.post(url("/admin/gold"), content=b"not json", headers={"Content-Type": "application/json"})
        assert r.status_code in (400, 422)


# --- GET /dictionary ------------------------------------------------------------------------------

class TestDictionary:
    def test_shape(self, dev_env):
        body = dev_env.get("/dictionary", collection="solar").json()
        assert set(body) == {"params", "units", "materials", "conditions"}
        assert all(isinstance(v, list) for v in body.values())

    def test_taken_from_normalizer(self, dev_env):
        snap = dev_env.services.normalizer.dictionary()
        body = dev_env.get("/dictionary", collection="solar").json()
        assert len(body["params"]) == len(snap.metrics)
        assert len(body["units"]) == len(snap.units)
        assert len(body["materials"]) == len(snap.materials)
        assert len(body["conditions"]) == len(snap.spectra)

    def test_params_cover_11_metrics(self, dev_env):
        params = {p["param"].lower() for p in dev_env.get("/dictionary", collection="solar").json()["params"]}
        assert {m.lower() for m in EVAL_METRICS} <= params

    def test_param_item_shape(self, dev_env):
        item = next(p for p in dev_env.get("/dictionary", collection="solar").json()["params"] if p["param"] == "voc")
        assert {"param", "label", "unit", "synonyms", "usage"} <= set(item)
        assert item["unit"] == "V"
        assert item["synonyms"] and set(item["synonyms"][0]) == {"text", "custom"}
        assert all(s["custom"] is False for s in item["synonyms"])
        assert {"facts", "papers", "total_papers"} <= set(item["usage"])

    def test_usage_counts_from_corpus(self, dev_env):
        item = next(p for p in dev_env.get("/dictionary", collection="solar").json()["params"] if p["param"] == "pce")
        assert item["usage"]["facts"] >= 205 - 0 and item["usage"]["papers"] > 0
        assert item["usage"]["total_papers"] >= item["usage"]["papers"]

    def test_units_item_shape(self, dev_env):
        units = dev_env.get("/dictionary", collection="solar").json()["units"]
        mv = next(u for u in units if u["unit"] == "mV")
        assert mv["canon"] == "V" and mv["factor"] == 0.001 and mv["is_canonical"] is False

    def test_materials_item_shape(self, dev_env):
        mats = dev_env.get("/dictionary", collection="solar").json()["materials"]
        assert mats and {"entry_id", "kind", "canon", "name", "synonyms", "papers_count"} <= set(mats[0])

    def test_conditions_item_shape(self, dev_env):
        conds = dev_env.get("/dictionary", collection="solar").json()["conditions"]
        assert conds and {"entry_id", "kind", "canon", "synonyms", "papers_count"} <= set(conds[0])

    def test_collection_optional_defaults_solar(self, dev_env):
        assert dev_env.get("/dictionary").status_code == 200

    def test_rag_collection_400(self, dev_env):
        assert dev_env.get("/dictionary", collection="rag").status_code == 400

    def test_requires_auth(self, dev_env):
        assert dev_env.anon.get(url("/dictionary")).status_code == 401

    def test_non_admin_can_read(self, dev_env):
        reader = dev_env.make_user("dict-reader@example.com")
        assert reader.get(url("/dictionary")).status_code == 200


# --- POST /admin/reprocess ------------------------------------------------------------------------

class TestAdminReprocess:
    def test_202_run_full_keep_review(self, small_env):
        r = small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id})
        assert r.status_code == 202
        run = r.json()
        assert run["kind"] == "full" and run["keep_review"] is True
        assert run["corpus_id"] == small_env.corpus_id and run["run_id"].startswith("run_")

    def test_run_is_executed_by_inline_executor(self, small_env):
        run = small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id}).json()
        done = small_env.get(f"/runs/{run['run_id']}").json()
        assert done["status"] in ("done", "done_with_errors") and done["counts"]["done"] == 3

    def test_review_preserved(self, small_env):
        pid = small_env.pid("D001")
        fid = all_facts(small_env.client, pid)[0]["fact_id"]
        with small_env.uow() as uow:
            uow.facts.update_review(fid, review_status="accepted")
        small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id})
        facts = all_facts(small_env.client, pid)
        assert len(facts) == 47 and sum(f["review_status"] == "accepted" for f in facts) == 1

    def test_model_version_recorded(self, small_env):
        run = small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id,
                                                                    "model_version": "latest"}).json()
        assert run["model_version"]

    def test_without_body_uses_default_corpus_or_400(self, small_env):
        r = small_env.client.post(url("/admin/reprocess"), json={})
        assert r.status_code in (202, 400)

    def test_unknown_corpus_404(self, small_env):
        assert small_env.client.post(url("/admin/reprocess"), json={"corpus_id": "crp_nope"}).status_code == 404

    def test_active_run_conflict_409(self, small_env):
        with small_env.uow() as uow:
            uow.runs.create(corpus_id=small_env.corpus_id, kind="full", status="running")
        r = small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id})
        assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"

    def test_non_admin_403(self, small_env):
        reader = small_env.make_user()
        r = reader.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id})
        assert r.status_code == 403

    def test_anon_401(self, small_env):
        assert small_env.anon.post(url("/admin/reprocess"), json={}).status_code == 401

    def test_not_a_stub(self, small_env):
        r = small_env.client.post(url("/admin/reprocess"), json={"corpus_id": small_env.corpus_id})
        assert r.headers.get("x-faktura-stub") is None
