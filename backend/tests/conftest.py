"""Общие фикстуры (план 4.4). Тесты с маркером `data` пропускаются, если нет данных кейса."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
DEFAULT_CASE_DIR = (BACKEND_DIR.parent / "data" / "caseB_kejs5").resolve()


def _case_dir_or_none() -> Path | None:
    env = os.environ.get("FAKTURA_CASE_DIR")
    p = Path(env) if env else DEFAULT_CASE_DIR
    return p if p.is_dir() and (p / "dev_gold").is_dir() else None


def pytest_collection_modifyitems(config, items):
    case = _case_dir_or_none()
    for item in items:
        if "data" in item.keywords and case is None:
            item.add_marker(pytest.mark.skip(reason="нет данных кейса (FAKTURA_CASE_DIR / data/caseB_kejs5)"))
        if "pg" in item.keywords and not os.environ.get("FAKTURA_TEST_PG_URL"):
            item.add_marker(pytest.mark.skip(reason="нет FAKTURA_TEST_PG_URL"))
        if "s3" in item.keywords and not os.environ.get("FAKTURA_TEST_S3_URL"):
            item.add_marker(pytest.mark.skip(reason="нет FAKTURA_TEST_S3_URL"))


@pytest.fixture(scope="session")
def case_dir() -> Path:
    p = _case_dir_or_none()
    if p is None:
        pytest.skip("нет данных кейса")
    return p


@pytest.fixture(scope="session")
def dev_dir(case_dir: Path) -> Path:
    return case_dir / "dev"


@pytest.fixture(scope="session")
def dev_gold_dir(case_dir: Path) -> Path:
    return case_dir / "dev_gold"


@pytest.fixture(scope="session")
def test_dir(case_dir: Path) -> Path:
    return case_dir / "test"


@pytest.fixture(scope="session")
def check_script(case_dir: Path) -> Path:
    p = case_dir / "tools" / "check_submission.py"
    if not p.is_file():
        pytest.skip("нет check_submission.py")
    return p


@pytest.fixture
def settings(tmp_path: Path):
    """Settings с временными путями; без внешних сервисов."""
    from faktura.config import Settings

    case = _case_dir_or_none()
    return Settings(
        _env_file=None,
        case_dir=case,
        db_url=f"sqlite:///{(tmp_path / 'faktura.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        cache_dir=tmp_path / "ml_cache",
        jwt_secret="test-secret-test-secret-test-secret",
        admin_email="admin@example.com",
        admin_password="admin-password-1",
        cookie_secure=False,
        extractor="mock",
    )


@pytest.fixture
def tmp_db_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def make_pdf(tmp_path: Path):
    """Фабрика PDF через PyMuPDF: make_pdf(pages=["text p1", "text p2"], name="a.pdf") -> Path."""
    import fitz

    counter = {"n": 0}

    def _make(pages: list[str] | None = None, name: str | None = None, directory: Path | None = None) -> Path:
        counter["n"] += 1
        d = directory or tmp_path
        d.mkdir(parents=True, exist_ok=True)
        path = d / (name or f"t{counter['n']}.pdf")
        doc = fitz.open()
        for text in pages or ["Test page"]:
            page = doc.new_page()
            if text:
                page.insert_text((72, 100), text, fontsize=11)
        doc.save(path)
        doc.close()
        return path

    return _make


@pytest.fixture(scope="session")
def example_7_0_1() -> dict:
    """Пример ответа ML-1 по spec/07 §7.0.1."""
    return json.loads((FIXTURES_DIR / "ml_extract_example.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def example_broken() -> dict:
    return json.loads((FIXTURES_DIR / "ml_extract_broken.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def mini_gold_dir() -> Path:
    return FIXTURES_DIR / "mini_gold"


@pytest.fixture(scope="session")
def gold_bundles(dev_gold_dir: Path):
    """Бандлы dev_gold через domain.io (кешируются на сессию)."""
    from faktura.domain.io import bundles_from_tables, read_tables

    return bundles_from_tables(read_tables(dev_gold_dir))
