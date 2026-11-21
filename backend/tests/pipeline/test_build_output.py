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
















# --- _meta ------------------------------------------------------------------------------------------------------------











# --- упавшие файлы: failed_stub (REQ-024) ---------------------------------------------------------------------------------------







# --- правки рецензента попадают в сдачу ----------------------------------------------------------------------------------------------------

def _result_fact(env, paper_id, rid):
    with env.uow() as uow:
        return next(r.fact for r in uow.facts.query(FactFilter(paper_ids=[paper_id]), [], 1000, None).data
                    if r.fact.target.get("id") == rid)


def _paper(env, corpus, name="D001.pdf"):
    with env.uow() as uow:
        return next(f.paper_id for f in uow.files.list(corpus.id) if f.name == name)








# --- проверка сдачи ----------------------------------------------------------------------------------------------------------------------------------





def test_script_errors_hint_3(env, done_corpus, profile, tmp_path, fake_validator):
    res = build(env, done_corpus[0], tmp_path / "out", profile, check_script=fake_validator("errors"))
    assert res.check.ok is False and res.check.errors and res.exit_code_hint == 3
    assert sorted(p.name for p in (tmp_path / "out").glob("*.csv")) == TABLE_FILES  # набор всё равно записан














# --- (data) реальный валидатор на эталоне ------------------------------------------------------------------------------------------------------------

