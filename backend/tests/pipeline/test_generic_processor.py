"""GenericProcessor: правила проверки ответа модели для rag (validate_tables), проверка цитат, скан-признак."""
from __future__ import annotations

import pytest

from faktura.domain.errors import ExtractError
from faktura.domain.registry import get_domain
from faktura.pdfio.inputs import detect_scan
from faktura.pdfio.quotes import VariantQuoteIndex, rag_norm
from faktura.pipeline.generic import cell, check_quotes, validate_tables

SPEC = get_domain("rag")


def tables(**kw):
    t = {
        "publications": [{"publication_id": "x", "title": "T", "year": 2020, "in_scope": "true"}],
        "configurations": [{"config_id": "C1"}], "datasets": [{"dataset_id": "DS1"}],
        "tasks": [{"task_id": "T1"}], "experiments": [{"experiment_id": "E1"}],
        "results": [{"result_id": "R1", "experiment_id": "E1", "config_id": "C1", "dataset_id": "DS1",
                     "task_id": "T1", "metric_name": "EM", "metric_value": "0.2", "metric_unit": "fraction",
                     "result_origin": "primary", "extraction_status": "extracted", "source_page": 1}],
        "issues": [{"issue_id": "I1", "record_ids": ["R1"], "check_name": "free text"}],
    }
    t.update(kw)
    return t


def run(**kw):
    return validate_tables(SPEC, tables(**kw), "P1")


def test_columns_strings_and_publication_id():
    t, w = run()
    assert list(t["results"][0]) == list(SPEC.tables["results"])
    assert t["results"][0]["source_page"] == "1" and t["issues"][0]["record_ids"] == "R1"
    assert all(r["publication_id"] == "P1" for rows in t.values() for r in rows)
    assert cell(True) == "true" and cell(None) == "" and cell(["a", None, "b"]) == "a;b" and cell(1.5) == "1.5"


def test_closed_enum_cleared_with_warning_and_case_fixed():
    r = tables()["results"][0]
    t, w = run(results=[{**r, "result_origin": "Primary"}, {**r, "result_id": "R2", "extraction_status": "maybe"}])
    assert t["results"][0]["result_origin"] == "primary"
    assert t["results"][1]["extraction_status"] == "" and any("extraction_status" in x and "вне словаря" in x for x in w)


def test_recommended_enum_only_warning():
    r = tables()["results"][0]
    t, w = run(results=[{**r, "metric_direction": "bigger"}])
    assert t["results"][0]["metric_direction"] == "bigger" and any("metric_direction" in x for x in w)


def test_dangling_refs_cleared():
    r = tables()["results"][0]
    t, w = run(results=[{**r, "config_id": "C9", "duplicate_of": "R77"}],
               issues=[{"issue_id": "I1", "record_ids": "R1;R5"}])
    assert t["results"][0]["config_id"] == "" and t["results"][0]["duplicate_of"] == ""
    assert t["issues"][0]["record_ids"] == "R1" and len(w) == 3


def test_duplicate_of_cycle_cleared():
    r = tables()["results"][0]
    t, w = run(results=[{**r, "duplicate_of": "R2"}, {**r, "result_id": "R2", "duplicate_of": "R1"}])
    assert not (t["results"][0]["duplicate_of"] and t["results"][1]["duplicate_of"]) and any("цикл" in x for x in w)


def test_duplicate_key_is_file_error():
    r = tables()["results"][0]
    with pytest.raises(ExtractError) as e:
        run(results=[r, dict(r)])
    assert e.value.code == "ml_invalid_data" and e.value.message.startswith("Модель вернула некорректные данные: ")
    assert "R1" in e.value.message
    with pytest.raises(ExtractError):
        run(configurations=[{"config_id": ""}])


def test_issue_without_id_is_numbered():
    t, _ = run(issues=[{"check_name": "a"}, {"check_name": "b"}, {"issue_id": "I1"}])
    assert sorted(r["issue_id"] for r in t["issues"]) == ["I1", "I2", "I3"]


def test_out_of_scope_keeps_only_publication():
    t, w = run(publications=[{"title": "T", "in_scope": False, "exclusion_reason": "review"}])
    assert t["publications"][0]["in_scope"] == "false" and all(not t[n] for n in t if n != "publications")
    t, w = run(publications=[{"in_scope": "false"}])
    assert t["publications"][0]["exclusion_reason"] and w
    t, w = run(publications=[{"in_scope": "true", "exclusion_reason": "x"}])
    assert t["publications"][0]["exclusion_reason"] == ""
    t, w = run(publications=[{}])
    assert t["publications"][0]["in_scope"] == "true"  # пусто - в области


def test_normalization_never_rescales():
    r = tables()["results"][0]
    t, _ = run(results=[{**r, "metric_value": "", "normalized_value": "0.5", "normalized_unit": "fraction",
                         "metric_unit": ""}])
    x = t["results"][0]
    assert x["metric_value"] == "0.5" and x["metric_unit"] == "fraction" and x["normalized_value"] == "0.5"
    t, _ = run(results=[{**r, "metric_value": "85", "metric_unit": "%"}])
    x = t["results"][0]
    assert x["normalized_value"] == "85" and x["normalized_unit"] == "%"  # проценты не пересчитаны
    t, _ = run(results=[{**r, "metric_value": "85", "metric_unit": ""}])
    assert t["results"][0]["normalized_value"] == ""  # единица неизвестна
    t, _ = run(results=[{**r, "metric_value": "85%", "metric_unit": "%"}])
    assert t["results"][0]["normalized_value"] == ""  # не простое число
    t, w = run(results=[{**r, "metric_value": "1", "normalized_value": "2", "normalized_unit": "fraction"}])
    assert t["results"][0]["metric_value"] == "1" and any("не равно" in x for x in w)


class FakeQuote:
    def check(self, page, frag):
        return {"a": True, "b": False}.get(frag)


def test_check_quotes_maps_ids():
    t, _ = run(results=[{**tables()["results"][0], "source_fragment": "a"}],
               issues=[{"issue_id": "I1", "source_fragment": "b"}, {"issue_id": "I2"}])
    res, iss = check_quotes(FakeQuote(), t)
    assert res == {"R1": True} and iss == {"I1": False, "I2": None}


def test_variant_quote_index(make_pdf_text, tmp_path):
    pdf = make_pdf_text(["Hello, World: EM 0.204 in-\nformation", ""])
    q = VariantQuoteIndex(pdf)
    assert q.check(1, "EM 0.204") is True and q.check("1", "nothing here") is False
    assert q.check(1, "Table 1 EM") is None and q.check(1, "Supplementary x") is None  # не проверяются
    assert q.check(None, "EM 0.204") is None and q.check(9, "EM") is False
    assert q.check(2, "EM 0.204") is None  # у страницы нет текстового слоя
    assert rag_norm("a­b  c-\nd") == "ab cd"


def test_detect_scan(make_pdf_text, make_scan):
    assert detect_scan(make_pdf_text(["some real text on the page"])) is False
    assert detect_scan(make_scan()) is True


@pytest.fixture
def make_pdf_text(tmp_path):
    import fitz

    def _make(pages: list[str], name: str = "t.pdf"):
        doc = fitz.open()
        for text in pages:
            p = doc.new_page()
            if text:
                p.insert_text((72, 100), text.split("\n")[0], fontsize=11)
        path = tmp_path / name
        doc.save(path)
        doc.close()
        return path

    return _make


@pytest.fixture
def make_scan(tmp_path):
    import fitz

    def _make():
        doc = fitz.open()
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 300, 400))
        pix.clear_with(180)
        page.insert_image(page.rect, pixmap=pix)
        path = tmp_path / "scan.pdf"
        doc.save(path)
        doc.close()
        return path

    return _make
