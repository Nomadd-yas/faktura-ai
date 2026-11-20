"""Фикстуры сквозных тестов (e2e). Нужны данные кейса (маркер `data` ставится в тестах).

Выборка dev-статей (маленькие PDF, разные сценарии):
  D002 - в предметной области, 12 строк results;
  D016 - ИСКЛЮЧЁННАЯ статья (in_scope=false, расчётная работа), results пуст;
  D018 - в предметной области, 8 строк results.
Мок ML-1 отдаёт строки dev_gold; эталон сужается до этих статей, иначе полнота считалась бы по всему dev.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path


E2E_PAPERS = ("D002", "D016", "D018")
EXCLUDED_PAPER = "D016"
TABLES = ("publications", "materials", "devices", "layers", "measurements", "results", "issues")
BACKEND_DIR = Path(__file__).resolve().parents[2]

# TODO(порог-заглушка): поднять до 1.0 после реализации мока; на моке dev_gold ожидается точное совпадение.
MIN_RECALL = 0.95
MIN_PRECISION = 0.95


def read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def run_cli(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    """Запуск `faktura ...` как внешнего процесса (python -m faktura), без внешних сервисов."""
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR / "src"), "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "faktura", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)
