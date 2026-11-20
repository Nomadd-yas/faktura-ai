"""Представления статьи: api/papers/views.py (card, layout, anchors, coverage, structure, processing, trace,
source) - напрямую и через HTTP (api_contract §3.6, §3.8, §4.7; spec/04 §4.6-4.8)."""
from __future__ import annotations

import pytest

from faktura.api.papers import views

from .conftest import all_facts, url


# --- card -----------------------------------------------------------------------------------------

class TestCardView:
    def test_matches_http(self, dev_env, paper_id):
        assert views.card(dev_env.services, paper_id) == dev_env.get(f"/papers/{paper_id}").json()

    def test_unknown_raises_not_found(self, dev_env):
        from faktura.domain.errors import AppError

        with pytest.raises(AppError) as e:
            views.card(dev_env.services, "pap_nope")
        assert e.value.code == "not_found"

# --- layout ---------------------------------------------------------------------------------------

class TestLayout:
    def test_pages_only_in_wave1(self, dev_env, paper_id):
        out = views.layout(dev_env.services, paper_id)
        assert out["blocks"] == [] and out["tables"] == [] and out["figures"] == []
        n = dev_env.get(f"/papers/{paper_id}").json()["page_count"]
        assert len(out["pages"]) == n

    def test_page_item_shape(self, dev_env, paper_id):
        page = views.layout(dev_env.services, paper_id)["pages"][0]
        assert set(page) >= {"n", "width_pt", "height_pt", "image_url", "has_text_layer"}
        assert page["n"] == 1 and page["width_pt"] > 0 and page["height_pt"] > 0
        assert page["has_text_layer"] is True
        assert page["image_url"] == f"/api/v1/papers/{paper_id}/pages/1"

    def test_image_url_is_fetchable(self, dev_env, paper_id):
        page = dev_env.get(f"/papers/{paper_id}/layout").json()["pages"][0]
        assert dev_env.client.get(page["image_url"]).status_code == 200

    def test_pages_param_single(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/layout", pages="4").json()
        assert [p["n"] for p in body["pages"]] == [4]

    def test_pages_param_range(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/layout", pages="3-5").json()
        assert [p["n"] for p in body["pages"]] == [3, 4, 5]

    def test_pages_range_clipped_to_page_count(self, dev_env, paper_id):
        n = dev_env.get(f"/papers/{paper_id}").json()["page_count"]
        body = dev_env.get(f"/papers/{paper_id}/layout", pages=f"{n - 1}-{n + 5}").json()
        assert [p["n"] for p in body["pages"]] == [n - 1, n]

    @pytest.mark.parametrize("bad", ["x", "5-3", "0", "1-", "-2"])
    def test_bad_pages_400(self, dev_env, paper_id, bad):
        r = dev_env.get(f"/papers/{paper_id}/layout", pages=bad)
        assert r.status_code == 400 and r.json()["error"]["code"] == "validation_error"

    def test_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/layout").status_code == 404


# --- anchors --------------------------------------------------------------------------------------

class TestAnchors:
    def test_shape(self, dev_env, paper_id):
        data = dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]
        assert data
        for a in data:
            assert set(a) >= {"anchor_id", "kind", "page", "rects", "text", "fact_ids", "issue_ids"}
            assert a["anchor_id"].startswith("anc_")
            assert a["kind"] in ("cell", "text", "figure", "manual", "caption", "reference")

    def test_rects_are_fractions_of_page(self, dev_env, paper_id):
        data = dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]
        with_rects = [a for a in data if a["rects"]]
        assert with_rects, "у якорей с найденной цитатой есть rects"
        for a in with_rects:
            for r in a["rects"]:
                assert set(r) == {"x", "y", "w", "h"}
                assert 0 <= r["x"] <= 1 and 0 <= r["y"] <= 1
                assert 0 < r["w"] <= 1 and 0 < r["h"] <= 1
                assert r["x"] + r["w"] <= 1.001 and r["y"] + r["h"] <= 1.001

    def test_page_filter(self, dev_env, paper_id):
        all_ = dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]
        page = all_[0]["page"]
        only = dev_env.get(f"/papers/{paper_id}/anchors", page=page).json()["data"]
        assert only and all(a["page"] == page for a in only)
        assert len(only) == len([a for a in all_ if a["page"] == page])

    def test_page_without_anchors_is_empty(self, dev_env, paper_id):
        assert dev_env.get(f"/papers/{paper_id}/anchors", page=999).json() == {"data": []}

    def test_anchor_ids_unique(self, dev_env, paper_id):
        ids = [a["anchor_id"] for a in dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]]
        assert len(ids) == len(set(ids))

    def test_fact_ids_link_back(self, dev_env, paper_id):
        facts = {f["fact_id"]: f for f in all_facts(dev_env.client, paper_id)}
        for a in dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]:
            for fid in a["fact_ids"]:
                assert fid in facts and facts[fid]["anchor_id"] == a["anchor_id"]

    def test_every_fact_anchor_exists(self, dev_env, paper_id):
        anchors = {a["anchor_id"] for a in dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]}
        for f in all_facts(dev_env.client, paper_id):
            if f["anchor_id"]:
                assert f["anchor_id"] in anchors

    def test_view_equals_http(self, dev_env, paper_id):
        assert views.anchors(dev_env.services, paper_id) == dev_env.get(f"/papers/{paper_id}/anchors").json()

    def test_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/anchors").status_code == 404

    def test_bad_page_param(self, dev_env, paper_id):
        assert dev_env.get(f"/papers/{paper_id}/anchors", page="abc").status_code in (400, 422)


# --- coverage -------------------------------------------------------------------------------------

class TestCoverage:
    def test_shape(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/coverage").json()
        assert set(body) >= {"params", "total", "filled", "conflicts", "figure_only", "diverge", "cells"}
        assert body["params"] and 0 <= body["filled"] <= body["total"]

    def test_cell_shape(self, dev_env, paper_id):
        cells = dev_env.get(f"/papers/{paper_id}/coverage").json()["cells"]
        assert cells
        for c in cells:
            assert set(c) >= {"row_id", "param", "fact_ids", "main_fact_id", "level", "diverge", "figure_only"}
            assert c["level"] in (None, "conflict", "doubt", "warn")
            if c["fact_ids"]:
                assert c["main_fact_id"] in c["fact_ids"]

    def test_params_are_profile_fields(self, dev_env, paper_id, profile):
        body = dev_env.get(f"/papers/{paper_id}/coverage").json()
        metrics = {m.lower() for m in profile.metric_names()}
        results_params = [p for p in body["params"] if p.lower() in metrics]
        assert results_params, "параметры покрытия берутся из полей профиля"

    def test_equals_checks_paper_coverage(self, dev_env, paper_id, profile):
        """Единственный владелец расчёта - checks.paper_coverage (план 5.9)."""
        from faktura import checks

        with dev_env.uow() as uow:
            bundle = uow.papers.load_bundle(paper_id)
        cov = checks.paper_coverage(bundle, profile)
        body = dev_env.get(f"/papers/{paper_id}/coverage").json()
        assert (body["filled"], body["total"]) == (cov.filled, cov.total)

    def test_card_coverage_summary_matches(self, dev_env, paper_id):
        cov = dev_env.get(f"/papers/{paper_id}/coverage").json()
        summary = dev_env.get(f"/papers/{paper_id}").json()["coverage_summary"]
        assert summary == {"filled": cov["filled"], "total": cov["total"]}

    def test_matches_corpus_summary_coverage(self, dev_env, paper_id):
        """Приёмка 5.9: /papers/{id}/coverage и summary.coverage[] корпуса дают одинаковые числа."""
        summary = dev_env.get(f"/corpora/{dev_env.corpus_id}/summary").json()
        cov = dev_env.get(f"/papers/{paper_id}/coverage").json()
        per_paper = {c.get("paper_id"): c for c in summary["coverage"] if "paper_id" in c}
        if per_paper:
            assert per_paper[paper_id]["filled"] == cov["filled"]
            assert per_paper[paper_id]["total"] == cov["total"]
        else:
            # агрегат по полям профиля: сумма по статье не превосходит корпусной
            assert sum(c.get("filled", 0) for c in summary["coverage"]) >= cov["filled"]

    def test_rejected_facts_not_counted(self, small_env):
        pid = small_env.pid("D001")
        before = small_env.get(f"/papers/{pid}/coverage").json()
        facts = all_facts(small_env.client, pid)
        cell = next(c for c in before["cells"] if len(c["fact_ids"]) == 1 and c["main_fact_id"])
        with small_env.uow() as uow:
            uow.facts.update_review(cell["main_fact_id"], review_status="rejected")
        after = small_env.get(f"/papers/{pid}/coverage").json()
        assert after["filled"] == before["filled"] - 1
        assert len(facts) > 0

    def test_main_fact_prefers_accepted(self, small_env):
        pid = small_env.pid("D001")
        body = small_env.get(f"/papers/{pid}/coverage").json()
        multi = [c for c in body["cells"] if len(c["fact_ids"]) > 1]
        if not multi:
            pytest.skip("в D001 нет клеток с несколькими фактами")
        cell = multi[0]
        other = next(f for f in cell["fact_ids"] if f != cell["main_fact_id"])
        with small_env.uow() as uow:
            uow.facts.update_review(other, review_status="accepted")
        again = small_env.get(f"/papers/{pid}/coverage").json()
        new = next(c for c in again["cells"] if c["row_id"] == cell["row_id"] and c["param"] == cell["param"])
        assert new["main_fact_id"] == other

    def test_view_equals_http(self, dev_env, paper_id):
        assert views.coverage(dev_env.services, paper_id) == dev_env.get(f"/papers/{paper_id}/coverage").json()

    def test_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/coverage").status_code == 404


# --- structure ------------------------------------------------------------------------------------

class TestStructure:
    def test_shape_solar(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/structure").json()
        assert body["collection"] == "solar"
        assert body["pipeline"] is None
        assert isinstance(body["variants"], list) and isinstance(body["conditions"], list)

    def test_variants_have_layers_from_gold(self, dev_env, paper_id):
        variants = dev_env.get(f"/papers/{paper_id}/structure").json()["variants"]
        assert variants
        for v in variants:
            assert set(v) >= {"id", "label", "device_ids", "row_ids", "layers"}
            for layer in v["layers"]:
                assert set(layer) >= {"layer_id", "layer_order", "layer_function", "layer_material",
                                      "layer_thickness", "thickness_unit", "source_page", "fact_ids"}
        orders = [layer["layer_order"] for layer in variants[0]["layers"]]
        assert orders == sorted(orders)

    def test_conditions_from_measurements(self, dev_env, paper_id):
        conds = dev_env.get(f"/papers/{paper_id}/structure").json()["conditions"]
        keys = {c["key"] for c in conds}
        assert {"spectrum", "power", "temp", "area", "scan"} >= keys
        for c in conds:
            assert set(c) >= {"key", "label", "value", "fact_id"}

    def test_view_equals_http(self, dev_env, paper_id):
        assert views.structure(dev_env.services, paper_id) == dev_env.get(f"/papers/{paper_id}/structure").json()

    def test_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/structure").status_code == 404


# --- processing -----------------------------------------------------------------------------------

class TestProcessing:
    def test_done_shape(self, dev_env, paper_id):
        body = dev_env.get(f"/papers/{paper_id}/processing").json()
        assert body["status"] == "done" and body["progress"] == 100 and body["error"] is None
        assert body["stages"] == ["Текст", "Таблицы", "Рисунки", "Формулы", "Нормализация", "Проверка"]
        assert body["topic_by"] == "project" and body["collection"] == "solar" and body["dup_of"] is None
        facts = all_facts(dev_env.client, paper_id)
        assert body["summary"]["facts"] == len(facts)
        assert "issues" in body["summary"]

    def test_view_equals_http(self, dev_env, paper_id):
        assert views.processing(dev_env.services, paper_id) == dev_env.get(f"/papers/{paper_id}/processing").json()

    def test_queued_paper(self, small_env):
        """Статья в статусе queued: summary отсутствует, progress 0."""
        with small_env.uow() as uow:
            f = uow.files.add(corpus_id=small_env.corpus_id, name="Dx.pdf", publication_id="Dx")
            p = uow.papers.create(workspace_id=small_env.admin.workspace_id, corpus_id=small_env.corpus_id,
                                  file_id=f.id, publication_id="Dx", status="queued")
        body = small_env.get(f"/papers/{p.id}/processing").json()
        assert body["status"] == "queued" and body["progress"] == 0
        assert "summary" not in body or body["summary"] is None

    def test_404(self, dev_env):
        assert dev_env.get("/papers/pap_nope/processing").status_code == 404


# --- trace ----------------------------------------------------------------------------------------

class TestTrace:
    def _numeric_fact(self, env, pid):
        return next(f for f in all_facts(env.client, pid) if f["normalized_value"] is not None)

    def test_steps_shape(self, dev_env, paper_id):
        fact = self._numeric_fact(dev_env, paper_id)
        steps = dev_env.get(f"/facts/{fact['fact_id']}/trace").json()["steps"]
        assert steps and all(set(s) == {"k", "v"} for s in steps)
        assert steps[0]["k"] == "В статье" and steps[-1]["k"] == "Итог"

    def test_first_step_quotes_raw(self, dev_env, paper_id):
        fact = self._numeric_fact(dev_env, paper_id)
        steps = dev_env.get(f"/facts/{fact['fact_id']}/trace").json()["steps"]
        assert fact["raw_value"] in steps[0]["v"]

    def test_converted_fact_has_conversion_step(self, dev_env):
        """D001 R002: 1197 mV -> 1.197 V."""
        facts = [f for pid in dev_env.papers.values() for f in all_facts(dev_env.client, pid)
                 if f["norm_status"] == "converted"]
        assert facts, "в dev есть переведённые единицы (мВ -> В)"
        steps = dev_env.get(f"/facts/{facts[0]['fact_id']}/trace").json()["steps"]
        assert "Перевод" in [s["k"] for s in steps]

    def test_corrected_fact_last_step(self, small_env):
        pid = small_env.pid("D001")
        fact = self._numeric_fact(small_env, pid)
        with small_env.uow() as uow:
            uow.facts.update_review(fact["fact_id"], review_status="accepted", corrected_raw="1 V",
                                    corrected_value=1)
        steps = small_env.get(f"/facts/{fact['fact_id']}/trace").json()["steps"]
        assert steps[-1]["k"] == "Исправлено"

    def test_view_equals_http(self, dev_env, paper_id):
        fact = self._numeric_fact(dev_env, paper_id)
        assert views.trace(dev_env.services, fact["fact_id"]) == dev_env.get(f"/facts/{fact['fact_id']}/trace").json()

    def test_404(self, dev_env):
        assert dev_env.get("/facts/fct_nope/trace").status_code == 404


# --- source ---------------------------------------------------------------------------------------

class TestSource:
    def test_shape(self, dev_env, paper_id):
        fact = all_facts(dev_env.client, paper_id)[0]
        body = dev_env.get(f"/facts/{fact['fact_id']}/source").json()
        assert set(body) >= {"fact_id", "paper_id", "paper", "page", "kind", "ref", "where", "section",
                             "anchor_id", "rects", "quote"}
        assert body["fact_id"] == fact["fact_id"] and body["paper_id"] == paper_id
        assert body["page"] == fact["source_page"] and body["quote"] == fact["source_fragment"]
        assert body["paper"].startswith("Improved Photovoltaic")

    def test_rects_match_anchor(self, dev_env, paper_id):
        anchors = {a["anchor_id"]: a for a in dev_env.get(f"/papers/{paper_id}/anchors").json()["data"]}
        for fact in all_facts(dev_env.client, paper_id)[:10]:
            body = dev_env.get(f"/facts/{fact['fact_id']}/source").json()
            if body["anchor_id"]:
                assert body["rects"] == anchors[body["anchor_id"]]["rects"]

    def test_view_equals_http(self, dev_env, paper_id):
        fid = all_facts(dev_env.client, paper_id)[0]["fact_id"]
        assert views.source(dev_env.services, fid) == dev_env.get(f"/facts/{fid}/source").json()

    def test_404(self, dev_env):
        assert dev_env.get("/facts/fct_nope/source").status_code == 404

    def test_requires_auth(self, dev_env, paper_id):
        fid = all_facts(dev_env.client, paper_id)[0]["fact_id"]
        assert dev_env.anon.get(url(f"/facts/{fid}/source")).status_code == 401
