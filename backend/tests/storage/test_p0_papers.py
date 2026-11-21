"""Этап A: papers (create/get, save_processed, load_bundle, поиск дублей), facts.manual_for_paper."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.entities import DupKind, PaperStats, PaperStatus
from faktura.domain.errors import AppError
from faktura.domain.records import PaperBundle

NOT_FOUND = (AppError, KeyError, LookupError)


# ---- create / get ----------------------------------------------------------------------------











# ---- save_processed --------------------------------------------------------------------------





def test_save_processed_roundtrip_bundle(db, new_paper, process):
    p = new_paper()
    bundle = process(p, n=6).bundle
    with db.uow() as u:
        loaded = u.papers.load_bundle(p.id)
    assert isinstance(loaded, PaperBundle)
    assert loaded == bundle












def test_save_processed_is_atomic_on_failure(db, new_paper, process, make_bundle, make_drafts):
    p = new_paper()
    original = process(p, n=4).bundle
    bad = make_bundle("D001", n=2, base=Decimal("99"))
    with pytest.raises(Exception) as ei:
        with db.uow() as u:
            u.papers.save_processed(p.id, bad, make_drafts(bad), [], [object()], stats=PaperStats(),
                                    model_version="m2", versions={})
    assert ei.type is not NotImplementedError
    with db.uow() as u:
        assert u.papers.load_bundle(p.id) == original
        assert u.papers.get(p.id).model_version == "m1"
















# ---- find_by_doi / find_by_title_key / set_duplicate -----------------------------------------



















# ---- facts.manual_for_paper ------------------------------------------------------------------







