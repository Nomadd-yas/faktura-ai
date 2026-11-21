"""Ход разбора и оставшееся время (прогресс файла и прогона, ETA).

ML-сервис отвечает одним запросом на файл и не сообщает о страницах, поэтому внутри этапа «Извлечение» ход
оценивается по времени: прошло / ожидаемое (страницы x секунд на страницу), не выше `STAGE_CAP` доли этапа,
пока этап реально не завершится. Скорости этапов - скользящее среднее по последним файлам (таблица
`stage_speeds`, отдельно по областям), до накопления данных - значения по умолчанию `DEFAULT_SPP`.

Доли файла: извлечение 0-70 %, нормализация 70-85 %, проверка 85-100 %. 100 - только в конечном статусе.
Всё здесь - чистые функции (кроме `Smoother`), БД не трогают.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

EXTRACT, NORMALIZE, VALIDATE = "Извлечение", "Нормализация", "Проверка"
STAGES = (EXTRACT, NORMALIZE, VALIDATE)
STAGE_RANGE = {EXTRACT: (0.0, 70.0), NORMALIZE: (70.0, 85.0), VALIDATE: (85.0, 100.0)}
STAGE_CAP = 0.95  # внутри этапа ход не выше этой доли, пока этап не завершился
OVERRUN_LEFT = 0.1  # если этап идёт дольше ожидаемого: осталось ещё 10 % ожидаемого времени
UNKNOWN_PAGES = 10  # для хода файла без известного числа страниц

# секунд на страницу, пока нет замеров. Извлечение на деле ~10-20 с/стр; нормализация и проверка - доли секунды
DEFAULT_SPP: dict[str, dict[str, float]] = {
    "solar": {EXTRACT: 10.0, NORMALIZE: 0.05, VALIDATE: 0.1},
    "rag": {EXTRACT: 15.0, NORMALIZE: 0.05, VALIDATE: 0.1},
}
FALLBACK_SPP = {EXTRACT: 12.0, NORMALIZE: 0.05, VALIDATE: 0.1}

MAX_JUMP = 0.30  # ETA между опросами меняется не более чем на 30 %
ETA_STEP = 5  # округление ETA, секунд


def speeds_for(domain: str, learned: dict[str, float] | None) -> dict[str, float]:
    """Скорости этапов: выученные поверх значений по умолчанию области."""
    out = dict(DEFAULT_SPP.get(domain, FALLBACK_SPP))
    for k, v in (learned or {}).items():
        if k in out and v and v > 0:
            out[k] = float(v)
    return out


def stage_fractions(stage: str, elapsed: float, pages: float, speeds: dict[str, float]) -> float:
    """Доля пройденного внутри этапа, 0..STAGE_CAP."""
    expected = max(pages, 1.0) * speeds[stage]
    return max(0.0, min(elapsed / expected, STAGE_CAP)) if expected > 0 else STAGE_CAP


def file_progress(stage: str | None, elapsed: float, pages: int | None, speeds: dict[str, float]) -> float:
    """Процент выполняющегося файла (0..99.x)."""
    st = stage if stage in STAGE_RANGE else EXTRACT
    lo, hi = STAGE_RANGE[st]
    frac = stage_fractions(st, max(elapsed, 0.0), float(pages or UNKNOWN_PAGES), speeds)
    return lo + (hi - lo) * frac


def file_remaining(stage: str | None, elapsed: float, pages: float, speeds: dict[str, float]) -> float:
    """Секунд до конца выполняющегося файла: остаток текущего этапа + все этапы впереди."""
    st = stage if stage in STAGE_RANGE else EXTRACT
    expected = max(pages, 1.0) * speeds[st]
    left = max(expected - max(elapsed, 0.0), expected * OVERRUN_LEFT)
    for nxt in STAGES[STAGES.index(st) + 1:]:
        left += max(pages, 1.0) * speeds[nxt]
    return left


def file_duration(pages: float, speeds: dict[str, float]) -> float:
    """Секунд на файл целиком (для файла в очереди)."""
    return max(pages, 1.0) * sum(speeds[s] for s in STAGES)


@dataclass
class FileState:
    """Файл для расчёта: status - queued | running | final."""
    key: str
    status: str
    pages: int | None = None
    stage: str | None = None
    elapsed: float = 0.0  # секунд в текущем этапе


@dataclass
class Estimate:
    progress: dict[str, int]  # key -> процент файла
    eta: dict[str, float | None]  # key -> секунд (None - неизвестно); конечные файлы: None
    run_progress: int
    run_eta: float | None


def estimate(files: list[FileState], speeds: dict[str, float], workers: int) -> Estimate:
    """Прогресс и ETA файлов и всего прогона (до сглаживания и округления).

    Прогресс прогона - среднее прогрессов файлов с весом в страницах. ETA файла в очереди включает ожидание:
    очередь раздаётся свободным рабочим по порядку. ETA прогона = max(остаток работы / рабочих,
    ETA самого долгого файла).
    """
    workers = max(int(workers or 1), 1)
    known = [f.pages for f in files if f.pages]
    avg_pages = (sum(known) / len(known)) if known else None
    prog: dict[str, float] = {}
    for f in files:
        if f.status == "final":
            prog[f.key] = 100.0
        elif f.status == "running":
            prog[f.key] = min(file_progress(f.stage, f.elapsed, f.pages, speeds), 99.0)
        else:
            prog[f.key] = 0.0
    weights = {f.key: float(f.pages or avg_pages or 1.0) for f in files}
    wsum = sum(weights.values())
    run_p = sum(prog[k] * weights[k] for k in prog) / wsum if wsum else 0.0

    eta: dict[str, float | None] = {f.key: None for f in files}
    live = [f for f in files if f.status != "final"]
    if not live:
        return Estimate({k: int(v) for k, v in prog.items()}, eta, 100 if files else 0, None)
    if avg_pages is None:  # страниц не знаем ни у одного файла: оценить нельзя
        return Estimate({k: int(v) for k, v in prog.items()}, eta, int(run_p), None)

    slots: list[float] = []
    total = 0.0
    for f in live:
        if f.status == "running":
            left = file_remaining(f.stage, f.elapsed, float(f.pages or avg_pages), speeds)
            eta[f.key] = left
            slots.append(left)
            total += left
    slots += [0.0] * max(workers - len(slots), 0)
    for f in live:
        if f.status != "queued":
            continue
        slots.sort()
        dur = file_duration(float(f.pages or avg_pages), speeds)
        slots[0] += dur
        eta[f.key] = slots[0]
        total += dur
    longest = max((v for v in eta.values() if v is not None), default=0.0)
    run_eta = max(total / workers, longest)
    return Estimate({k: int(v) for k, v in prog.items()}, eta, int(run_p), run_eta)


# --- сглаживание ---------------------------------------------------------------------------------

def smooth_eta(prev: float | None, new: float | None, max_jump: float = MAX_JUMP) -> float | None:
    """Не более чем на max_jump (доля) от предыдущего значения за опрос."""
    if new is None:
        return None
    if prev is None or prev <= 0:
        return new
    return min(max(new, prev * (1 - max_jump)), prev * (1 + max_jump))


def round_eta(v: float | None, step: int = ETA_STEP) -> int | None:
    """Округление до step секунд; идущая работа не показывает 0."""
    if v is None:
        return None
    return max(int(round(v / step)) * step, step)


class Smoother:
    """Состояние между опросами в памяти (ключ -> прошлый ETA и прогресс). Потеря при рестарте допустима."""

    LIMIT = 20000

    def __init__(self) -> None:
        self._eta: dict[str, float] = {}
        self._prog: dict[str, int] = {}
        self._lock = threading.Lock()

    def eta(self, key: str, value: float | None) -> int | None:
        with self._lock:
            sm = smooth_eta(self._eta.get(key), value)
            if sm is None:
                self._eta.pop(key, None)
            else:
                if len(self._eta) > self.LIMIT:
                    self._eta.clear()
                self._eta[key] = sm
        return round_eta(sm)

    def progress(self, key: str, value: int, *, final: bool = False) -> int:
        """Процент не убывает; 100 только в конечном статусе."""
        with self._lock:
            v = 100 if final else min(max(value, self._prog.get(key, 0)), 99)
            if len(self._prog) > self.LIMIT:
                self._prog.clear()
            self._prog[key] = v
        return v

    def forget(self, key: str) -> None:
        with self._lock:
            self._eta.pop(key, None)
            self._prog.pop(key, None)


SMOOTHER = Smoother()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def elapsed_since(ts: datetime | None, now: datetime | None = None) -> float:
    if ts is None:
        return 0.0
    now = now or utcnow()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return max((now - ts).total_seconds(), 0.0)
