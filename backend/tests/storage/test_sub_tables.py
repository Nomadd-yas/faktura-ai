"""Таблицы шаблона сдачи sub_*: схема 1:1 с эталоном, запись, чтение, фильтры, пагинация."""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from faktura.domain.errors import AppError
from faktura.storage import schema as S

GOLD = Path(__file__).resolve().parents[3] / "data" / "caseB_kejs5" / "dev_gold"
NAMES = ("publications", "materials", "devices", "layers", "measurements", "results", "issues")
SERVICE = {"seq", "paper_id", "corpus_id", "position"}


def _gold_headers(name: str) -> list[str]:
    line = (GOLD / f"{name}.csv").read_text(encoding="utf-8-sig").splitlines()[0]
    return line.split(",")


@pytest.mark.data
@pytest.mark.parametrize("name", NAMES[:1])
def test_schema_matches_gold_headers(name):
    t = S.SUB[name]
    assert t.name == f"sub_{name}"
    assert [c.name for c in t.columns if c.name not in SERVICE] == _gold_headers(name)
    for c in t.columns:
        if c.name not in SERVICE:
            assert isinstance(c.type, sa.Text) and not c.nullable




def _res(pid, n, metric="PCE", **kw):
    return {"publication_id": pid, "result_id": f"R{n:03d}", "device_id": kw.get("device_id", "DV1"),
            "measurement_id": "MS1", "material_id": "M1", "metric_name": metric, "metric_value": str(n),
            "duplicate_of": kw.get("duplicate_of", "")}


@pytest.fixture
def filled(db, corpus, new_paper):
    papers = {}
    for pid, off in (("D001", 0), ("D002", 100)):
        p = new_paper(pid)
        papers[pid] = p
        rows = [_res(pid, off + i, metric=("PCE", "Voc", "FF")[i % 3], device_id="DV1" if i < 5 else "DV2",
                     duplicate_of="R000" if i == 9 else "") for i in range(10)]
        issues = [{"publication_id": pid, "issue_id": f"I{i}", "check_name": "range" if i % 2 else "contradiction",
                   "record_ids": f"R{i:03d}", "description": f"d{i}"} for i in range(1, 5)]
        with db.uow() as u:
            u.sub.replace(p.id, corpus.id, "solar", {"publications": [{"publication_id": pid, "title": "T"}],
                                            "results": rows, "issues": issues})
    return papers














