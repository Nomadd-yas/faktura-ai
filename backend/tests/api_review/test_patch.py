"""ReviewService.patch_value и PATCH /facts/{id} (spec/04 §4.8, REQ-108, REQ-058).

Критерии: {raw: '1100 мВ'} для voc -> corrected_value=1.1, extraction_status не меняется;
'1,1 кВ' -> 422 fact_value_unrecognized, field=raw; правка синхронно открывает/закрывает jv_consistency.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.errors import AppError

from review_support import err, url

pytestmark = pytest.mark.data


class TestServicePatch:
    def test_voc_millivolts(self, env, svc, user, new_fact):
        res = svc.patch_value(user, new_fact.id, "1100 мВ")
        f = res.fact
        assert f.corrected_value == Decimal("1.1")
        assert f.corrected_raw == "1100 мВ"
        assert str(f.review_status) == "accepted"
        assert f.extraction_status == new_fact.extraction_status  # статус извлечения не трогаем
        assert res.action.action == "edit" and res.action.fact_id == new_fact.id

    def test_original_values_preserved(self, env, svc, user, new_fact):
        res = svc.patch_value(user, new_fact.id, "1100 мВ")
        assert res.fact.raw_value == new_fact.raw_value
        assert res.fact.normalized_value == new_fact.normalized_value

    @pytest.mark.parametrize("raw, expected", [
        ("1,08 В", Decimal("1.08")),
        ("1.08 V", Decimal("1.08")),
        ("1080 мВ", Decimal("1.08")),
        ("1,1", Decimal("1.1")),          # число без единицы - в канонической (В)
        ("  1100  мВ ", Decimal("1.1")),  # пробелы по краям
    ])
    def test_voc_variants(self, env, svc, user, new_fact, raw, expected):
        assert svc.patch_value(user, new_fact.id, raw).fact.corrected_value == expected

    def test_pce_percent_and_bare_number(self, env, svc, user):
        pce = env.find_fact("pce")
        assert svc.patch_value(user, pce.id, "19,0 %").fact.corrected_value == Decimal("19.0")
        assert svc.patch_value(user, pce.id, "19").fact.corrected_value == Decimal("19")

    @pytest.mark.parametrize("raw", ["1,1 кВ", "abc", "", "   ", "В"])
    def test_unrecognized_422_field_raw(self, env, svc, user, new_fact, raw):
        with pytest.raises(AppError) as e:
            svc.patch_value(user, new_fact.id, raw)
        assert e.value.code == "fact_value_unrecognized"
        assert e.value.http_status == 422 and e.value.field == "raw"

    def test_unrecognized_message_mentions_unit(self, svc, user, new_fact):
        with pytest.raises(AppError) as e:
            svc.patch_value(user, new_fact.id, "1,1 кВ")
        assert "кВ" in e.value.message

    def test_unrecognized_changes_nothing(self, env, svc, user, new_fact):
        n = len(env.history())
        with pytest.raises(AppError):
            svc.patch_value(user, new_fact.id, "1,1 кВ")
        f = env.fact(new_fact.id)
        assert f.corrected_value is None and str(f.review_status) == "new" and len(env.history()) == n

    def test_unknown_fact_404(self, svc, user):
        with pytest.raises(AppError) as e:
            svc.patch_value(user, "fct_nope", "1 В")
        assert e.value.code == "not_found"

    def test_patch_rejected_fact_becomes_accepted(self, env, svc, user, new_fact):
        svc.reject(user, new_fact.id)
        assert str(svc.patch_value(user, new_fact.id, "1 В").fact.review_status) == "accepted"

    def test_twice_keeps_last_value(self, env, svc, user, new_fact):
        svc.patch_value(user, new_fact.id, "1 В")
        assert svc.patch_value(user, new_fact.id, "1,2 В").fact.corrected_value == Decimal("1.2")

    def test_text_param_saved_as_is(self, env, svc, user):
        """Для текстовых параметров raw сохраняется как есть (+ словарь)."""
        text_fact = next((f for f in env.facts() if f.normalized_value is None
                          and f.param not in ("voc", "jsc", "ff", "pce")), None)
        if text_fact is None:
            pytest.skip("в D001 нет текстовых фактов")
        assert svc.patch_value(user, text_fact.id, "1 sun").fact.corrected_raw == "1 sun"

    def test_journal_entry_has_from_to(self, env, svc, user, new_fact):
        svc.patch_value(user, new_fact.id, "1100 мВ")
        e = next(h for h in env.history() if h["action"] == "edit")
        assert e["fact_id"] == new_fact.id and e["to"] and e["from"] is not None

    def test_sync_open_and_close_jv_consistency(self, env, svc, user):
        """Правка синхронно открывает, а обратная правка закрывает jv_consistency; issues_changed это отражает."""
        row = env.jv_row()
        pce = env.find_fact("pce", row_id=row)
        assert not any(row in i for i in env.open_issue_ids("jv_consistency"))
        res = svc.patch_value(user, pce.id, "5 %")
        assert any("jv_consistency" in i for i in res.issues_changed.opened)
        assert env.open_issue_ids("jv_consistency")
        back = svc.patch_value(user, pce.id, f"{pce.normalized_value} %")
        assert any("jv_consistency" in i for i in back.issues_changed.closed)
        assert not any(row in i for i in env.open_issue_ids("jv_consistency"))

    def test_paper_summary_reflects_new_issue(self, env, svc, user):
        pce = env.find_fact("pce", row_id=env.jv_row())
        n0 = env.paper().stats.open_issues
        res = svc.patch_value(user, pce.id, "5 %")
        assert res.paper.stats.open_issues > n0 and res.paper.stats.top_level is not None


class TestHttpPatch:
    def test_voc_mv_200(self, env, client, new_fact):
        r = client.patch(url(f"/facts/{new_fact.id}"), json={"raw": "1100 мВ"})
        assert r.status_code == 200, r.text
        f = r.json()["fact"]
        assert f["corrected_value"] == pytest.approx(1.1) and f["value"] == pytest.approx(1.1)
        assert f["corrected_raw"] == "1100 мВ" and f["review_status"] == "accepted"
        assert f["extraction_status"] == new_fact.extraction_status  # не меняется
        assert r.headers["X-Action-Id"] == r.json()["action_id"]
        assert "issues_changed" in r.json() and "open_issues" in r.json()["paper"]

    def test_kilovolts_422(self, client, new_fact):
        r = client.patch(url(f"/facts/{new_fact.id}"), json={"raw": "1,1 кВ"})
        assert r.status_code == 422
        e = err(r)
        assert e["code"] == "fact_value_unrecognized" and e["field"] == "raw"
        assert "кВ" in e["message"]

    def test_missing_raw_400(self, client, new_fact):
        r = client.patch(url(f"/facts/{new_fact.id}"), json={})
        assert r.status_code == 400 and err(r)["code"] == "validation_error"

    def test_unknown_fact_404(self, client):
        assert client.patch(url("/facts/fct_nope"), json={"raw": "1 В"}).status_code == 404

    def test_jv_consistency_opens_in_response(self, env, client):
        pce = env.find_fact("pce", row_id=env.jv_row())
        r = client.patch(url(f"/facts/{pce.id}"), json={"raw": "5 %"})
        assert any("jv_consistency" in i for i in r.json()["issues_changed"]["opened"])

    def test_undo_returns_value(self, env, client, new_fact):
        r = client.patch(url(f"/facts/{new_fact.id}"), json={"raw": "1100 мВ"})
        assert client.post(url(f"/actions/{r.json()['action_id']}/undo")).status_code == 200
        f = env.fact(new_fact.id)
        assert f.corrected_value is None and str(f.review_status) == "new"
