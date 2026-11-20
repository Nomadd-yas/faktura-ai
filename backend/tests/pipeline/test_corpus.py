"""CorpusService: create, manual_corpus, add_inputs (api/cli, дубли, reuse_existing) - план 5.6, REQ-024, REQ-074."""
from __future__ import annotations

import shutil

import pytest

from faktura.domain.entities import CorpusFileStatus, DupKind
from faktura.domain.errors import AppError
from faktura.pdfio.inputs import CollectResult, InputFile, Rejected
from faktura.pipeline import CorpusService


@pytest.fixture
def env(make_env):
    return make_env()


def files_of(env, corpus_id):
    with env.uow() as uow:
        return {f.name: f for f in uow.files.list(corpus_id)}


@pytest.fixture
def broken_pdf(tmp_path):
    d = tmp_path / "bad"
    d.mkdir()
    p = d / "BROKEN.pdf"
    p.write_bytes(b"%PDF-1.4\nthis is not a pdf at all\n")
    return d


@pytest.fixture
def encrypted_pdf(tmp_path):
    import fitz
    d = tmp_path / "enc"
    d.mkdir()
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "secret")
    doc.save(d / "ENC.pdf", encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    return d


# --- create / manual_corpus -------------------------------------------------------------------

def test_create_regular_defaults(env):
    c = CorpusService(env.services).create(env.workspace_id, env.user_id, "Мой корпус", "solar")
    assert c.id and c.kind == "regular" and c.collection == "solar" and c.name == "Мой корпус"
    assert c.workspace_id == env.workspace_id and c.created_by == env.user_id
    with env.uow() as uow:
        assert uow.corpora.get(c.id).name == "Мой корпус"


def test_create_with_kind_profile_project(env):
    c = CorpusService(env.services).create(env.workspace_id, env.user_id, "X", "solar", kind="extract",
                                           profile_id="prf_solar_tz_v1", project_id="prj_1")
    assert (c.kind, c.profile_id, c.project_id) == ("extract", "prf_solar_tz_v1", "prj_1")


def test_manual_corpus_is_idempotent(env):
    svc = CorpusService(env.services)
    a, b = svc.manual_corpus(env.workspace_id), svc.manual_corpus(env.workspace_id)
    assert a.id == b.id and a.kind == "manual"
    assert "вручную" in a.name


def test_manual_corpus_per_workspace(env):
    svc = CorpusService(env.services)
    assert svc.manual_corpus(env.workspace_id).id != svc.manual_corpus("ws_other").id


# --- add_inputs: нормальный приём ---------------------------------------------------------------------

def test_add_api_creates_files_and_papers(env, pdf_dir):
    c = env.new_corpus()
    r = env.add_dir(pdf_dir, c.id)
    assert sorted(f.name for f in r.added) == ["D001.pdf", "D003.pdf"]
    assert r.rejected == [] and r.skipped_duplicates == []
    f = files_of(env, c.id)["D001.pdf"]
    assert f.publication_id == "D001" and len(f.sha256) == 64 and f.page_count == 1 and f.size_bytes > 0
    assert f.status in (CorpusFileStatus.pending, CorpusFileStatus.queued)
    assert f.storage_key and env.services.store.exists(f.storage_key)
    assert f.paper_id
    with env.uow() as uow:
        p = uow.papers.get(f.paper_id)
        assert p.publication_id == "D001" and p.corpus_id == c.id and p.sha256 == f.sha256


def test_add_empty_inputs(env):
    c = env.new_corpus()
    r = CorpusService(env.services).add_inputs(c.id, CollectResult(), mode="api")
    assert (r.added, r.rejected, r.skipped_duplicates) == ([], [], [])


def test_add_to_unknown_corpus_not_found(env, pdf_dir, collect):
    with pytest.raises(AppError) as ei:
        CorpusService(env.services).add_inputs("crp_nope", collect(pdf_dir), mode="api")
    assert ei.value.code == "not_found"


def test_collect_rejections_passed_through(env, pdf_dir, collect):
    c = env.new_corpus()
    inputs = collect(pdf_dir)
    inputs.rejected.append(Rejected(name="notes.txt", code="unsupported_file", message="не PDF"))
    r = CorpusService(env.services).add_inputs(c.id, inputs, mode="api")
    assert [x.code for x in r.rejected] == ["unsupported_file"]
    assert len(r.added) == 2


# --- отказы inspect_pdf: api vs cli (REQ-024) ----------------------------------------------------------------

def test_api_broken_pdf_rejected_no_file_row(env, broken_pdf):
    c = env.new_corpus()
    r = env.add_dir(broken_pdf, c.id, mode="api")
    assert [x.code for x in r.rejected] == ["pdf_broken"] and r.added == []
    assert files_of(env, c.id) == {}


def test_cli_broken_pdf_becomes_failed_file(env, broken_pdf):
    c = env.new_corpus()
    r = env.add_dir(broken_pdf, c.id, mode="cli")
    f = files_of(env, c.id)["BROKEN.pdf"]
    assert f.status == CorpusFileStatus.failed and f.error_code == "pdf_broken"
    assert f.publication_id == "BROKEN"
    assert any(x.name == "BROKEN.pdf" for x in r.added) or r.rejected == []


def test_cli_encrypted_pdf_failed_code(env, encrypted_pdf):
    c = env.new_corpus()
    env.add_dir(encrypted_pdf, c.id, mode="cli")
    f = files_of(env, c.id)["ENC.pdf"]
    assert f.status == CorpusFileStatus.failed and f.error_code == "pdf_encrypted"


def test_api_encrypted_pdf_rejected(env, encrypted_pdf):
    c = env.new_corpus()
    r = env.add_dir(encrypted_pdf, c.id, mode="api")
    assert [x.code for x in r.rejected] == ["pdf_encrypted"]


def test_too_many_pages_limit_from_settings(env, make_pdf, tmp_path):
    env.services.settings.max_pages = 1
    d = tmp_path / "big"
    make_pdf(["p1", "p2"], name="BIG.pdf", directory=d)
    c = env.new_corpus()
    env.add_dir(d, c.id, mode="cli")
    f = files_of(env, c.id)["BIG.pdf"]
    assert f.status == CorpusFileStatus.failed and f.error_code == "too_many_pages"


def test_file_too_large_limit_from_settings(env, make_pdf, tmp_path):
    env.services.settings.max_file_mb = 0
    d = tmp_path / "huge"
    make_pdf(["x"], name="HUGE.pdf", directory=d)
    c = env.new_corpus()
    env.add_dir(d, c.id, mode="cli")
    assert files_of(env, c.id)["HUGE.pdf"].error_code == "file_too_large"


def test_good_files_not_blocked_by_bad_neighbour(env, pdf_dir, broken_pdf, collect):
    shutil.copy(broken_pdf / "BROKEN.pdf", pdf_dir / "BROKEN.pdf")
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id, mode="cli")
    fs = files_of(env, c.id)
    assert fs["D001.pdf"].status != CorpusFileStatus.failed and fs["BROKEN.pdf"].status == CorpusFileStatus.failed


# --- duplicate_name ----------------------------------------------------------------------------------------------

def test_api_duplicate_name_rejected(env, pdf_dir):
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    r = env.add_dir(pdf_dir, c.id)
    assert {x.code for x in r.rejected} == {"duplicate_name"} and r.added == []
    assert len(files_of(env, c.id)) == 2


def test_cli_duplicate_name_rejected_first_row_kept(env, pdf_dir, tmp_path, make_pdf, collect):
    # два входа с одним именем: первый принят, второй - rejected duplicate_name
    other = tmp_path / "other"
    make_pdf(["другой текст"], name="D001.pdf", directory=other)
    c = env.new_corpus()
    first = collect(pdf_dir, ["D001.pdf"])
    second = collect(other)
    inputs = CollectResult(files=first.files + second.files)
    r = CorpusService(env.services).add_inputs(c.id, inputs, mode="cli")
    assert [x.code for x in r.rejected] == ["duplicate_name"]
    assert len(files_of(env, c.id)) == 1


# --- одинаковое содержимое (sha256) -----------------------------------------------------------------------------------

@pytest.fixture
def same_content_dir(tmp_path, make_pdf):
    d = tmp_path / "same"
    src = make_pdf(["identical"], name="D001.pdf", directory=d)
    shutil.copy(src, d / "D003.pdf")
    return d


def test_api_same_sha_skipped_with_duplicate_of(env, same_content_dir):
    c = env.new_corpus()
    r = env.add_dir(same_content_dir, c.id, mode="api")
    assert [f.name for f in r.added] == ["D001.pdf"]
    assert [f.name for f in r.skipped_duplicates] == ["D003.pdf"]
    dup = files_of(env, c.id)["D003.pdf"]
    assert dup.status == CorpusFileStatus.skipped_duplicate and dup.duplicate_of == files_of(env, c.id)["D001.pdf"].id


def test_cli_same_sha_not_skipped_marked_file_dup(env, same_content_dir):
    c = env.new_corpus()
    r = env.add_dir(same_content_dir, c.id, mode="cli")
    fs = files_of(env, c.id)
    assert len(r.added) == 2 and r.skipped_duplicates == []
    assert fs["D003.pdf"].status != CorpusFileStatus.skipped_duplicate
    assert fs["D003.pdf"].publication_id == "D003"  # своя строка publications
    assert fs["D003.pdf"].dup_kind == DupKind.file and fs["D003.pdf"].duplicate_of == fs["D001.pdf"].id
    assert fs["D001.pdf"].dup_kind is None


def test_cli_no_dedupe_disables_sha_marks(env, same_content_dir):
    c = env.new_corpus()
    env.add_dir(same_content_dir, c.id, mode="cli", dedupe=False)
    fs = files_of(env, c.id)
    assert fs["D003.pdf"].dup_kind is None and fs["D003.pdf"].duplicate_of is None


# --- reuse_existing (другой корпус, API) ------------------------------------------------------------------------------------

def test_reuse_existing_links_without_ml(env, pdf_dir):
    a, b = env.new_corpus("A"), env.new_corpus("B")
    env.add_dir(pdf_dir, a.id)
    env.full_run(a.id)
    calls_before = list(env.spy.calls)
    env.add_dir(pdf_dir, b.id, reuse_existing=True)
    fb = files_of(env, b.id)["D001.pdf"]
    fa = files_of(env, a.id)["D001.pdf"]
    assert fb.status == CorpusFileStatus.done and fb.paper_id == fa.paper_id
    assert env.spy.calls == calls_before


def test_reuse_existing_off_creates_new_paper(env, pdf_dir):
    a, b = env.new_corpus("A"), env.new_corpus("B")
    env.add_dir(pdf_dir, a.id)
    env.full_run(a.id)
    env.add_dir(pdf_dir, b.id, reuse_existing=False)
    fb, fa = files_of(env, b.id)["D001.pdf"], files_of(env, a.id)["D001.pdf"]
    assert fb.status != CorpusFileStatus.done and fb.paper_id != fa.paper_id


def test_cli_mode_ignores_reuse_existing(env, pdf_dir):
    a, b = env.new_corpus("A"), env.new_corpus("B")
    env.add_dir(pdf_dir, a.id, mode="cli")
    env.full_run(a.id)
    env.add_dir(pdf_dir, b.id, mode="cli", reuse_existing=True)
    assert files_of(env, b.id)["D001.pdf"].status != CorpusFileStatus.done


# --- смысловые дубли при приёме и --no-dedupe ---------------------------------------------------------------------------------

def test_cli_files_listing_keeps_input_order(env, pdf_dir):
    c = env.new_corpus()
    r = env.add_dir(pdf_dir, c.id, mode="cli")
    assert [f.name for f in r.added] == sorted(f.name for f in r.added)
