"""Единые сериализаторы сущностей в форму api_contract §3. Фикстуры - из mini_gold (реальные строки dev_gold)."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from faktura.api.base import serializers as S
from faktura.domain.entities import (Action, Anchor, Corpus, CorpusFile, CorpusFileStatus, Coverage, Export, Fact,
                                     Issue, Paper, PaperStats, PaperStatus, Quality, Run, RunCounts, RunKind,
                                     RunOutput, RunStatus, User)
from faktura.domain.records import AddedBy, IssueRecord, IssueStatus, NormStatus, Rect, ReviewStatus

ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")
T = datetime(2026, 11, 20, 9, 15, 0, tzinfo=timezone.utc)


def _gold_rows(mini_gold_dir, table: str) -> list[dict]:
    with open(mini_gold_dir / f"{table}.csv", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _json_safe(obj) -> None:
    json.dumps(obj, ensure_ascii=False, allow_nan=False)  # без default=: всё уже примитивы


@pytest.fixture
def normalizer(h):
    return h.FakeNormalizer()


@pytest.fixture
def gold_result(mini_gold_dir):
    """D001 / R002: Voc, raw 1197 mV -> normalized 1.197 V (реальная строка эталона)."""
    row = next(r for r in _gold_rows(mini_gold_dir, "results") if r["publication_id"] == "D001" and r["result_id"] == "R002")
    return row


@pytest.fixture
def fact(gold_result) -> Fact:
    return Fact(
        id="fct_4kq9w2m1z8ab", paper_id="pap_7d1x0c2b9qre", publication_id=gold_result["publication_id"],
        target={"entity": "results", "id": gold_result["result_id"], "field": "metric_value"},
        param="voc", metric_name=gold_result["metric_name"], row_id="DV1", row_label="pristine",
        raw_value=gold_result["raw_value"], raw_unit=gold_result["raw_unit"],
        normalized_value=Decimal(gold_result["normalized_value"]), normalized_unit=gold_result["normalized_unit"],
        norm_status="converted", extraction_status=gold_result["extraction_status"], confidence=0.9,
        source_page=int(gold_result["source_page"]), source_fragment=gold_result["source_fragment"],
        source_table=gold_result["source_table"], source_figure=gold_result["source_figure"], source_kind="table",
        quote_found=True, anchor_id="anc_p5_0001", model_version="mock-gold-abc12345",
    )


# --- user_out ----------------------------------------------------------------------------------

class TestUserOut:
    def test_shape(self):
        out = S.user_out(User(id="usr_01", workspace_id="ws", email="team@faktura.ai", name="Исследователь",
                              is_admin=False, password_hash="argon2-secret"))
        assert {"id", "user_id", "email", "name", "role", "team", "color", "focus", "is_admin"} <= set(out)
        assert out["id"] == out["user_id"] == "usr_01"
        assert out["email"] == "team@faktura.ai" and out["name"] == "Исследователь" and out["is_admin"] is False
        _json_safe(out)

    def test_never_leaks_secrets_or_workspace(self):
        out = S.user_out(User(id="u", workspace_id="ws_secret", email="a@b.c", password_hash="argon2-secret"))
        text = json.dumps(out)
        assert "argon2-secret" not in text and "password" not in out

    def test_admin_flag(self):
        assert S.user_out(User(id="u", workspace_id="w", email="a@b.c", is_admin=True))["is_admin"] is True

    def test_name_none_is_null_or_string(self):
        out = S.user_out(User(id="u", workspace_id="w", email="a@b.c", name=None))
        assert out["name"] is None or isinstance(out["name"], str)


# --- corpus_out / corpus_file_out / run_out ----------------------------------------------------

class TestCorpusOut:
    def test_shape(self):
        c = Corpus(id="crp_solar", workspace_id="ws", name="Солнечные", collection="solar", kind="regular",
                   profile_id="prf_solar_tz_v1", created_at=T)
        out = S.corpus_out(c)
        assert out["corpus_id"] == "crp_solar" and out["name"] == "Солнечные"
        assert out["kind"] == "regular" and out["collection"] == "solar" and out["profile_id"] == "prf_solar_tz_v1"
        for k in ("files_total", "papers_done", "papers_failed", "facts", "open_issues"):
            assert isinstance(out[k], int), k
        for k in ("project_id", "last_run_id", "active_run", "created_at", "updated_at", "deleted_at"):
            assert k in out, k
        assert out["deleted_at"] is None
        assert ISO_Z.match(out["created_at"])
        _json_safe(out)

    @pytest.mark.parametrize("kind", ["regular", "manual", "extract"])
    def test_kinds(self, kind):
        assert S.corpus_out(Corpus(id="c", workspace_id="w", name="n", kind=kind))["kind"] == kind

    def test_deleted_at_serialized(self):
        out = S.corpus_out(Corpus(id="c", workspace_id="w", name="n", deleted_at=T))
        assert ISO_Z.match(out["deleted_at"])

    def test_no_internal_workspace_leak(self):
        assert "workspace_id" not in S.corpus_out(Corpus(id="c", workspace_id="w", name="n"))


class TestDatetimeFormat:
    def test_aware_utc_ends_with_z(self):
        assert S.corpus_out(Corpus(id="c", workspace_id="w", name="n", created_at=T))["created_at"] == "2026-11-20T09:15:00Z"

    def test_naive_treated_as_utc(self):
        naive = datetime(2026, 11, 20, 9, 15, 0)
        assert S.corpus_out(Corpus(id="c", workspace_id="w", name="n", created_at=naive))["created_at"] == "2026-11-20T09:15:00Z"

    def test_other_timezone_converted_to_utc(self):
        msk = timezone(timedelta(hours=3))
        out = S.corpus_out(Corpus(id="c", workspace_id="w", name="n", created_at=datetime(2026, 11, 20, 12, 15, tzinfo=msk)))
        assert out["created_at"] == "2026-11-20T09:15:00Z"

    def test_none_stays_null(self):
        assert S.corpus_out(Corpus(id="c", workspace_id="w", name="n"))["created_at"] is None


class TestCorpusFileOut:
    def _file(self, **kw) -> CorpusFile:
        base = dict(id="cf_1", corpus_id="crp_solar", name="pdfs/2021/D001.pdf", publication_id="D001",
                    sha256="ab" * 32, size_bytes=1234567, page_count=9, status=CorpusFileStatus.done,
                    paper_id="pap_1", source="zip", storage_key="secret/storage/key", created_at=T)
        base.update(kw)
        return CorpusFile(**base)

    def test_done_file(self):
        out = S.corpus_file_out(self._file())
        assert out["file_id"] == "cf_1" and out["corpus_id"] == "crp_solar"
        assert out["name"] == "pdfs/2021/D001.pdf" and out["publication_id"] == "D001"
        assert out["size"] == 1234567 and out["page_count"] == 9 and out["sha256"] == "ab" * 32
        assert out["status"] == "done" and out["progress"] == 100
        assert out["paper_id"] == "pap_1" and out["source"] == "zip"
        assert out["error"] is None
        for k in ("stage", "title", "duplicate_of", "facts", "issues", "attempts", "model_version", "from_cache",
                  "updated_at"):
            assert k in out, k
        _json_safe(out)

    def test_storage_key_not_leaked(self):
        out = S.corpus_file_out(self._file())
        assert "storage_key" not in out and "secret/storage/key" not in json.dumps(out)

    def test_failed_file_has_error_object(self):
        out = S.corpus_file_out(self._file(status=CorpusFileStatus.failed, error_code="pdf_encrypted",
                                           error_message="PDF защищён паролем", paper_id=None))
        assert out["status"] == "failed"
        assert out["error"] == {"code": "pdf_encrypted", "message": "PDF защищён паролем"}

    def test_pending_progress_zero(self):
        out = S.corpus_file_out(self._file(status=CorpusFileStatus.pending, paper_id=None))
        assert out["progress"] == 0

    def test_duplicate_of_passthrough(self):
        out = S.corpus_file_out(self._file(status=CorpusFileStatus.skipped_duplicate, duplicate_of="cf_0"))
        assert out["status"] == "skipped_duplicate" and out["duplicate_of"] == "cf_0"

    @pytest.mark.parametrize("status", list(CorpusFileStatus))
    def test_every_status_serializes(self, status):
        out = S.corpus_file_out(self._file(status=status))
        assert out["status"] == status.value
        assert 0 <= out["progress"] <= 100

    def test_missing_optional_fields(self):
        f = CorpusFile(id="cf_2", corpus_id="c", name="a.pdf", publication_id="a")
        out = S.corpus_file_out(f)
        assert out["size"] is None and out["page_count"] is None and out["sha256"] is None


class TestRunOut:
    def _run(self, **kw) -> Run:
        base = dict(id="run_12", corpus_id="crp_solar", kind=RunKind.full, status=RunStatus.running,
                    counts=RunCounts(total=148, queued=80, processing=4, done=60, failed=2, skipped=2),
                    model_version="extract-0.3", concurrency=4, keep_review=True, profile_id="prf_solar_tz_v1",
                    started_by="usr_01", started_at=T)
        base.update(kw)
        return Run(**base)

    def test_shape(self):
        out = S.run_out(self._run())
        assert out["run_id"] == "run_12" and out["corpus_id"] == "crp_solar"
        assert out["kind"] == "full" and out["status"] == "running"
        assert out["counts"] == {"total": 148, "queued": 80, "processing": 4, "done": 60, "failed": 2,
                                 "skipped": 2, "cancelled": 0}
        assert out["paused"] is False and out["pause_reason"] is None
        assert out["keep_review"] is True and out["concurrency"] == 4
        assert out["model_version"] == "extract-0.3" and out["profile_id"] == "prf_solar_tz_v1"
        assert out["output"] is None and out["finished_at"] is None
        assert ISO_Z.match(out["started_at"])
        assert isinstance(out["progress"], int) and 0 <= out["progress"] <= 100
        _json_safe(out)

    def test_progress_zero_when_total_zero(self):
        assert S.run_out(self._run(counts=RunCounts()))["progress"] == 0

    def test_progress_hundred_when_all_done(self):
        out = S.run_out(self._run(status=RunStatus.done, counts=RunCounts(total=3, done=3), finished_at=T))
        assert out["progress"] == 100 and ISO_Z.match(out["finished_at"])

    def test_progress_monotonic_with_done(self):
        p1 = S.run_out(self._run(counts=RunCounts(total=10, queued=9, done=1)))["progress"]
        p2 = S.run_out(self._run(counts=RunCounts(total=10, queued=4, done=6)))["progress"]
        assert p1 < p2

    def test_paused_run(self):
        out = S.run_out(self._run(paused=True, pause_reason="ml_unavailable"))
        assert out["paused"] is True and out["pause_reason"] == "ml_unavailable" and out["status"] == "running"

    def test_output_serialized(self):
        out = S.run_out(self._run(status=RunStatus.done, output=RunOutput(
            available=True, url="/api/v1/runs/run_12/output", csv_files=["results.csv"], check_status="ok",
            built_at=T)))
        o = out["output"]
        assert o["available"] is True and o["csv_files"] == ["results.csv"] and o["check_status"] == "ok"
        _json_safe(out)

    @pytest.mark.parametrize("status", list(RunStatus))
    def test_all_statuses(self, status):
        assert S.run_out(self._run(status=status))["status"] == status.value


# --- paper_out ---------------------------------------------------------------------------------

class TestPaperOut:
    def _paper(self, mini_gold_dir, **kw) -> Paper:
        pub = next(r for r in _gold_rows(mini_gold_dir, "publications") if r["publication_id"] == "D001")
        base = dict(id="pap_7d1x0c2b9qre", workspace_id="ws", corpus_id="crp_solar", file_id="cf_1",
                    publication_id=pub["publication_id"], title=pub["title"], year=int(pub["year"]), doi=pub["DOI"],
                    in_scope=True, status=PaperStatus.done, sha256="cd" * 32, page_count=9, created_at=T,
                    processed_at=T, model_version="mock-gold-abc12345")
        base.update(kw)
        return Paper(**base)

    def test_basic_fields_from_gold(self, mini_gold_dir):
        p = self._paper(mini_gold_dir)
        out = S.paper_out(p, None)
        assert out["id"] == out["paper_id"] == "pap_7d1x0c2b9qre"
        assert out["publication_id"] == "D001" and out["corpus_id"] == "crp_solar"
        assert out["title"].startswith("Improved Photovoltaic Performance")
        assert out["year"] == 2021 and out["doi"] == "10.3389/fenrg.2021.692059"
        assert out["collection"] == "solar" and out["in_scope"] is True and out["page_count"] == 9
        assert out["processing"]["status"] == "done"
        for k in ("authors", "authors_text", "journal", "keywords", "file", "mine", "added_at", "chat_id",
                  "topic_by", "dup_of", "dup_kind", "model_version", "rows", "claims", "quality",
                  "coverage_summary", "open_issues", "top_level", "facts_count", "exclusion_reason"):
            assert k in out, k
        assert out["chat_id"] is None and out["claims"] == [] and out["topic_by"] == "project"
        _json_safe(out)

    def test_doi_prefix_stripped(self, mini_gold_dir):
        out = S.paper_out(self._paper(mini_gold_dir, doi="https://doi.org/10.1002/eom2.12202"), None)
        assert out["doi"] == "10.1002/eom2.12202"

    def test_doi_none(self, mini_gold_dir):
        assert S.paper_out(self._paper(mini_gold_dir, doi=None), None)["doi"] is None

    def test_stats_argument_is_used(self, mini_gold_dir):
        stats = PaperStats(quality=Quality(score=0.9, level="good"), coverage=Coverage(filled=5, total=7, ratio=5 / 7),
                           open_issues=2, top_level="conflict", facts_count=18)
        out = S.paper_out(self._paper(mini_gold_dir), stats)
        assert out["open_issues"] == 2 and out["top_level"] == "conflict" and out["facts_count"] == 18
        assert out["coverage_summary"] == {"filled": 5, "total": 7}
        assert {"total", "sourced_pct", "normalized_pct", "accepted", "issues"} <= set(out["quality"])

    def test_stats_default_from_paper(self, mini_gold_dir):
        p = self._paper(mini_gold_dir, stats=PaperStats(open_issues=4, facts_count=3, top_level="doubt"))
        out = S.paper_out(p)
        assert out["open_issues"] == 4 and out["facts_count"] == 3 and out["top_level"] == "doubt"

    def test_explicit_stats_override_paper_stats(self, mini_gold_dir):
        p = self._paper(mini_gold_dir, stats=PaperStats(open_issues=4))
        assert S.paper_out(p, PaperStats(open_issues=1))["open_issues"] == 1

    def test_failed_paper(self, mini_gold_dir):
        out = S.paper_out(self._paper(mini_gold_dir, status=PaperStatus.failed, in_scope=None, error_code="pdf_broken",
                                      title=None), None)
        assert out["processing"]["status"] == "failed"
        assert out["processing"]["error"]["code"] == "pdf_broken"
        assert out["in_scope"] is None
        assert out["facts_count"] == 0

    def test_title_falls_back_to_file_name_before_parse(self, mini_gold_dir):
        out = S.paper_out(self._paper(mini_gold_dir, title=None, status=PaperStatus.queued), None)
        assert out["title"]  # до разбора - имя файла/publication_id, не null
        assert out["processing"]["status"] == "queued"

    def test_duplicate_markers(self, mini_gold_dir):
        out = S.paper_out(self._paper(mini_gold_dir, dup_of="pap_0", dup_kind="doi"), None)
        assert out["dup_of"] == "pap_0" and out["dup_kind"] == "doi"

    @pytest.mark.parametrize("status", list(PaperStatus))
    def test_all_statuses(self, mini_gold_dir, status):
        assert S.paper_out(self._paper(mini_gold_dir, status=status), None)["processing"]["status"] == status.value


# --- fact_out ----------------------------------------------------------------------------------

class TestFactOut:
    def test_shape_for_gold_row(self, fact, normalizer):
        out = S.fact_out(fact, normalizer)
        assert out["fact_id"] == "fct_4kq9w2m1z8ab" and out["paper_id"] == "pap_7d1x0c2b9qre"
        assert out["publication_id"] == "D001" and out["param"] == "voc" and out["metric_name"] == "Voc"
        assert out["raw_value"] == "1197" and out["raw_unit"] == "mV"
        assert out["normalized_value"] == 1.197 and out["normalized_unit"] == "V"
        assert out["norm_status"] == "converted" and out["extraction_status"] == "extracted"
        assert out["review_status"] == "new" and out["added_by"] == "auto"
        assert out["source_page"] == 5 and out["source_kind"] == "table" and out["source_table"] == "TABLE 1"
        assert out["source_fragment"] == "Table 1, строка pristine"
        assert out["quote_found"] is True and out["anchor_id"] == "anc_p5_0001"
        assert out["row_id"] == "DV1" and out["row_label"] == "pristine"
        assert out["exportable"] is True and out["confidence"] == 0.9
        assert out["corrected_value"] is None and out["corrected_raw"] is None
        for k in ("param_label", "metric_value", "metric_unit", "normalized_text", "display", "value", "issue_ids",
                  "duplicate_of", "created_at", "updated_at", "model_version", "source_section", "cross", "note"):
            assert k in out, k
        _json_safe(out)

    def test_target_entity_is_singular_per_contract_u7(self, fact, normalizer):
        t = S.fact_out(fact, normalizer)["target"]
        assert t == {"entity": "result", "id": "R002", "field": "metric_value"}

    @pytest.mark.parametrize("plural,singular", [("results", "result"), ("materials", "material"), ("devices", "device"),
                                                  ("layers", "layer"), ("measurements", "measurement")])
    def test_target_entity_mapping(self, fact, normalizer, plural, singular):
        fact.target = {"entity": plural, "id": "X1", "field": "f"}
        assert S.fact_out(fact, normalizer)["target"]["entity"] == singular

    def test_numbers_are_json_numbers_without_float_noise(self, fact, normalizer):
        fact.normalized_value = Decimal("1.197")
        raw = json.dumps(S.fact_out(fact, normalizer))
        assert '"normalized_value": 1.197' in raw and "1.1970000" not in raw
        assert '"value": 1.197' in raw

    def test_value_is_normalized_when_not_corrected(self, fact, normalizer):
        out = S.fact_out(fact, normalizer)
        assert out["value"] == 1.197 and out["metric_value"] == 1.197 and out["metric_unit"] == "V"

    def test_value_prefers_corrected(self, fact, normalizer):
        fact.corrected_value = Decimal("1.08")
        fact.corrected_raw = "1,08 В"
        fact.review_status = ReviewStatus.accepted
        out = S.fact_out(fact, normalizer)
        assert out["value"] == 1.08 and out["corrected_value"] == 1.08 and out["corrected_raw"] == "1,08 В"
        assert out["review_status"] == "accepted" and out["normalized_value"] == 1.197

    def test_display_uses_normalizer_with_value_and_unit(self, fact, normalizer):
        out = S.fact_out(fact, normalizer)
        assert out["display"] == "1,197 V"
        assert normalizer.display_calls[-1] == (Decimal("1.197"), "V")

    def test_display_of_corrected_value(self, fact, normalizer):
        fact.corrected_value = Decimal("1.08")
        assert S.fact_out(fact, normalizer)["display"] == "1,08 V"

    def test_null_value_has_empty_or_null_display(self, fact, normalizer):
        fact.normalized_value = None
        fact.normalized_unit = None
        fact.norm_status = NormStatus.failed
        out = S.fact_out(fact, normalizer)
        assert out["value"] is None and out["normalized_value"] is None
        assert out["display"] in (None, "")

    def test_manual_fact_without_page(self, fact, normalizer):
        fact.added_by = AddedBy.manual
        fact.source_kind = "manual"
        fact.source_page = None
        fact.source_fragment = None
        fact.exportable = False
        out = S.fact_out(fact, normalizer)
        assert out["added_by"] == "manual" and out["source_kind"] == "manual" and out["exportable"] is False
        assert out["source_page"] is None

    def test_duplicate_of_passthrough(self, fact, normalizer):
        fact.duplicate_of = "fct_main"
        assert S.fact_out(fact, normalizer)["duplicate_of"] == "fct_main"

    def test_extraction_status_absent(self, fact, normalizer):
        fact.extraction_status = "absent"
        fact.exportable = False
        out = S.fact_out(fact, normalizer)
        assert out["extraction_status"] == "absent" and out["exportable"] is False

    def test_text_fact_conditions_has_normalized_text(self, fact, normalizer):
        fact.param = "conditions"
        fact.metric_name = None
        fact.normalized_value = None
        fact.normalized_unit = None
        fact.raw_value = "AM1.5G, 100 mW/cm2"
        out = S.fact_out(fact, normalizer)
        assert out["metric_name"] is None and "normalized_text" in out


# --- anchor_out --------------------------------------------------------------------------------

class TestAnchorOut:
    def _anchor(self, **kw) -> Anchor:
        base = dict(id="anc_p13_0007", paper_id="pap_1", kind="quote", page=13,
                    rects=[Rect(x=0.598, y=0.52, w=0.059, h=0.017)], text="exhibited PCE = 5.25%",
                    fact_ids=["fct_4kq9w2m1z8ab"])
        base.update(kw)
        return Anchor(**base)

    def test_shape(self):
        out = S.anchor_out(self._anchor())
        assert out["anchor_id"] == "anc_p13_0007" and out["page"] == 13
        assert out["rects"] == [{"x": 0.598, "y": 0.52, "w": 0.059, "h": 0.017}]
        assert out["text"] == "exhibited PCE = 5.25%" and out["fact_ids"] == ["fct_4kq9w2m1z8ab"]
        assert out["issue_ids"] == []
        assert out["kind"] in {"cell", "text", "figure", "manual", "caption", "reference"}
        _json_safe(out)

    def test_kind_mapping_table_and_figure(self):
        assert S.anchor_out(self._anchor(kind="table"))["kind"] == "cell"
        assert S.anchor_out(self._anchor(kind="figure"))["kind"] == "figure"

    @pytest.mark.parametrize("kind", ["value", "quote", "table", "figure"])
    def test_every_domain_kind_maps_into_contract_set(self, kind):
        assert S.anchor_out(self._anchor(kind=kind))["kind"] in {"cell", "text", "figure", "manual", "caption", "reference"}

    def test_empty_rects_and_text(self):
        out = S.anchor_out(self._anchor(rects=[], text=None, fact_ids=[]))
        assert out["rects"] == [] and out["text"] is None and out["fact_ids"] == []

    def test_rects_are_fractions(self):
        for r in S.anchor_out(self._anchor())["rects"]:
            assert all(0 <= r[k] <= 1 for k in "xywh")


# --- issue_out ---------------------------------------------------------------------------------

class TestIssueOut:
    def _issue(self, mini_gold_dir=None, **kw) -> Issue:
        rec = IssueRecord(publication_id="D021", issue_id="I1", record_ids=["R010", "R007"], check_name="jv_consistency",
                          description="PCE 2,36 % не согласуется с Voc·Jsc·FF/Pin = 3,11 % (расхождение 31,7 %)",
                          source_page=6, source_fragment="Table 2, строка A1", severity="warn", computed_by="backend",
                          numbers={"calc": 3.11, "reported": 2.36, "diff_pct": 31.7, "threshold_pct": 3})
        base = dict(id="iss_d021_jv_r010", paper_id="pap_7d1x0c2b9qre", key="I-abc", record=rec,
                    fact_ids=["fct_1", "fct_2"], anchor_ids=["anc_1"], rule_version="1", created_at=T)
        base.update(kw)
        return Issue(**base)

    def test_shape(self):
        out = S.issue_out(self._issue())
        assert out["issue_id"] == "iss_d021_jv_r010" and out["check_name"] == "jv_consistency"
        assert out["paper_id"] == "pap_7d1x0c2b9qre" and out["publication_id"] == "D021"
        assert out["scope"] == "paper" and out["collection"] == "solar"
        assert out["severity"] == "warn" and out["ui_level"] == "doubt"
        assert out["record_ids"] == ["R010", "R007"] and out["fact_ids"] == ["fct_1", "fct_2"]
        assert out["anchor_ids"] == ["anc_1"]
        assert out["source_page"] == 6 and out["source_fragment"] == "Table 2, строка A1"
        assert out["status"] == "open" and out["stale"] is False and out["merged"] is False
        assert out["computed_by"] == "backend" and out["rule_version"] == "1"
        assert out["numbers"]["diff_pct"] == 31.7
        for k in ("plugin", "title", "resolved_by", "resolved_at", "touched_fact_ids", "confidence", "related",
                  "main_fact_id", "created_at", "updated_at"):
            assert k in out, k
        _json_safe(out)

    def test_text_equals_description_u17(self):
        out = S.issue_out(self._issue())
        assert out["text"] == out["description"] == "PCE 2,36 % не согласуется с Voc·Jsc·FF/Pin = 3,11 % (расхождение 31,7 %)"

    def test_internal_key_not_exposed_as_issue_id(self):
        out = S.issue_out(self._issue())
        assert out["issue_id"] != "I-abc" and out["issue_id"] != "I1"

    @pytest.mark.parametrize("severity,level", [("error", "conflict"), ("warn", "doubt"), ("info", "warn")])
    def test_ui_level_from_severity(self, severity, level):
        rec = self._issue().record.model_copy(update={"severity": severity})
        out = S.issue_out(self._issue(record=rec))
        assert out["severity"] == severity and out["ui_level"] == level

    def test_empty_record_ids_paper_level(self):
        rec = self._issue().record.model_copy(update={"record_ids": [], "check_name": "contradiction"})
        out = S.issue_out(self._issue(record=rec, fact_ids=[]))
        assert out["record_ids"] == [] and out["fact_ids"] == [] and out["main_fact_id"] is None

    def test_ml_issue_without_page(self):
        rec = self._issue().record.model_copy(update={"computed_by": "ml", "confidence": 0.8, "source_page": None,
                                                      "source_fragment": None, "check_name": "typo"})
        out = S.issue_out(self._issue(record=rec))
        assert out["computed_by"] == "ml" and out["confidence"] == 0.8
        assert out["source_page"] is None and out["source_fragment"] is None

    def test_merged_and_stale_flags(self):
        rec = self._issue().record.model_copy(update={"merged": True})
        out = S.issue_out(self._issue(record=rec, stale=True, status=IssueStatus.fixed))
        assert out["merged"] is True and out["stale"] is True and out["status"] == "fixed"

    def test_page_given_as_string_becomes_int_or_kept(self):
        rec = self._issue().record.model_copy(update={"source_page": "6"})
        assert S.issue_out(self._issue(record=rec))["source_page"] in (6, "6")

    def test_gold_issue_row_roundtrip(self, mini_gold_dir):
        row = _gold_rows(mini_gold_dir, "issues")[0]
        rec = IssueRecord(publication_id=row["publication_id"], issue_id=row["issue_id"],
                          record_ids=[x for x in row["record_ids"].split(";") if x], check_name=row["check_name"],
                          description=row["description"], source_page=row["source_page"],
                          source_fragment=row["source_fragment"], severity="warn", computed_by="ml")
        out = S.issue_out(Issue(id="iss_x", paper_id="pap_x", key="k", record=rec))
        assert out["check_name"] == row["check_name"] and out["publication_id"] == row["publication_id"]
        assert out["text"] == row["description"]
        _json_safe(out)


# --- history_out -------------------------------------------------------------------------------

class TestHistoryOut:
    def test_shape(self):
        a = Action(id="act_95", user_id="usr_01", paper_id="pap_1", fact_id="fct_1", action="edit",
                   label="исправление", created_at=T,
                   changes=[{"field": "corrected_value", "from": "1100 мВ", "to": "1,08 В"}])
        out = S.history_out(a)
        assert out["action_id"] == "act_95" and out["user_id"] == "usr_01"
        assert out["action"] == "edit" and out["label"] == "исправление"
        assert out["paper_id"] == "pap_1" and out["fact_id"] == "fct_1"
        assert ISO_Z.match(out["at"]) and out["undone"] is False
        for k in ("who", "paper_title", "param_label", "row_label", "from", "to", "count"):
            assert k in out, k
        _json_safe(out)

    def test_undone_flag(self):
        assert S.history_out(Action(id="a", user_id="u", action="accept", label="принято", undone=True))["undone"] is True

    def test_group_action_has_no_fact(self):
        out = S.history_out(Action(id="act_90", user_id="u", action="acceptAll", label="принять все",
                                   paper_id="pap_chunks", group_id="g1",
                                   changes=[{"fact_id": f"f{i}"} for i in range(6)]))
        assert out["action"] == "acceptAll" and out["fact_id"] is None
        assert out["count"] in (6, None)

    def test_missing_created_at(self):
        out = S.history_out(Action(id="a", user_id="u", action="add", label="добавление"))
        assert out["at"] is None or isinstance(out["at"], str)


# --- export_out --------------------------------------------------------------------------------

class TestExportOut:
    def _export(self, **kw) -> Export:
        base = dict(id="exp_3k9m2q7x1aa", workspace_id="ws", user_id="usr_01", format="submission",
                    scope={"corpus_id": "crp_solar"}, status="done", file_key="exports/exp_3k9m2q7x1aa.zip",
                    created_at=T, finished_at=T + timedelta(seconds=91))
        base.update(kw)
        return Export(**base)

    def test_done(self):
        out = S.export_out(self._export())
        assert out["export_id"] == "exp_3k9m2q7x1aa" and out["format"] == "submission" and out["status"] == "done"
        assert out["scope"] == {"corpus_id": "crp_solar"}
        assert out["url"].endswith("/api/v1/exports/exp_3k9m2q7x1aa/file")
        for k in ("papers", "facts", "filename", "expires_at", "check", "error", "action_id", "created_at",
                  "finished_at"):
            assert k in out, k
        assert out["error"] is None
        assert "file_key" not in out
        _json_safe(out)

    @pytest.mark.parametrize("domain_status", ["queued", "running"])
    def test_in_progress_maps_to_processing_without_url(self, domain_status):
        out = S.export_out(self._export(status=domain_status, file_key=None, finished_at=None))
        assert out["status"] == "processing" and out["url"] is None and out["finished_at"] is None

    def test_failed_has_error_object(self):
        out = S.export_out(self._export(status="failed", error="Набор не прошёл проверку: 3 ошибки"))
        assert out["status"] == "failed"
        assert isinstance(out["error"], dict) and {"code", "message"} <= set(out["error"])
        assert "3 ошибки" in out["error"]["message"]

    def test_filename_is_zip_for_submission(self):
        assert S.export_out(self._export())["filename"].endswith(".zip")
