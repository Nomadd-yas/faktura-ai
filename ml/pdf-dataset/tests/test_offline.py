"""Офлайн-тесты сервиса pdf-dataset (без сети и без ключей). Запуск: pytest ml/pdf-dataset/tests -q"""
import sys
import types
from pathlib import Path

import pytest

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:  # [faktura-patch] openai нужен только для вызовов LLM — в тестах подменяем заглушкой
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import extract_dataset as ed  # noqa: E402
import pipeline  # noqa: E402
from strip_tail import strip_trailing_sections  # noqa: E402


def _backend_tables():
    src = SVC.parents[1] / "backend" / "src"
    if not (src / "faktura" / "domain" / "tables.py").exists():
        return None
    sys.path.insert(0, str(src))
    from faktura.domain.tables import TABLES
    return TABLES


def test_schema_columns_match_backend():
    tables = _backend_tables()
    if tables is None:
        pytest.skip("backend недоступен")
    for name in ed.TABLES:
        assert tuple(ed.TABLES[name]["properties"]) == tables[name], name


def test_page_markers_and_image_skipped():
    jr = [[{"label": "text", "content": "Intro text"},
           {"label": "image", "content": "![img](x.jpg)"}],
          [{"label": "table", "content": "<table><tr><td>PCE</td></tr></table>"}]]
    md, paged = pipeline.build_paged_markdown(jr, "fallback")
    assert paged
    assert md.index("<!-- page 1 -->") < md.index("Intro text") < md.index("<!-- page 2 -->") < md.index("<table>")
    assert "img" not in md


def test_page_markers_fallback():
    md, paged = pipeline.build_paged_markdown({"weird": 1}, "raw md")
    assert md == "raw md" and not paged


def test_strip_tail_ignores_early_heading():
    body = "# Intro\n\n" + "text line\n" * 100
    text = "## Funding\n\n" + body  # заголовок в самом начале — не режем
    cleaned, info = strip_trailing_sections(text)
    assert info is None and cleaned == text


def test_strip_tail_cuts_late_heading_keeps_markers():
    text = ("<!-- page 1 -->\n\n" + "body\n" * 200 + "<!-- page 2 -->\n\nmore\n" * 5
            + "\n<!-- page 3 -->\n\n## References\n\n[1] A. B.\n" * 1)
    cleaned, info = strip_trailing_sections(text)
    assert info is not None
    assert "<!-- page 1 -->" in cleaned and "[1] A. B." not in cleaned
    assert not cleaned.rstrip().endswith("-->")  # висящий маркер убран


def test_check_dataset_valid_and_invalid():
    ds = {
        "publications": [{"publication_id": "P1", "in_scope": True}],
        "materials": [{"publication_id": "P1", "material_id": "M1", "material_class": "perovskite"}],
        "devices": [{"publication_id": "P1", "device_id": "DV1", "absorber_material_id": "M1",
                     "area_unit": "cm2"}],
        "layers": [{"publication_id": "P1", "layer_id": "L1", "device_id": "DV1", "layer_order": 1,
                    "layer_thickness": "63-68"}],
        "measurements": [{"publication_id": "P1", "measurement_id": "MS1", "device_id": "DV1;DV2"}],
        "results": [{"publication_id": "P1", "result_id": "R1", "device_id": "DV1", "measurement_id": "MS1",
                     "metric_name": "PCE", "source_page": 3}],
        "issues": [{"publication_id": "P1", "issue_id": "I1", "record_ids": "R1",
                    "check_name": "contradiction"}],
    }
    ds["devices"].append({"publication_id": "P1", "device_id": "DV2"})
    assert ed.check_dataset(ds) == []
    ds["materials"][0]["material_class"] = "bogus"
    assert any("material_class" in p for p in ed.check_dataset(ds))
