"""Фикстуры тестов модуля norm (spec/06)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from faktura.norm import LocalNormalizer


@pytest.fixture(scope="session")
def nz() -> LocalNormalizer:
    return LocalNormalizer()


def read_gold(dev_gold_dir: Path, name: str) -> list[dict[str, str]]:
    with open(dev_gold_dir / f"{name}.csv", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="session")
def gold_results(dev_gold_dir: Path) -> list[dict[str, str]]:
    return read_gold(dev_gold_dir, "results")


@pytest.fixture(scope="session")
def gold_layers(dev_gold_dir: Path) -> list[dict[str, str]]:
    return read_gold(dev_gold_dir, "layers")


@pytest.fixture(scope="session")
def gold_materials(dev_gold_dir: Path) -> list[dict[str, str]]:
    return read_gold(dev_gold_dir, "materials")
