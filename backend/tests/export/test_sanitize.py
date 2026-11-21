"""sanitize и number_issues (REQ-018, REQ-020, REQ-037, К-3): последний слой перед записью CSV."""
from __future__ import annotations

from decimal import Decimal

import pytest

from faktura.domain.io import tables_from_bundles
from faktura.domain.records import Device, IssueRecord, Layer, Material, Result
from faktura.domain.tables import ID_COLUMN
from faktura.export import SanitizeResult, number_issues, sanitize, strict_validate


def _errors(bundles):
    return [p for p in strict_validate(tables_from_bundles(bundles)) if p.level == "error"]


def _note(row) -> str:
    return row.note or ""


def test_clean_bundle_unchanged_no_events(make_bundle):
    b = make_bundle()
    before = b.model_dump()
    res = sanitize([b])
    assert isinstance(res, SanitizeResult)
    assert res.events == []
    assert res.bundles[0].model_dump() == before


def test_empty_input():
    res = sanitize([])
    assert res.bundles == [] and res.events == []


def test_does_not_mutate_input(make_bundle):
    b = make_bundle()
    b.results[0].metric_value = "1,5"
    b.materials[0].material_class = "weird"
    before = b.model_dump()
    sanitize([b])
    assert b.model_dump() == before


def test_idempotent(make_bundle):
    b = make_bundle()
    b.results[0].metric_value = "~1.9"
    b.results[1].result_id = "R001"
    b.materials[0].material_class = "weird"
    once = sanitize([b])
    twice = sanitize(once.bundles)
    assert twice.events == []
    assert twice.bundles[0].model_dump() == once.bundles[0].model_dump()


def test_same_ids_in_different_papers_are_not_renamed(make_bundle):
    res = sanitize([make_bundle("P001"), make_bundle("P002")])
    assert [r.result_id for r in res.bundles[0].results] == ["R001", "R002"]
    assert [r.result_id for r in res.bundles[1].results] == ["R001", "R002"]
    assert res.events == []


# --- словари ------------------------------------------------------------------------------------

VOCAB_CASES = [
    ("materials", "material_class", "Perovskite-like"), ("materials", "dimensionality", "4D"),
    ("materials", "bandgap_method", "DFT"), ("devices", "architecture", "p-i-p"),
    ("devices", "device_role", "main"), ("devices", "device_type", "stack"),
    ("devices", "substrate_type", "glass"), ("devices", "area_definition", "geometric"),
    ("layers", "layer_function", "buffer"), ("measurements", "measurement_type", "IV"),
    ("measurements", "scan_direction", "both"), ("measurements", "stability_protocol", "ISOS-X"),
    ("results", "aggregation", "average"), ("results", "result_origin", "own"),
    ("results", "extraction_status", "maybe"),
]


@pytest.mark.parametrize("table,col,bad", VOCAB_CASES)
def test_value_outside_vocab_cleared_and_kept_in_note(make_bundle, table, col, bad):
    b = make_bundle()
    setattr(getattr(b, table)[0], col, bad)
    res = sanitize([b])
    out = getattr(res.bundles[0], table)[0]
    assert getattr(out, col) is None
    assert bad in _note(out)
    assert res.events  # REQ-018: каждое изменение в логе
    assert _errors(res.bundles) == []


def test_vocab_value_valid_is_kept(make_bundle):
    out = sanitize([make_bundle()]).bundles[0]
    assert out.materials[0].material_class == "perovskite"
    assert out.results[0].aggregation == "champion"


def test_existing_note_is_preserved_when_appending(make_bundle):
    b = make_bundle()
    b.materials[0].note = "исходное примечание"
    b.materials[0].material_class = "weird"
    out = sanitize([b]).bundles[0].materials[0]
    assert "исходное примечание" in _note(out) and "weird" in _note(out)


@pytest.mark.parametrize("bad", ["encapsulated glass", "maybe", "yes?"])
def test_bad_boolean_not_left_as_text(make_bundle, bad):
    b = make_bundle()
    # devices.encapsulation - свободный текст (README шаблона, эталон: «unencapsulated»), не логическое поле
    b.measurements[0].mask_used = bad
    out = sanitize([b]).bundles[0]
    assert not isinstance(out.measurements[0].mask_used, str)


# --- числа --------------------------------------------------------------------------------------

NUM_CASES = [
    ("1,5", Decimal("1.5")), ("~1.9", Decimal("1.9")), ("18.2-19.0", None), ("15.28 mA", Decimal("15.28")),
    ("abc", None), ("1e", None), ("63–68", None), ("n/a", None),
]


@pytest.mark.parametrize("raw,expected", NUM_CASES)
@pytest.mark.parametrize("table,col", [("results", "metric_value"), ("results", "normalized_value"),
                                       ("layers", "layer_thickness"), ("devices", "active_area"),
                                       ("materials", "bandgap_value")])
def test_non_numeric_converted_or_cleared(make_bundle, table, col, raw, expected):
    b = make_bundle()
    setattr(getattr(b, table)[0], col, raw)
    res = sanitize([b])
    v = getattr(getattr(res.bundles[0], table)[0], col)
    if (table, col) == ("layers", "layer_thickness") and raw in ("18.2-19.0", "63–68"):
        assert v == raw  # диапазон толщины остаётся текстом, как в эталоне («63–68»)
        return
    assert not isinstance(v, str)
    assert v is None or v == expected
    assert res.events
    assert _errors(res.bundles) == []


def test_conversion_keeps_original_in_note(make_bundle):
    b = make_bundle()
    b.results[0].metric_value = "18.2-19.0"
    out = sanitize([b]).bundles[0].results[0]
    assert out.metric_value is None
    assert "18.2-19.0" in _note(out)


def test_integer_fields_str_to_int_or_none(make_bundle):
    b = make_bundle()
    b.results[0].source_page = "5"
    b.layers[0].layer_order = "two"
    b.results[1].n_devices = "many"
    out = sanitize([b]).bundles[0]
    assert out.results[0].source_page == 5
    assert out.layers[0].layer_order is None
    assert out.results[1].n_devices is None


def test_year_string_normalised(make_bundle):
    b = make_bundle()
    b.publication.year = "2021"
    assert sanitize([b]).bundles[0].publication.year == 2021
    b.publication.year = "circa 2020s"
    assert sanitize([b]).bundles[0].publication.year is None


# --- единица только при значении (REQ-037) -----------------------------------------------------

@pytest.mark.parametrize("table,val,unit", [("layers", "layer_thickness", "thickness_unit"),
                                            ("materials", "bandgap_value", "bandgap_unit"),
                                            ("devices", "active_area", "area_unit")])
def test_unit_without_value_kept(make_bundle, table, val, unit):
    # эталон оставляет «eV» у пустой ширины зоны; README шаблона правила об очистке не содержит
    b = make_bundle()
    row = getattr(b, table)[0]
    setattr(row, val, None)
    assert getattr(row, unit)
    res = sanitize([b])
    assert getattr(getattr(res.bundles[0], table)[0], unit) == getattr(row, unit)


def test_thickness_range_text_kept_with_unit(make_bundle):
    b = make_bundle()
    b.layers[0].layer_thickness = "63–68"
    out = sanitize([b]).bundles[0].layers[0]
    assert out.layer_thickness == "63–68" and out.thickness_unit == "nm"


def test_unit_kept_with_value(make_bundle):
    out = sanitize([make_bundle()]).bundles[0].layers[0]
    assert out.layer_thickness == Decimal("400") and out.thickness_unit == "nm"


# --- висячие ссылки ----------------------------------------------------------------------------

def test_dangling_device_absorber_material(make_bundle):
    b = make_bundle()
    b.devices[0].absorber_material_id = "M9"
    res = sanitize([b])
    assert res.bundles[0].devices[0].absorber_material_id is None
    assert any("M9" in e for e in res.events)


def test_dangling_layer_device(make_bundle):
    b = make_bundle()
    b.layers[0].device_id = "DV9"
    res = sanitize([b])
    assert res.bundles[0].layers[0].device_id is None and any("DV9" in e for e in res.events)


def test_dangling_measurement_devices_partially_removed(make_bundle):
    b = make_bundle()
    b.measurements[0].device_id = ["DV1", "DV9"]
    assert sanitize([b]).bundles[0].measurements[0].device_id == ["DV1"]


def test_dangling_result_refs(make_bundle):
    b = make_bundle()
    b.results[0].device_id = "DV9"
    b.results[0].measurement_id = "MS9"
    b.results[0].material_id = "M9"
    res = sanitize([b])
    r = res.bundles[0].results[0]
    assert (r.device_id, r.measurement_id, r.material_id) == (None, None, None)
    assert len([e for e in res.events if any(x in e for x in ("DV9", "MS9", "M9"))]) >= 3
    assert _errors(res.bundles) == []


def test_dangling_duplicate_of(make_bundle):
    b = make_bundle()
    b.results[1].duplicate_of = "R777"
    res = sanitize([b])
    assert res.bundles[0].results[1].duplicate_of is None and any("R777" in e for e in res.events)


def test_dangling_issue_record_ids_removed_keeping_valid(make_bundle):
    b = make_bundle()
    b.issues[0].record_ids = ["R001", "R555"]
    res = sanitize([b])
    assert res.bundles[0].issues[0].record_ids == ["R001"]
    assert any("R555" in e for e in res.events)


def test_issue_with_all_records_dangling_is_kept_with_empty_ids(make_bundle):
    b = make_bundle()
    b.issues[0].record_ids = ["R555"]
    res = sanitize([b])
    assert len(res.bundles[0].issues) == 1 and res.bundles[0].issues[0].record_ids == []


# --- повтор id ----------------------------------------------------------------------------------

def test_duplicate_result_id_renamed_with_cascade(make_bundle):
    b = make_bundle()
    b.results.append(b.results[0].model_copy(deep=True, update={"source_page": 9, "duplicate_of": None}))
    res = sanitize([b])
    ids = [r.result_id for r in res.bundles[0].results]
    assert len(set(ids)) == 3 and ids[0] == "R001"
    assert res.events
    assert _errors(res.bundles) == []  # ссылки duplicate_of и record_ids консистентны


@pytest.mark.parametrize("table", ["materials", "devices", "layers", "measurements"])
def test_duplicate_ids_in_dictionary_tables_renamed(make_bundle, table):
    b = make_bundle()
    rows = getattr(b, table)
    rows.append(rows[0].model_copy(deep=True))
    col = ID_COLUMN[table]
    first = getattr(rows[0], col)
    res = sanitize([b])
    ids = [getattr(r, col) for r in getattr(res.bundles[0], table)]
    assert len(ids) == len(set(ids)) == 2
    assert ids[0] == first
    assert res.events
    assert _errors(res.bundles) == []


def test_duplicate_material_id_references_follow_rename(make_bundle):
    b = make_bundle()
    b.materials.append(Material(publication_id="P001", material_id="M1", material_name_raw="second"))
    b.devices.append(Device(publication_id="P001", device_id="DV2", absorber_material_id="M1"))
    res = sanitize([b])
    mats = {m.material_id for m in res.bundles[0].materials}
    assert len(mats) == 2
    assert all(d.absorber_material_id in mats for d in res.bundles[0].devices)


# --- замечания ---------------------------------------------------------------------------------

def test_issues_with_empty_page_and_fragment_are_unchanged(make_bundle):
    b = make_bundle()
    b.issues.append(IssueRecord(publication_id="P001", issue_id="I2", record_ids=[], check_name="contradiction",
                                description="без источника", source_page=None, source_fragment=None))
    res = sanitize([b])
    i2 = res.bundles[0].issues[1]
    assert i2.source_page is None and i2.source_fragment is None
    assert i2.description == "без источника"
    assert res.events == []


def test_issue_check_name_outside_catalog_kept(make_bundle):
    b = make_bundle()
    b.issues[0].check_name = "my_custom_rule"
    assert sanitize([b]).bundles[0].issues[0].check_name == "my_custom_rule"


# --- fuzz (приёмка) ----------------------------------------------------------------------------

def _nasty(make_bundle):
    b = make_bundle()
    b.materials[0].material_class = "Perovskite?"
    b.materials[0].bandgap_value = "1,5"
    b.devices[0].active_area = "~0.06"
    b.devices[0].absorber_material_id = "MX"
    b.layers[0].layer_thickness = None
    b.layers.append(Layer(publication_id="P001", layer_id="DV1-L1", device_id="DVX", layer_order="2",
                          layer_function="?", layer_thickness="63–68", thickness_unit="nm"))
    b.measurements[0].device_id = ["DV1", "DVY"]
    b.measurements[0].scan_direction = "both"
    b.results[0].metric_value = "18.2-19.0"
    b.results[0].normalized_value = "15.28 mA"
    b.results[1].result_id = "R001"
    b.results[1].duplicate_of = "R001"
    b.results.append(Result(publication_id="P001", result_id="R003", device_id="ZZ", metric_name="Voc",
                            metric_value="1,5", aggregation="avg", source_page="p.5", duplicate_of="R404"))
    b.issues[0].record_ids = ["R001", "R404", "R003"]
    return b


def test_fuzz_sanitized_passes_strict_validator(make_bundle):
    res = sanitize([_nasty(make_bundle)])
    assert _errors(res.bundles) == []
    assert len(res.events) >= 8


def test_fuzz_passes_check_submission(make_bundle, tmp_path, check_script):
    from faktura.export import run_check_script, write_submission

    res = sanitize([_nasty(make_bundle)])
    out = tmp_path / "out"
    write_submission(res.bundles, out)
    rep = run_check_script(check_script, out, tmp_path)
    assert rep.crashed is False and rep.errors == [] and rep.ok is True


# --- number_issues -----------------------------------------------------------------------------

def _iss(iid, page, order):
    r = IssueRecord(publication_id="P001", issue_id=iid, record_ids=[], check_name="range", description=iid,
                    source_page=page, source_fragment="f" if page else None)
    r.meta.ml_order = order
    return r


def test_number_issues_renumbers_all_by_page(make_bundle):
    b = make_bundle()
    b.issues = [_iss("I7", None, 0), _iss("I-range-R001", 5, 1), _iss("I5", 2, 2), _iss("", 5, 3)]
    out = number_issues(b)
    assert [i.issue_id for i in out.issues] == ["I1", "I2", "I3", "I4"]
    assert [i.source_page for i in out.issues] == [2, 5, 5, None]  # по номеру страницы, пустая - последней
    assert [i.description for i in out.issues] == ["I5", "I-range-R001", "", "I7"]  # внутри страницы - исходный порядок
    assert [i.meta.ml_order for i in out.issues] == [0, 1, 2, 3]


def test_number_issues_page_is_numeric_not_text(make_bundle):
    b = make_bundle()
    b.issues = [_iss("a", 10, 0), _iss("b", "9", 1), _iss("c", "", 2)]
    assert [i.description for i in number_issues(b).issues] == ["b", "a", "c"]


def test_number_issues_empty(make_bundle):
    b = make_bundle()
    b.issues = []
    assert number_issues(b).issues == []


def test_number_issues_single_gets_i1(make_bundle):
    assert [i.issue_id for i in number_issues(make_bundle()).issues] == ["I1"]


def test_number_issues_is_stable_on_repeat(make_bundle):
    b = make_bundle()
    b.issues = [_iss("I1", 3, 1), _iss("", 3, 0)]
    once = number_issues(b)
    twice = number_issues(once)
    assert [(i.issue_id, i.description) for i in once.issues] == [(i.issue_id, i.description) for i in twice.issues]


def test_number_issues_preserves_other_tables(make_bundle):
    out = number_issues(make_bundle())
    assert [r.result_id for r in out.results] == ["R001", "R002"]
    assert out.publication.publication_id == "P001"
