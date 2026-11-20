"""ReviewService.add_manual / guess и POST /papers/{id}/facts, POST /papers/{id}/facts/guess (spec/04 §4.9, REQ-109).

Критерии: с цитатой -> exportable=true и pipeline.build_output даёт строку RM1 при 0 ошибок валидатора;
без цитаты -> exportable=false.
"""
from __future__ import annotations

import csv
from decimal import Decimal

import pytest

from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data

QUOTE = "напряжение холостого хода составило 1100 мВ"


def body(env, **over):
    row = env.jv_row()
    b = {"row_id": row, "param": "voc", "raw": "1100 мВ", "quote": QUOTE, "kind": "manual",
         "selection": {"page": 4, "rects": [{"x": 0.21, "y": 0.33, "w": 0.18, "h": 0.016}],
                       "block_id": "blk_43", "char_range": [120, 163]}}
    b.update(over)
    return {k: v for k, v in b.items() if v is not None}


class TestAddManualService:
    def test_manual_defaults(self, env, svc, user):
        res = svc.add_manual(user, env.pid(), body(env))
        f = res.fact
        assert f.added_by == "manual" and str(f.review_status) == "accepted"
        assert f.confidence == 1 and f.param == "voc"
        assert f.normalized_value == Decimal("1.1")
        assert f.source_page == 4 and f.source_fragment == QUOTE
        assert str(f.source_kind) == "manual"
        assert f.exportable is True
        assert res.action.action == "add" and res.action.fact_id == f.id

    def test_fact_persisted_and_listed(self, env, svc, user):
        f = svc.add_manual(user, env.pid(), body(env)).fact
        assert any(x.id == f.id for x in env.facts())
        with env.uow() as uow:
            assert any(x.id == f.id for x in uow.facts.manual_for_paper(env.pid()))

    def test_bare_number_in_canonical_unit(self, env, svc, user):
        f = svc.add_manual(user, env.pid(), body(env, raw="1,1")).fact
        assert f.normalized_value == Decimal("1.1")

    def test_figure_kind_new_and_confidence_07(self, env, svc, user):
        f = svc.add_manual(user, env.pid(), body(env, kind="figure", ref="Рис. 3",
                                                  where="пересечение с осью V")).fact
        assert str(f.review_status) == "new" and f.confidence == pytest.approx(0.7)
        assert str(f.source_kind) == "figure" and f.added_by == "manual"

    def test_without_quote_not_exportable(self, env, svc, user):
        f = svc.add_manual(user, env.pid(), body(env, quote=None)).fact
        assert f.exportable is False

    def test_without_page_not_exportable(self, env, svc, user):
        f = svc.add_manual(user, env.pid(), body(env, selection=None)).fact
        assert f.exportable is False

    def test_empty_quote_not_exportable(self, env, svc, user):
        assert svc.add_manual(user, env.pid(), body(env, quote="   ")).fact.exportable is False

    def test_ref_instead_of_quote_is_enough(self, env, svc, user):
        """У-35: источник - page + (quote или ref)."""
        f = svc.add_manual(user, env.pid(), body(env, quote=None, kind="figure", ref="Рис. 3")).fact
        assert f.exportable is True

    def test_unrecognized_value_422(self, env, svc, user):
        with pytest.raises(AppError) as e:
            svc.add_manual(user, env.pid(), body(env, raw="1,1 кВ"))
        assert e.value.code == "fact_value_unrecognized" and e.value.http_status == 422 and e.value.field == "raw"

    def test_unknown_param_400(self, env, svc, user):
        with pytest.raises(AppError) as e:
            svc.add_manual(user, env.pid(), body(env, param="no_such_param"))
        assert e.value.http_status == 400 and e.value.field == "param"

    def test_unknown_row_400(self, env, svc, user):
        with pytest.raises(AppError) as e:
            svc.add_manual(user, env.pid(), body(env, row_id="ZZZ"))
        assert e.value.http_status == 400 and e.value.field == "row_id"

    def test_star_row_for_conditions_allowed(self, env, svc, user):
        """Для условий (общие факты) row_id='*'."""
        res = svc.add_manual(user, env.pid(), body(env, row_id="*", param="light_intensity", raw="100 мВт/см²"))
        assert res.fact.row_id in ("*", None)

    def test_unknown_paper_404(self, env, svc, user):
        with pytest.raises(AppError) as e:
            svc.add_manual(user, "pap_nope", body(env))
        assert e.value.code == "not_found"

    def test_paper_not_ready_422(self, env, svc, user):
        from faktura.domain.entities import PaperStatus

        with env.uow() as uow:
            uow.papers.update(env.pid(), status=PaperStatus.processing)
        with pytest.raises(AppError) as e:
            svc.add_manual(user, env.pid(), body(env))
        assert e.value.code == "paper_not_ready"

    def test_recheck_after_add_paper_summary(self, env, svc, user):
        res = svc.add_manual(user, env.pid(), body(env))
        assert res.paper is not None and res.paper.stats.facts_count == len(env.facts())

    def test_add_value_conflicting_opens_issue(self, env, svc, user):
        """Новый ручной pce, не сходящийся с Voc·Jsc·FF, открывает замечание синхронно."""
        row = env.jv_row()
        res = svc.add_manual(user, env.pid(), body(env, row_id=row, param="pce", raw="1 %"))
        assert res.issues_changed.opened or res.issues_changed.changed

    def test_undo_removes_manual_fact(self, env, svc, user):
        res = svc.add_manual(user, env.pid(), body(env))
        svc.undo(user, res.action.id)
        assert all(x.id != res.fact.id for x in env.facts())


class TestManualInSubmission:
    def test_exportable_manual_fact_goes_to_results_rm1(self, env, svc, user, check_script, tmp_path):
        from faktura.domain.profile import builtin_solar_profile
        from faktura.pipeline import build_output

        svc.add_manual(user, env.pid(), body(env))
        out = tmp_path / "out"
        res = build_output(env.services, env.corpus_id, out, profile=builtin_solar_profile(),
                           check_script=check_script, include_meta=False)
        assert res.check is not None and res.check.errors == [] and not res.problems
        rows = list(csv.DictReader(open(out / "results.csv", encoding="utf-8-sig", newline="")))
        rm = [r for r in rows if r["result_id"] == "RM1"]
        assert len(rm) == 1
        assert rm[0]["source_page"] == "4" and rm[0]["source_fragment"] == QUOTE
        assert rm[0]["metric_name"] == "Voc" and float(rm[0]["metric_value"]) == pytest.approx(1.1)

    def test_not_exportable_manual_fact_absent_from_csv(self, env, svc, user, check_script, tmp_path):
        from faktura.domain.profile import builtin_solar_profile
        from faktura.pipeline import build_output

        svc.add_manual(user, env.pid(), body(env, quote=None))
        out = tmp_path / "out"
        res = build_output(env.services, env.corpus_id, out, profile=builtin_solar_profile(),
                           check_script=check_script, include_meta=False)
        assert res.check is not None and res.check.errors == []
        rows = list(csv.DictReader(open(out / "results.csv", encoding="utf-8-sig", newline="")))
        assert not [r for r in rows if r["result_id"].startswith("RM")]


class TestHttpAddManual:
    def test_201_with_quote(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env))
        assert r.status_code == 201, r.text
        j = r.json()
        assert j["fact"]["exportable"] is True and j["fact"]["review_status"] == "accepted"
        assert j["fact"]["added_by"] == "manual" and j["fact"]["confidence"] == 1
        assert j["action_id"] == r.headers["X-Action-Id"]
        assert {"opened", "closed"} <= set(j["issues_changed"]) and "open_issues" in j["paper"]

    def test_no_quote_not_exportable(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env, quote=None))
        assert r.status_code == 201 and r.json()["fact"]["exportable"] is False

    def test_figure_kind(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"),
                        json=body(env, kind="figure", ref="Рис. 3", where="пересечение с осью V"))
        assert r.status_code == 201
        assert r.json()["fact"]["review_status"] == "new" and r.json()["fact"]["confidence"] == pytest.approx(0.7)

    def test_422_unrecognized(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env, raw="1,1 кВ"))
        assert r.status_code == 422 and err(r)["code"] == "fact_value_unrecognized" and err(r)["field"] == "raw"

    @pytest.mark.parametrize("over, field", [({"param": "zzz"}, "param"), ({"row_id": "ZZZ"}, "row_id")])
    def test_400_unknown(self, env, client, over, field):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env, **over))
        assert r.status_code == 400 and err(r)["field"] == field

    def test_400_missing_required(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json={"param": "voc"})
        assert r.status_code == 400

    def test_404_paper(self, env, client):
        assert client.post(url("/papers/pap_nope/facts"), json=body(env)).status_code == 404

    def test_new_fact_visible_in_paper_facts(self, env, client):
        fid = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env)).json()["fact"]["fact_id"]
        facts = client.get(url(f"/papers/{env.pid()}/facts")).json()["data"]
        assert fid in [f["fact_id"] for f in facts]

    def test_undo_deletes_manual_fact(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts"), json=body(env))
        fid, aid = r.json()["fact"]["fact_id"], r.json()["action_id"]
        assert client.post(url(f"/actions/{aid}/undo")).status_code == 200
        assert fid not in [f["fact_id"] for f in client.get(url(f"/papers/{env.pid()}/facts")).json()["data"]]


class TestGuess:
    def sel(self, env, **over):
        b = {"text": "напряжение 1100 мВ при", "page": 4, "cell_anchor_id": None, "block_id": "blk_41",
             "before": "Для образца", "after": "освещении", "row_label_text": None}
        b.update(over)
        return b

    def test_voc_by_synonym_left(self, env, svc):
        g = svc.guess(env.pid(), self.sel(env))
        assert g["param"] == "voc" and g["raw"] == "1100 мВ"
        assert g["preview"]["ok"] is True and "1,10 В" in g["preview"]["text"]
        assert "row_id" in g and "example" in g

    def test_param_by_unit_when_no_synonym(self, env, svc):
        assert svc.guess(env.pid(), self.sel(env, text="значение 1100 мВ", before="", after=""))["param"] == "voc"

    def test_bandgap_by_ev(self, env, svc):
        g = svc.guess(env.pid(), self.sel(env, text="ширина запрещённой зоны 1,55 эВ"))
        assert g["param"] in ("bandgap_value", "bandgap")

    def test_default_main_param_when_nothing_found(self, env, svc):
        assert svc.guess(env.pid(), self.sel(env, text="12,5", before="", after=""))["param"] == "pce"

    def test_row_from_row_label_text(self, env, svc):
        row = env.jv_row()
        with env.uow() as uow:
            label = next(f.row_label for f in env.facts() if f.row_id == row)
        g = svc.guess(env.pid(), self.sel(env, row_label_text=label))
        assert g["row_id"] == row

    def test_row_default_first_row(self, env, svc):
        assert svc.guess(env.pid(), self.sel(env))["row_id"]

    def test_unrecognizable_preview_not_ok(self, env, svc):
        g = svc.guess(env.pid(), self.sel(env, text="напряжение 1,1 кВ"))
        assert g["preview"]["ok"] is False

    def test_unknown_paper_404(self, svc):
        with pytest.raises(AppError) as e:
            svc.guess("pap_nope", {"text": "x"})
        assert e.value.code == "not_found"

    def test_no_text_400(self, env, svc):
        with pytest.raises(AppError) as e:
            svc.guess(env.pid(), {"page": 4})
        assert e.value.http_status == 400

    def test_guess_does_not_mutate(self, env, svc):
        n, h = len(env.facts()), len(env.history())
        svc.guess(env.pid(), self.sel(env))
        assert len(env.facts()) == n and len(env.history()) == h

    def test_http_guess(self, env, client):
        r = client.post(url(f"/papers/{env.pid()}/facts/guess"), json=self.sel(env))
        assert r.status_code == 200
        assert {"row_id", "param", "raw", "preview", "example"} <= set(r.json())
        assert "X-Action-Id" not in r.headers

    def test_http_guess_404(self, env, client):
        assert client.post(url("/papers/pap_nope/facts/guess"), json=self.sel(env)).status_code == 404
