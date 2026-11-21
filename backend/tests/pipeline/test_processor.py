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












# --- scope: in_scope=false -> только publications --------------------------------------------------------------------



# --- нормализация применяется только через services.normalizer ----------------------------------------------------------



# --- ошибки ML ---------------------------------------------------------------------------------------------------------------







# --- renormalize / recheck не вызывают ML -----------------------------------------------------------------------------------------







# --- кеш ML через http (REQ-066) ------------------------------------------------------------------------------------------------------

@pytest.fixture
def http_env(make_env, settings, http_ml, tmp_path):
    settings.extractor = "http"
    settings.extract_url = "http://ml.test"
    settings.cache_dir = tmp_path / "cache"
    settings.extract_retry_delays = "0"
    return make_env(real_extractor=True)








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




















