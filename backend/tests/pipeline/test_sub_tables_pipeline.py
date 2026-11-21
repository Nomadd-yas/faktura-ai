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


def test_reprocess_replaces_rows(make_env, pdf_dir, profile):
    env = make_env()
    _, ids = _ids(env, pdf_dir)
    pid = ids["D001.pdf"]
    _run(env, pid, profile)
    _run(env, pid, profile)
    _run(env, pid, profile, mode="renormalize")
    with env.uow() as uow:
        n = uow.conn.execute(S.sub_rows.select().where(S.sub_rows.c.paper_id == pid, S.sub_rows.c.table_name == "results")).fetchall()
        pubs = uow.conn.execute(S.sub_rows.select().where(S.sub_rows.c.paper_id == pid, S.sub_rows.c.table_name == "publications")).fetchall()
    assert len(n) == 47 and len(pubs) == 1


def test_out_of_scope_only_publications_row(make_env, pdf_dir, fake_inner, fake_norm, profile):
    env = make_env(extractor=fake_inner(in_scope=False), normalizer=fake_norm)
    _, ids = _ids(env, pdf_dir)
    pid = ids["D001.pdf"]
    _run(env, pid, profile)
    with env.uow() as uow:
        t = uow.sub.paper_tables(pid)
    assert len(t["publications"]) == 1 and t["publications"][0]["in_scope"] == "false"
    assert all(t[n] == [] for n in t if n != "publications")
