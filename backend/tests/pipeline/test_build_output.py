"""build_output: единственная сборка выходной папки для CLI, POST /exports и POST /extract (план 5.6)."""
from __future__ import annotations

import csv
import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from faktura.domain.entities import AddedBy, FactDraft, FactFilter, OutputResult, ReviewStatus
from faktura.domain.tables import TABLES
from faktura.pipeline import build_output

TABLE_FILES = sorted(f"{t}.csv" for t in TABLES)


@pytest.fixture
def env(make_env):
    return make_env()


@pytest.fixture
def done_corpus(env, pdf_dir):
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id, mode="cli")
    run = env.full_run(c.id)
    return c, run


def rows(out: Path, table: str) -> list[dict]:
    with open(out / f"{table}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


class _RecPath(type(Path())):
    """Path с атрибутом record: у pathlib.Path в Python 3.12 нет __dict__, присвоить p.record нельзя."""


@pytest.fixture
def recording_validator(tmp_path):
    """Фальшивый check_submission.py: пишет argv и список PDF в record.json; ошибок нет."""
    rec = tmp_path / "record.json"
    p = _RecPath(tmp_path / "check_rec.py")
    p.write_text(
        "import json, os, sys\n"
        f"rec = {str(rec)!r}\n"
        "pdfs = sorted(os.listdir(sys.argv[2])) if len(sys.argv) > 2 and os.path.isdir(sys.argv[2]) else None\n"
        "csvs = sorted(os.listdir(sys.argv[1]))\n"
        "json.dump({'argv': sys.argv[1:], 'pdfs': pdfs, 'csvs': csvs}, open(rec, 'w'))\n"
        "print('Ошибок: 0')\n", encoding="utf-8")
    p.record = rec  # type: ignore[attr-defined]
    return p


def build(env, corpus, out, profile, **kw):
    kw.setdefault("check_script", None)
    return build_output(env.services, corpus.id, out, profile=profile, **kw)


# --- структура выхода ------------------------------------------------------------------------------------------

def test_exactly_seven_csv_and_meta(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    res = build(env, done_corpus[0], out, profile)
    assert isinstance(res, OutputResult)
    assert sorted(p.name for p in out.glob("*.csv")) == TABLE_FILES
    assert sorted(p.name for p in res.csv_paths) == TABLE_FILES
    assert res.meta_dir == out / "_meta" and res.meta_dir.is_dir()


def test_out_dir_created_when_missing(env, done_corpus, profile, tmp_path):
    out = tmp_path / "a" / "b" / "out"
    build(env, done_corpus[0], out, profile)
    assert (out / "publications.csv").is_file()


def test_csv_headers_match_template(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    for t, cols in TABLES.items():
        with open(out / f"{t}.csv", encoding="utf-8-sig", newline="") as f:
            assert tuple(next(csv.reader(f))) == cols


def test_rows_of_all_papers_present(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    assert [r["publication_id"] for r in rows(out, "publications")] == ["D001", "D003"]
    assert len(rows(out, "results")) == 47 + 21
    assert {r["publication_id"] for r in rows(out, "layers")} == {"D001", "D003"}


def test_issue_ids_renumbered_per_paper(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    for pid in ("D001", "D003"):
        ids = [r["issue_id"] for r in rows(out, "issues") if r["publication_id"] == pid]
        assert ids == [f"I{n}" for n in range(1, len(ids) + 1)] and ids


def test_deterministic_bytes(env, done_corpus, profile, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    build(env, done_corpus[0], a, profile)
    build(env, done_corpus[0], b, profile)
    for t in TABLES:
        assert (a / f"{t}.csv").read_bytes() == (b / f"{t}.csv").read_bytes()


def test_empty_corpus_gives_headers_only(env, profile, tmp_path):
    c = env.new_corpus("empty")
    out = tmp_path / "out"
    res = build(env, c, out, profile)
    assert sorted(p.name for p in out.glob("*.csv")) == TABLE_FILES
    assert all(rows(out, t) == [] for t in TABLES) and res.exit_code_hint == 0


def test_unknown_corpus_not_found(env, profile, tmp_path):
    from faktura.domain.errors import AppError
    with pytest.raises(AppError) as ei:
        build_output(env.services, "crp_nope", tmp_path / "o", profile=profile, check_script=None)
    assert ei.value.code == "not_found"


# --- _meta ------------------------------------------------------------------------------------------------------------

def test_meta_files(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    names = {p.name for p in (out / "_meta").iterdir()}
    assert {"manifest.json", "passport.json", "passport.md", "files.csv", "facts_ui.json", "errors.log"} <= names


def test_include_meta_false_no_meta_dir(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    res = build(env, done_corpus[0], out, profile, include_meta=False)
    assert not (out / "_meta").exists() and res.meta_dir is None


def test_meta_json_files_parse(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    for n in ("manifest.json", "passport.json", "facts_ui.json"):
        json.loads((out / "_meta" / n).read_text(encoding="utf-8"))
    assert (out / "_meta" / "passport.md").read_text(encoding="utf-8").strip()


def test_manifest_mentions_run_and_counts(env, done_corpus, profile, tmp_path):
    c, run = done_corpus
    out = tmp_path / "out"
    build(env, c, out, profile, run=run)
    text = (out / "_meta" / "manifest.json").read_text(encoding="utf-8")
    assert run.id in text


def test_files_csv_columns_and_rows(env, done_corpus, profile, tmp_path):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile)
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        got = list(rd)
    assert {"file", "publication_id", "status", "error_code", "sha256", "pages", "from_cache", "dup_of",
            "dup_kind", "facts", "issues"} <= set(rd.fieldnames)
    byname = {r["file"]: r for r in got}
    assert byname["D001.pdf"]["status"] == "done" and byname["D001.pdf"]["facts"] == "47"


# --- упавшие файлы: failed_stub (REQ-024) ---------------------------------------------------------------------------------------

def test_failed_file_gets_stub_row_and_errors_log(env, pdf_dir, profile, tmp_path):
    env.spy.fail_on["D003"] = "ml_error"
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id, mode="cli")
    env.full_run(c.id)
    out = tmp_path / "out"
    res = build(env, c, out, profile)
    pubs = {r["publication_id"]: r for r in rows(out, "publications")}
    assert set(pubs) == {"D001", "D003"}
    stub = pubs["D003"]
    assert all(v == "" for k, v in stub.items() if k != "publication_id")
    assert [r for r in rows(out, "results") if r["publication_id"] == "D003"] == []
    log = (out / "_meta" / "errors.log").read_text(encoding="utf-8")
    assert "D003" in log and "ml_error" in log
    assert res.exit_code_hint == 2


def test_rejected_at_intake_cli_files_appear_as_stub(env, pdf_dir, profile, tmp_path):
    (pdf_dir / "BROKEN.pdf").write_bytes(b"%PDF-1.4\ngarbage")
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id, mode="cli")
    env.full_run(c.id)
    out = tmp_path / "out"
    res = build(env, c, out, profile)
    assert "BROKEN" in [r["publication_id"] for r in rows(out, "publications")]
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        st = {r["file"]: r for r in csv.DictReader(f)}
    assert st["BROKEN.pdf"]["status"] == "failed" and st["BROKEN.pdf"]["error_code"] == "pdf_broken"
    assert res.exit_code_hint == 2


def test_same_content_two_rows_files_csv_dup(env, tmp_path, make_pdf, profile):
    d = tmp_path / "same"
    src = make_pdf(["identical"], name="D001.pdf", directory=d)
    shutil.copy(src, d / "D003.pdf")
    c = env.new_corpus()
    env.add_dir(d, c.id, mode="cli")
    env.full_run(c.id)
    out = tmp_path / "out"
    build(env, c, out, profile)
    assert [r["publication_id"] for r in rows(out, "publications")] == ["D001", "D003"]
    with open(out / "_meta" / "files.csv", encoding="utf-8-sig", newline="") as f:
        st = {r["file"]: r for r in csv.DictReader(f)}
    assert st["D003.pdf"]["dup_kind"] == "file" and st["D003.pdf"]["dup_of"]
    assert st["D001.pdf"]["dup_kind"] == ""


# --- правки рецензента попадают в сдачу ----------------------------------------------------------------------------------------------------

def _result_fact(env, paper_id, rid):
    with env.uow() as uow:
        return next(r.fact for r in uow.facts.query(FactFilter(paper_ids=[paper_id]), [], 1000, None).data
                    if r.fact.target.get("id") == rid)


def _paper(env, corpus, name="D001.pdf"):
    with env.uow() as uow:
        return next(f.paper_id for f in uow.files.list(corpus.id) if f.name == name)


def test_rejected_fact_not_in_submission(env, done_corpus, profile, tmp_path):
    c = done_corpus[0]
    pid = _paper(env, c)
    f = _result_fact(env, pid, "R004")
    with env.uow() as uow:
        uow.facts.update_review(f.id, review_status=ReviewStatus.rejected)
    out = tmp_path / "out"
    build(env, c, out, profile)
    assert "R004" not in [r["result_id"] for r in rows(out, "results") if r["publication_id"] == "D001"]


def test_corrected_value_written(env, done_corpus, profile, tmp_path):
    c = done_corpus[0]
    pid = _paper(env, c)
    f = _result_fact(env, pid, "R004")
    with env.uow() as uow:
        uow.facts.update_review(f.id, corrected_value=Decimal("13.5"), review_status=ReviewStatus.accepted)
    out = tmp_path / "out"
    build(env, c, out, profile)
    r = next(r for r in rows(out, "results") if r["publication_id"] == "D001" and r["result_id"] == "R004")
    assert Decimal(r["metric_value"]) == Decimal("13.5") and Decimal(r["normalized_value"]) == Decimal("13.5")


def test_manual_fact_materialised_as_result_row(env, done_corpus, profile, tmp_path):
    c = done_corpus[0]
    pid = _paper(env, c)
    draft = FactDraft(ref="m1", target={"entity": "results", "id": None, "field": "metric_value"}, param="pce",
                      metric_name="PCE", row_id="DV1", raw_value="9.9", raw_unit="%", normalized_value=Decimal("9.9"),
                      normalized_unit="%", source_page=5, source_fragment="Table 1, строка pristine",
                      added_by=AddedBy.manual)
    with env.uow() as uow:
        uow.facts.add_manual(pid, draft)
    out = tmp_path / "out"
    build(env, c, out, profile)
    got = [r for r in rows(out, "results") if r["publication_id"] == "D001"]
    assert len(got) == 48 and any(Decimal(r["metric_value"]) == Decimal("9.9") for r in got if r["metric_value"])


# --- проверка сдачи ----------------------------------------------------------------------------------------------------------------------------------

def test_no_script_check_not_run(env, done_corpus, profile, tmp_path):
    res = build(env, done_corpus[0], tmp_path / "out", profile)
    assert res.check is None or res.check.ran is False
    assert res.exit_code_hint == 0 and not [p for p in res.problems if p.level == "error"]


def test_script_ok(env, done_corpus, profile, tmp_path, fake_validator):
    res = build(env, done_corpus[0], tmp_path / "out", profile, check_script=fake_validator("ok"))
    assert res.check.ran and res.check.ok is True and res.exit_code_hint == 0
    assert "Ошибок: 0" in (tmp_path / "out" / "_meta" / "check_report.txt").read_text(encoding="utf-8")


def test_script_errors_hint_3(env, done_corpus, profile, tmp_path, fake_validator):
    res = build(env, done_corpus[0], tmp_path / "out", profile, check_script=fake_validator("errors"))
    assert res.check.ok is False and res.check.errors and res.exit_code_hint == 3
    assert sorted(p.name for p in (tmp_path / "out").glob("*.csv")) == TABLE_FILES  # набор всё равно записан


def test_script_crash_hint_3(env, done_corpus, profile, tmp_path, fake_validator):
    res = build(env, done_corpus[0], tmp_path / "out", profile, check_script=fake_validator("crash"))
    assert res.check.crashed is True and res.exit_code_hint == 3


def test_errors_beat_partial_failure_in_hint(env, pdf_dir, profile, tmp_path, fake_validator):
    env.spy.fail_on["D003"] = "ml_error"
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id, mode="cli")
    env.full_run(c.id)
    res = build(env, c, tmp_path / "out", profile, check_script=fake_validator("errors"))
    assert res.exit_code_hint == 3


def test_missing_script_path_is_not_silent(env, done_corpus, profile, tmp_path):
    res = build(env, done_corpus[0], tmp_path / "out", profile, check_script=tmp_path / "nope.py")
    assert res.check is not None and (res.check.crashed or res.check.ok is False)
    assert res.exit_code_hint == 3


def test_pdfs_materialised_from_store_for_validator(env, done_corpus, profile, tmp_path, recording_validator):
    build(env, done_corpus[0], tmp_path / "out", profile, check_script=recording_validator)
    rec = json.loads(recording_validator.record.read_text())
    assert rec["pdfs"] == ["D001.pdf", "D003.pdf"]
    assert set(TABLE_FILES) <= set(rec["csvs"])
    assert not Path(rec["argv"][1]).exists(), "временная папка PDF должна быть убрана после проверки"


def test_explicit_pdf_dir_is_used(env, done_corpus, profile, tmp_path, recording_validator, pdf_dir):
    build(env, done_corpus[0], tmp_path / "out", profile, check_script=recording_validator, pdf_dir=pdf_dir)
    rec = json.loads(recording_validator.record.read_text())
    assert Path(rec["argv"][1]) == pdf_dir and rec["pdfs"] == ["D001.pdf", "D003.pdf"]


def test_validator_gets_csv_dir_equal_out(env, done_corpus, profile, tmp_path, recording_validator):
    out = tmp_path / "out"
    build(env, done_corpus[0], out, profile, check_script=recording_validator)
    assert Path(json.loads(recording_validator.record.read_text())["argv"][0]) == out


# --- (data) реальный валидатор на эталоне ------------------------------------------------------------------------------------------------------------

@pytest.mark.data
def test_real_validator_on_dev_gold(make_env, dev_dir, dev_gold_dir, check_script, profile, tmp_path):
    env = make_env(gold_dir=dev_gold_dir)
    c = env.new_corpus("dev")
    env.add_dir(dev_dir, c.id, mode="cli")
    env.full_run(c.id)
    res = build(env, c, tmp_path / "out", profile, check_script=check_script, pdf_dir=dev_dir)
    assert res.check.ok is True and res.check.errors == [] and res.exit_code_hint == 0
    assert not [p for p in res.problems if p.level == "error"]
