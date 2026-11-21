"""[faktura-patch] meta.page_texts: постраничный текст OCR для полнотекстового индекса бэка. Без сети и ключей."""
import sys
import types
from pathlib import Path

SVC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SVC))
try:
    import openai  # noqa: F401
except ImportError:
    stub = types.ModuleType("openai")
    stub.OpenAI = object
    sys.modules["openai"] = stub

import pipeline  # noqa: E402

MD = "<!-- page 1 -->\n\nFirst page text.\n\n<!-- page 2 -->\n\nSecond page.\n\n<!-- page 3 -->\n\nThird page.\n"


def test_all_ocr_pages():
    assert pipeline.build_page_texts(MD, 3) == ["First page text.", "Second page.", "Third page."]


def test_pdf_pages_are_empty_and_count_padded():
    infos = [{"page": 1, "source": "pdf"}, {"page": 2, "source": "ocr"}, {"page": 3, "source": "pdf"},
             {"page": 4, "source": "ocr"}]
    got = pipeline.build_page_texts(MD, 4, infos)
    assert got == ["", "Second page.", "", ""]


def test_no_markers_gives_none():
    assert pipeline.build_page_texts("plain markdown without markers", 3) is None
