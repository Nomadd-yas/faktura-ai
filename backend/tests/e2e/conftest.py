"""Фикстуры e2e (константы и помощники - в e2e_support.py)."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from e2e_support import E2E_PAPERS, TABLES, read_csv  # noqa: E402
import csv  # noqa: E402


@pytest.fixture(scope="session")
def e2e_in(tmp_path_factory, dev_dir: Path) -> Path:
    """Папка с 3 dev-PDF."""
    d = tmp_path_factory.mktemp("e2e_in")
    for pid in E2E_PAPERS:
        shutil.copy2(dev_dir / f"{pid}.pdf", d / f"{pid}.pdf")
    return d


@pytest.fixture(scope="session")
def e2e_gold(tmp_path_factory, dev_gold_dir: Path) -> Path:
    """dev_gold, суженный до E2E_PAPERS (7 CSV)."""
    d = tmp_path_factory.mktemp("e2e_gold")
    for t in TABLES:
        rows = read_csv(dev_gold_dir / f"{t}.csv")
        with open(dev_gold_dir / f"{t}.csv", encoding="utf-8-sig", newline="") as f:
            cols = next(csv.reader(f))
        with open(d / f"{t}.csv", "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(r for r in rows if r["publication_id"] in E2E_PAPERS)
    return d


@pytest.fixture(scope="session")
def e2e_zip(tmp_path_factory, e2e_in: Path) -> Path:
    base = tmp_path_factory.mktemp("e2e_zip") / "dev3"
    return Path(shutil.make_archive(str(base), "zip", e2e_in))


@pytest.fixture
def e2e_settings(settings, e2e_gold: Path):
    return settings.model_copy(update={"mock_gold_dir": e2e_gold, "extractor": "mock", "mock_issues": "all"})
