"""Заполнение sub_* при сохранении статьи: идентичность CSV-экспорту, перезапись, вне области."""
from __future__ import annotations

import pytest

from faktura.export import paper_submission_tables
from faktura.pipeline import PaperProcessor
from faktura.storage import schema as S


def _run(env, paper_id, profile, mode="full"):
    return PaperProcessor(env.services).process(paper_id, profile=profile, mode=mode, model_version="latest")


def _ids(env, make_env_pdf):
    c = env.new_corpus()
    env.add_dir(make_env_pdf, c.id)
    with env.uow() as uow:
        return c, {f.name: f.paper_id for f in uow.files.list(c.id)}


def test_sub_tables_filled_and_identical_to_export(make_env, pdf_dir, profile):
    env = make_env()
    c, ids = _ids(env, pdf_dir)
    pid = ids["D001.pdf"]
    _run(env, pid, profile)
    with env.uow() as uow:
        got = uow.sub.paper_tables(pid)
        bundle = uow.papers.load_bundle(pid, apply_review=True)
        manual = uow.facts.manual_for_paper(pid)
        rows, total, _ = uow.sub.list_results(c.id, limit=1000)
    expected, _ev = paper_submission_tables(bundle, profile, manual_facts=manual)
    assert got == expected
    assert got["publications"][0]["publication_id"] == "D001"
    assert len(got["results"]) == 47 == total == len(rows)




