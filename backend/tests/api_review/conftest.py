"""Фикстуры модуля api_review. Корпус из D001 обрабатывается моком ML-1 (эталон dev_gold), БД SQLite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from review_support import Env, build_env  # noqa: E402


@pytest.fixture
def env(tmp_path, case_dir) -> Env:
    """Свежее окружение на каждый тест (правки мутируют БД)."""
    return build_env(tmp_path / "rv", case_dir)


@pytest.fixture
def svc(env):
    return env.review


@pytest.fixture
def user(env):
    return env.user


@pytest.fixture
def client(env):
    return env.client


@pytest.fixture
def new_fact(env):
    """Любой факт voc в статусе new."""
    return env.find_fact("voc", status="new")
