"""Адаптеры оценки (`EvalAdapter`, REQ-119): свой регрессионный модуль и внешний `faktura-eval` от ML-2.

Выбор - настройкой `FAKTURA_EVAL_IMPL=local|faktura_eval` (собирает `pipeline.build_services`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from ..domain.ports import EvalReport
from ..domain.tables import EVAL_METRICS
from .core import evaluate

__all__ = ["LocalEval", "FakturaEvalAdapter", "EvalToolError"]


class EvalToolError(RuntimeError):
    """Внешний инструмент оценки недоступен или вернул не то."""


class LocalEval:
    """Свой модуль сравнения с эталоном (spec/14 §14.7) - до появления `faktura-eval`."""

    def __init__(self, metrics: Sequence[str] = EVAL_METRICS):
        self.metrics = tuple(metrics)

    def evaluate(self, gold_dir: Path, pred_dir: Path) -> EvalReport:
        return evaluate(gold_dir, pred_dir, metrics=self.metrics)


class FakturaEvalAdapter:
    """Subprocess `faktura-eval`, когда его поставит ML-2.

    Команда: `<command> --gold <gold_dir> --pred <pred_dir> --format json`; stdout - JSON формы `EvalReport`
    (`per_metric`, `totals`, `warnings`). Аргументы задаются шаблоном `args` с `{gold}` и `{pred}`;
    команду можно переопределить переменной `FAKTURA_EVAL_CMD`."""

    DEFAULT_ARGS: tuple[str, ...] = ("--gold", "{gold}", "--pred", "{pred}", "--format", "json")

    def __init__(self, command: str | None = None, args: Sequence[str] | None = None, timeout_s: float = 600):
        self.command = command or os.environ.get("FAKTURA_EVAL_CMD") or "faktura-eval"
        self.args = tuple(args) if args is not None else self.DEFAULT_ARGS
        self.timeout_s = timeout_s

    def _resolve(self) -> str:
        exe = shutil.which(self.command)
        if exe is None and Path(self.command).is_file():
            exe = str(Path(self.command))
        if exe is None:
            raise EvalToolError(f"faktura-eval не найден ({self.command}): установите пакет оценки ML-2 "
                                f"или используйте FAKTURA_EVAL_IMPL=local")
        return exe

    def evaluate(self, gold_dir: Path, pred_dir: Path) -> EvalReport:
        exe = self._resolve()
        argv = [exe, *(a.format(gold=str(gold_dir), pred=str(pred_dir)) for a in self.args)]
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=self.timeout_s, env=env,
                                  stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as e:
            raise EvalToolError(f"faktura-eval не уложился в {self.timeout_s} с") from e
        except OSError as e:
            raise EvalToolError(f"faktura-eval не запустился: {e}") from e
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            raise EvalToolError(f"faktura-eval завершился с кодом {proc.returncode}: {err.strip()[-1000:]}")
        start, end = out.find("{"), out.rfind("}")
        if start < 0 or end < start:
            raise EvalToolError("faktura-eval: в выводе нет JSON")
        try:
            return EvalReport.model_validate(json.loads(out[start:end + 1]))
        except (ValueError, ValidationError) as e:
            raise EvalToolError(f"faktura-eval: ответ не соответствует EvalReport: {e}") from e
