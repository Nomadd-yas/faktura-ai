"""assign_keys (К-6), link_duplicates (REQ-032), comparability_key (spec/05 §5.3, REQ-049/059)."""
from __future__ import annotations

import pytest

from faktura.checks import DuplicateLink, assign_keys, comparability_key, link_duplicates


# --- assign_keys -------------------------------------------------------------------------------------














# --- link_duplicates ---------------------------------------------------------------------------------
def test_duplicate_same_value_other_place_is_linked_to_earlier(mk):
    rs = [mk.result("R001", "PCE", "15", source_table="Table 1"),
          mk.result("R002", "PCE", "15", source_figure="Fig. 2")]
    links = link_duplicates(mk.bundle(rs))
    assert links == [DuplicateLink(result_id="R002", duplicate_of="R001", note="связано сервисом разбора")]












# --- comparability_key -------------------------------------------------------------------------------
def _cmp(mk, *, area=0.5, agg="champion", scan="reverse", certified=False, protocol=None, spectrum="AM1.5G",
         intensity=100, dtype="cell", mclass="perovskite"):
    r = mk.result("R001", "PCE", 15, agg=agg)
    ms = mk.measurement(spectrum=spectrum, intensity=intensity, scan=scan, certified=certified,
                        stability_protocol=protocol)
    b = mk.bundle([r], devices=[mk.device(area=area, dtype=dtype)], materials=[mk.material(mclass=mclass)],
                  measurements=[ms])
    return comparability_key(b.results[0], b)














