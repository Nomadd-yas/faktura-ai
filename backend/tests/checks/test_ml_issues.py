"""check_ml_issues: висячие record_ids и ненайденная цитата -> пометки, замечания не удаляются (REQ-055)."""
from __future__ import annotations

import copy

from faktura.checks import check_ml_issues


def _b(mk):
    return mk.bundle([mk.result("R001", "PCE", 15), mk.result("R002", "Voc", 1)])




def test_dangling_record_id_is_marked_not_removed(mk):
    out = check_ml_issues([mk.issue("contradiction", ["R001", "R999"])], _b(mk), None)
    assert len(out) == 1
    assert out[0].meta.notes_backend and any("R999" in n for n in out[0].meta.notes_backend)
    assert out[0].record_ids == ["R001", "R999"]  # ссылки не стираются




















