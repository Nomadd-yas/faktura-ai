"""Дополнительные проверки adapters: поведение, которое не покрыто приёмкой плана, но на него опирается конвейер."""
from __future__ import annotations

import copy
import zipfile

import httpx
import pytest

from faktura.domain.errors import ExtractError
from faktura.ml import CachedExtractor, HttpExtractor, MlCache, RawDirExtractor, parse_extract_response, resolve_model_version
from faktura.ml.adapters import _parse_retry_after
from faktura.pdfio import ZipLimits, collect_inputs, dedupe_names, title_key
from faktura.pdfio.inputs import InputFile


# ---------------- pdfio ----------------
def test_collect_zip_reports_non_pdf_and_bad_signature(make_zip, pdf_bytes):
    z = make_zip({"a.pdf": pdf_bytes, "notes.docx": b"PK..", "fake.pdf": b"plain text"})
    r = collect_inputs(z)
    assert [f.name for f in r.files] == ["a.pdf"]
    assert sorted((x.name, x.code) for x in r.rejected) == [("fake.pdf", "unsupported_file"),
                                                           ("notes.docx", "unsupported_file")]


def test_collect_zip_bomb_rejects_whole_archive(make_zip, pdf_bytes, tmp_path):
    z = make_zip({"a.pdf": pdf_bytes})
    r = collect_inputs(z, limits=ZipLimits(max_unpacked_gb=0), work_dir=tmp_path / "w")
    assert r.files == [] and [x.code for x in r.rejected] == ["zip_bomb"]


def test_collect_zip_cp866_names(tmp_path, pdf_bytes):
    class LegacyInfo(zipfile.ZipInfo):  # имя в cp866 без флага UTF-8, как у архиваторов Windows
        def _encodeFilenameFlags(self):
            return self.filename.encode("cp437"), self.flag_bits

    path = tmp_path / "ru.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(LegacyInfo("Статья.pdf".encode("cp866").decode("cp437")), pdf_bytes)
    r = collect_inputs(path)
    assert [f.name for f in r.files] == ["Статья.pdf"]


def test_collect_dir_skips_hidden_and_macosx(tmp_path, make_pdf):
    root = tmp_path / "in"
    make_pdf(["a"], name="A.pdf", directory=root)
    make_pdf(["b"], name="B.pdf", directory=root / "__MACOSX")
    (root / ".DS_Store").write_bytes(b"x")
    r = collect_inputs(root)
    assert [f.name for f in r.files] == ["A.pdf"] and r.rejected == []


def test_dedupe_is_case_insensitive(tmp_path):
    files = [InputFile(name=n, rel_path=n, publication_id=n[:-4], path=tmp_path / n) for n in ("P1.pdf", "p1.PDF")]
    r = dedupe_names(files, existing_names=set())
    assert [f.name for f in r.files] == ["P1.pdf"] and r.rejected[0].code == "duplicate_name"


def test_title_key_author_order_and_diacritics():
    assert title_key("Café solar", 2020, "H.W. Qiao") == title_key("Cafe Solar", 2020, "Qiao, H.W.")
    assert title_key("T", "2020", "Ivanov A.") == title_key("T", 2020, "A. Ivanov")
    assert title_key("T", "not-a-year", "Ivanov") is None


# ---------------- ml: разбор ----------------
def test_metric_value_only_moves_to_raw_value(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    r0 = data["results"][0]
    r0.pop("raw_value")
    r0.pop("raw_unit")
    r0.update(metric_value=3.85, metric_unit="%")
    r = parse_extract_response(data, "D021").bundle.by_id("results", "R002")
    assert r.raw_value == "3.85" and r.raw_unit == "%" and r.metric_value is None
    assert any("metric_value" in n for n in r.meta.notes_backend)


def test_text_in_numeric_field_kept_with_note(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["layers"][0]["layer_thickness"] = "63-68"
    lay = parse_extract_response(data, "D021").bundle.layers[0]
    assert lay.layer_thickness == "63-68" and lay.meta.notes_backend


def test_numbers_as_strings_and_lists_as_strings(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    data["materials"][0]["bandgap_value"] = "1.50"
    data["measurements"][0]["device_id"] = "DV-B4; DV-B5"
    data["devices"].append({**data["devices"][0], "device_id": "DV-B5"})
    data["publication"]["year"] = "2023"
    b = parse_extract_response(data, "D021").bundle
    assert str(b.materials[0].bandgap_value) == "1.50"
    assert b.measurements[0].device_id == ["DV-B4", "DV-B5"]
    assert b.publication.year == 2023


def test_record_without_id_dropped(example_7_0_1):
    data = copy.deepcopy(example_7_0_1)
    del data["devices"][0]["device_id"]
    with pytest.raises(ExtractError) as e:  # WP4: запись без ключевого id - тяжёлое нарушение, файл error
        parse_extract_response(data, "D021")
    assert e.value.code == "ml_invalid_data" and "device_id" in e.value.message


def test_result_meta_page_defaults_to_source_page(example_7_0_1):
    r = parse_extract_response(example_7_0_1, "D021").bundle.by_id("results", "R002")
    assert r.meta.page == 13


def test_ml_issues_have_keys(example_7_0_1):
    i = parse_extract_response(example_7_0_1, "D021").bundle.issues[0]
    assert i.key.startswith("I-") and i.computed_by == "ml"


# ---------------- ml: HTTP и кеш ----------------
def test_retry_after_http_date():
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("garbage") is None


def test_503_is_retried(example_7_0_1, extract_req):
    seq = [httpx.Response(503, headers={"Retry-After": "3"}), httpx.Response(200, json=example_7_0_1)]
    sleeps: list[float] = []
    ext = HttpExtractor("http://ml", timeout_s=5, retry_delays=(1,), sleep=sleeps.append,
                        transport=httpx.MockTransport(lambda r: seq.pop(0)))
    assert ext.extract(extract_req()).bundle.publication_id == "D021" and sleeps == [3.0]


def test_cached_falls_back_to_last_on_timeout(fake_extractor, tmp_path, extract_req):
    c = CachedExtractor(fake_extractor, MlCache(tmp_path))
    c.extract(extract_req(sha="a" * 64, version="extract-0.3"))
    fake_extractor.fail = ExtractError("ml_timeout", retryable=True)
    out = c.extract(extract_req(sha="a" * 64, version="extract-0.4"))
    assert out.from_cache and out.warnings and out.model_version == "extract-0.3"


def test_cache_rejects_path_like_sha(tmp_path):
    with pytest.raises(ValueError):
        MlCache(tmp_path).get("../../etc", "v1")


def test_cache_version_with_slashes_is_safe(tmp_path, example_7_0_1):
    c = MlCache(tmp_path)
    c.put("a" * 64, "org/model:1", example_7_0_1)
    assert c.get("a" * 64, "org/model:1") == example_7_0_1
    assert c.last_version("a" * 64) == "org/model:1"
    assert all(p.parent.name == "a" * 64 for p in tmp_path.rglob("*.json"))


def test_resolve_latest_for_raw_is_not_none(tmp_path):
    assert resolve_model_version("latest", RawDirExtractor(tmp_path)) == "raw"
