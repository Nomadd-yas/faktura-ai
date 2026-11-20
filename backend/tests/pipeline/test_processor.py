"""PaperProcessor.process и detect_semantic_duplicate (spec/07 §7.4, план 5.6)."""
from __future__ import annotations

import pytest

from faktura.domain.entities import DupKind, PaperStatus
from faktura.domain.errors import AppError, ExtractError
from faktura.domain.records import Publication
from faktura.pipeline import PaperProcessor, detect_semantic_duplicate


@pytest.fixture
def env(make_env):
    return make_env()


def paper_ids(env, corpus_id):
    with env.uow() as uow:
        return {f.name: f.paper_id for f in uow.files.list(corpus_id)}


@pytest.fixture
def loaded(env, pdf_dir):
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    return c, paper_ids(env, c.id)


def process(env, paper_id, profile, mode="full", model="latest"):
    return PaperProcessor(env.services).process(paper_id, profile=profile, mode=mode, model_version=model)


# --- full ---------------------------------------------------------------------------------------------------

def test_full_process_done_and_saved(env, loaded, profile):
    _, pids = loaded
    out = process(env, pids["D001.pdf"], profile)
    assert out.paper_id == pids["D001.pdf"] and out.status == PaperStatus.done
    with env.uow() as uow:
        b = uow.papers.load_bundle(pids["D001.pdf"])
        p = uow.papers.get(pids["D001.pdf"])
    assert b.publication.publication_id == "D001"
    assert len(b.results) == 47  # строки results эталона mini_gold для D001
    assert p.status == PaperStatus.done and p.processed_at is not None
    assert p.model_version and p.model_version.startswith("mock")


def test_stats_filled(env, loaded, profile):
    _, pids = loaded
    out = process(env, pids["D001.pdf"], profile)
    assert out.stats.facts_count == 47
    assert out.stats.coverage.total >= out.stats.coverage.filled > 0
    assert out.stats.quality.level in ("good", "doubt", "bad")


def test_facts_one_per_result_row(env, loaded, profile):
    from faktura.domain.entities import FactFilter
    _, pids = loaded
    process(env, pids["D001.pdf"], profile)
    with env.uow() as uow:
        page = uow.facts.query(FactFilter(paper_ids=[pids["D001.pdf"]]), [], 1000, None)
    assert len([r for r in page.data if r.fact.target["entity"] == "results"]) == 47


def test_quote_found_filled(env, loaded, profile):
    from faktura.domain.entities import FactFilter
    _, pids = loaded
    process(env, pids["D001.pdf"], profile)
    with env.uow() as uow:
        rows = uow.facts.query(FactFilter(paper_ids=[pids["D001.pdf"]]), [], 1000, None).data
    # PDF синтетический: цитаты эталона в нём не встречаются, но значение должно быть выставлено (False), не None
    assert all(r.fact.quote_found is not None for r in rows if r.fact.source_fragment)


def test_unknown_paper_not_found(env, profile):
    with pytest.raises(AppError) as ei:
        process(env, "pap_nope", profile)
    assert ei.value.code == "not_found"


def test_unknown_publication_in_mock_gives_publication_only(env, make_pdf, tmp_path, profile):
    d = tmp_path / "unk"
    make_pdf(["x"], name="D999.pdf", directory=d)
    c = env.new_corpus()
    env.add_dir(d, c.id)
    pid = paper_ids(env, c.id)["D999.pdf"]
    out = process(env, pid, profile)
    assert out.status == PaperStatus.done
    with env.uow() as uow:
        b = uow.papers.load_bundle(pid)
    assert b.publication.publication_id == "D999" and b.results == []


# --- scope: in_scope=false -> только publications --------------------------------------------------------------------

def test_out_of_scope_keeps_only_publication(make_env, pdf_dir, fake_inner, fake_norm, profile):
    env = make_env(extractor=fake_inner(in_scope=False), normalizer=fake_norm)
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    pid = paper_ids(env, c.id)["D001.pdf"]
    process(env, pid, profile)
    with env.uow() as uow:
        b = uow.papers.load_bundle(pid)
        p = uow.papers.get(pid)
    assert b.publication.in_scope is False or str(b.publication.in_scope).lower() == "false"
    assert b.results == b.devices == b.layers == b.materials == b.measurements == []
    assert p.in_scope is False


# --- нормализация применяется только через services.normalizer ----------------------------------------------------------

def test_normalizer_from_services_is_used(make_env, pdf_dir, fake_inner, fake_norm, profile):
    env = make_env(extractor=fake_inner(), normalizer=fake_norm)
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    pid = paper_ids(env, c.id)["D001.pdf"]
    process(env, pid, profile)
    assert fake_norm.called("normalize")
    with env.uow() as uow:
        b = uow.papers.load_bundle(pid)
    r1 = next(r for r in b.results if r.result_id == "R1")
    assert r1.metric_name == "Voc" and str(r1.metric_value) in ("1.197",)


# --- ошибки ML ---------------------------------------------------------------------------------------------------------------

def test_ml_unavailable_reported_or_raised(env, loaded, profile):
    _, pids = loaded
    env.spy.fail_on["D001"] = "ml_unavailable"
    try:
        out = process(env, pids["D001.pdf"], profile)
    except ExtractError as e:
        assert e.code == "ml_unavailable" and e.retryable
    else:
        assert out.status == PaperStatus.failed and out.error_code == "ml_unavailable"


def test_schema_mismatch_marks_failed_with_code(env, loaded, profile):
    _, pids = loaded
    env.spy.fail_on["D001"] = "ml_schema_mismatch"
    try:
        out = process(env, pids["D001.pdf"], profile)
    except ExtractError as e:
        assert e.code == "ml_schema_mismatch"
        return
    assert out.status == PaperStatus.failed and out.error_code == "ml_schema_mismatch"
    with env.uow() as uow:
        assert uow.papers.get(pids["D001.pdf"]).status == PaperStatus.failed


def test_failure_does_not_wipe_previous_result(env, loaded, profile):
    _, pids = loaded
    process(env, pids["D001.pdf"], profile)
    env.spy.fail_on["D001"] = "ml_error"
    try:
        process(env, pids["D001.pdf"], profile)
    except ExtractError:
        pass
    with env.uow() as uow:
        assert len(uow.papers.load_bundle(pids["D001.pdf"]).results) == 47  # одна транзакция: либо всё, либо ничего


# --- renormalize / recheck не вызывают ML -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["renormalize", "recheck"])
def test_modes_do_not_call_extractor(env, loaded, profile, mode):
    _, pids = loaded
    process(env, pids["D001.pdf"], profile)
    n = len(env.spy.calls)
    out = process(env, pids["D001.pdf"], profile, mode=mode)
    assert out.status == PaperStatus.done and len(env.spy.calls) == n


def test_renormalize_recomputes_from_raw(make_env, pdf_dir, fake_inner, fake_norm, profile):
    env = make_env(extractor=fake_inner(), normalizer=fake_norm)
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    pid = paper_ids(env, c.id)["D001.pdf"]
    process(env, pid, profile)
    with env.uow() as uow:
        before = {r.result_id: r.normalized_value for r in uow.papers.load_bundle(pid).results}
    process(env, pid, profile, mode="renormalize")
    with env.uow() as uow:
        after = {r.result_id: r.normalized_value for r in uow.papers.load_bundle(pid).results}
    assert before == after and before


def test_renormalize_never_processed_paper_has_nothing_to_do(env, loaded, profile):
    _, pids = loaded
    try:
        out = process(env, pids["D001.pdf"], profile, mode="renormalize")
    except AppError as e:
        assert e.code in ("conflict", "not_found", "validation_error")
    else:
        assert out.status == PaperStatus.failed
    assert env.spy.calls == []


# --- кеш ML через http (REQ-066) ------------------------------------------------------------------------------------------------------

@pytest.fixture
def http_env(make_env, settings, http_ml, tmp_path):
    settings.extractor = "http"
    settings.extract_url = "http://ml.test"
    settings.cache_dir = tmp_path / "cache"
    settings.extract_retry_delays = "0"
    return make_env(real_extractor=True)


def test_http_second_process_uses_cache(http_env, http_ml, pdf_dir, profile):
    env = http_env
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    pid = paper_ids(env, c.id)["D001.pdf"]
    out1 = process(env, pid, profile, model="spy-1")
    assert len(http_ml.extract_calls) == 1 and not out1.from_cache
    out2 = process(env, pid, profile, model="spy-1")
    assert len(http_ml.extract_calls) == 1 and out2.from_cache


def test_http_other_model_version_bypasses_cache(http_env, http_ml, pdf_dir, profile):
    env = http_env
    c = env.new_corpus()
    env.add_dir(pdf_dir, c.id)
    pid = paper_ids(env, c.id)["D001.pdf"]
    process(env, pid, profile, model="spy-1")
    process(env, pid, profile, model="spy-2")
    assert len(http_ml.extract_calls) == 2


def test_http_identical_content_two_papers_one_ml_call(http_env, http_ml, make_pdf, tmp_path, profile):
    import shutil
    env = http_env
    d = tmp_path / "same"
    src = make_pdf(["same"], name="D001.pdf", directory=d)
    shutil.copy(src, d / "D003.pdf")
    c = env.new_corpus()
    env.add_dir(d, c.id, mode="cli")
    ids = paper_ids(env, c.id)
    process(env, ids["D001.pdf"], profile, model="spy-1")
    out = process(env, ids["D003.pdf"], profile, model="spy-1")
    assert len(http_ml.extract_calls) == 1 and out.from_cache
    with env.uow() as uow:
        assert uow.papers.load_bundle(ids["D003.pdf"]).publication.publication_id == "D003"


# --- смысловые дубли ------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def two_papers(make_env, pdf_dir, fake_inner, fake_norm, profile):
    """Фабрика: две статьи D001/D003 с заданными DOI/названием/годом; первая обработана."""
    def _make(**kw):
        env = make_env(extractor=fake_inner(**kw), normalizer=fake_norm)
        c = env.new_corpus()
        env.add_dir(pdf_dir, c.id)
        ids = paper_ids(env, c.id)
        process(env, ids["D001.pdf"], profile)
        return env, ids
    return _make


def test_dup_by_doi(two_papers, profile):
    env, ids = two_papers(doi="10.1000/dup", title=lambda p: f"Title {p}")
    process(env, ids["D003.pdf"], profile)
    with env.uow() as uow:
        p = uow.papers.get(ids["D003.pdf"])
        first = uow.papers.get(ids["D001.pdf"])
    assert p.dup_of == ids["D001.pdf"] and p.dup_kind == DupKind.doi
    assert first.dup_of is None  # у первой пометки нет, данные не сливаются


def test_dup_doi_is_case_and_prefix_insensitive(two_papers):
    env, ids = two_papers(doi="10.1000/ABC")
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        dup, kind = detect_semantic_duplicate(uow, ws, ids["D003.pdf"],
                                              Publication(publication_id="D003", DOI="https://doi.org/10.1000/abc "))
    assert (dup, kind) == (ids["D001.pdf"], DupKind.doi)


def test_dup_by_title_key_without_doi(two_papers, profile):
    env, ids = two_papers(doi=None, title="Same Title For Both", year=2022)
    process(env, ids["D003.pdf"], profile)
    with env.uow() as uow:
        p = uow.papers.get(ids["D003.pdf"])
    assert p.dup_of == ids["D001.pdf"] and p.dup_kind == DupKind.title


def test_doi_beats_title(two_papers):
    env, ids = two_papers(doi="10.5/x", title="Same", year=2022)
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        assert detect_semantic_duplicate(uow, ws, ids["D003.pdf"], Publication(
            publication_id="D003", DOI="10.5/x", title="Same", year=2022, authors=["Ivanov A."]))[1] == DupKind.doi


def test_different_year_is_not_title_dup(two_papers):
    env, ids = two_papers(doi=None, title="Same", year=2022)
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        assert detect_semantic_duplicate(uow, ws, ids["D003.pdf"], Publication(
            publication_id="D003", title="Same", year=2019, authors=["Ivanov A."])) == (None, None)


def test_title_key_ignores_case_and_punctuation(two_papers):
    env, ids = two_papers(doi=None, title="Perovskite: Solar-Cells!", year=2022)
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        assert detect_semantic_duplicate(uow, ws, ids["D003.pdf"], Publication(
            publication_id="D003", title="perovskite solar cells", year=2022,
            authors=["Ivanov A."])) == (ids["D001.pdf"], DupKind.title)


def test_no_doi_no_title_no_dup(two_papers):
    env, ids = two_papers(doi="10.1/a")
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        assert detect_semantic_duplicate(uow, ws, ids["D003.pdf"],
                                         Publication(publication_id="D003")) == (None, None)


def test_never_matches_itself(two_papers):
    env, ids = two_papers(doi="10.1/self")
    with env.uow() as uow:
        ws = uow.papers.get(ids["D001.pdf"]).workspace_id
        assert detect_semantic_duplicate(uow, ws, ids["D001.pdf"],
                                         Publication(publication_id="D001", DOI="10.1/self")) == (None, None)


def test_other_workspace_not_matched(two_papers):
    env, ids = two_papers(doi="10.1/ws")
    with env.uow() as uow:
        assert detect_semantic_duplicate(uow, "ws_other", ids["D003.pdf"],
                                         Publication(publication_id="D003", DOI="10.1/ws")) == (None, None)


def test_first_of_three_is_reference(make_env, make_pdf, tmp_path, fake_inner, fake_norm, profile):
    env = make_env(extractor=fake_inner(doi="10.1/three"), normalizer=fake_norm)
    d = tmp_path / "three"
    for n in ("D001", "D002", "D003"):
        make_pdf([f"text {n}"], name=f"{n}.pdf", directory=d)
    c = env.new_corpus()
    env.add_dir(d, c.id)
    ids = paper_ids(env, c.id)
    for n in ("D001", "D002", "D003"):
        process(env, ids[f"{n}.pdf"], profile)
    with env.uow() as uow:
        assert uow.papers.get(ids["D002.pdf"]).dup_of == ids["D001.pdf"]
        assert uow.papers.get(ids["D003.pdf"]).dup_of == ids["D001.pdf"]
