"""Общие помощники модуля checks: числа, точность записи, индекс статьи, единицы, форматирование.

Всё считается в Decimal: границы порогов (3 %, 5 %, 1 п.п.) должны срабатывать точно, без ошибок float.
Нераспознанный текст ячейки (str в числовом поле) в правилах не участвует.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

from ..domain.records import (Device, Material, Measurement, PaperBundle, Result, ReviewStatus)
from ..domain.tables import TARGET_UNITS

ZERO = Decimal(0)
HUNDRED = Decimal(100)

_NUM_RE = re.compile(r"[-+−]?\d+(?:[.,]\d+)?")


def num(v: object) -> Decimal | None:
    """Decimal (конечное) или None; строки (нераспознанный текст ячейки) и bool не считаются числом."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, Decimal):
        return v if v.is_finite() else None
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        d = Decimal(str(v))
        return d if d.is_finite() else None
    return None


def effective(r: Result) -> Decimal | None:
    """Действующее значение: corrected_value ?? normalized_value (spec/05 §5.0)."""
    return num(PaperBundle.effective_value(r))


def is_rejected(row: object) -> bool:
    meta = getattr(row, "meta", None)
    return meta is not None and str(meta.review_status) == ReviewStatus.rejected.value


def fmt(d: Decimal | None) -> str:
    """Число как в статье: без экспоненты, с исходным числом знаков (15.0 остаётся 15.0)."""
    if d is None:
        return "—"
    s = format(d, "f")
    return "0" if s in ("-0", "") else s


def fmt_q(d: Decimal, places: int = 2) -> str:
    """Вычисленное число с фиксированным числом знаков."""
    q = Decimal(1).scaleb(-places)
    return fmt(d.quantize(q))


def parse_leading_number(text: str | None) -> Decimal | None:
    """Первое число в тексте ячейки («1197», «0.71», «15.2 ± 0.3», «1,5»)."""
    if not text:
        return None
    m = _NUM_RE.search(str(text))
    if not m:
        return None
    s = m.group(0).replace("−", "-").replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _ulp(d: Decimal) -> Decimal:
    exp = d.as_tuple().exponent
    return Decimal(1).scaleb(int(exp)) if isinstance(exp, int) else Decimal(1)


def half_ulp(r: Result, value: Decimal | None = None) -> Decimal:
    """Половина единицы последнего знака значения записи в единицах normalized_value.

    Точность берётся из исходного текста (raw_value: «432 mV» -> 0.0005 V, «0.71» доли -> 0.5 %),
    а если его нет или он не число - из записи самого значения."""
    v = value if value is not None else effective(r)
    if v is None:
        return ZERO
    if r.meta.corrected_value is None:
        raw = parse_leading_number(r.raw_value)
        if raw is not None and raw != 0 and v != 0:
            ratio = abs(v / raw)
            return _ulp(raw) * ratio / 2
    return _ulp(v) / 2


def rel_half_ulp(r: Result, value: Decimal | None = None) -> Decimal:
    v = value if value is not None else effective(r)
    if v is None or v == 0:
        return ZERO
    return half_ulp(r, v) / abs(v)


def rounding_explains(a: Result, va: Decimal, b: Result, vb: Decimal) -> bool:
    """Два значения различаются только округлением: более точное лежит в интервале округления грубого.
    Пример: 57.5 и 57.49 - да; 344 и 343 (оба до 1 мВ) - нет."""
    if va == vb:
        return True
    coarse = max(half_ulp(a, va), half_ulp(b, vb))
    return abs(va - vb) <= coarse


# --- единицы ---------------------------------------------------------------------------------------------------------

def unit_ok(r: Result) -> bool:
    """normalized_unit совпадает с целевой единицей показателя (или не указана)."""
    target = TARGET_UNITS.get(r.metric_name or "")
    unit = r.normalized_unit
    if target is None or not unit:
        return True
    return unit.strip() == target


_PIN_UNITS = {
    "mw/cm2": Decimal(1), "mw/cm^2": Decimal(1), "mw cm-2": Decimal(1), "mwcm-2": Decimal(1),
    "w/m2": Decimal("0.1"), "w/m^2": Decimal("0.1"), "w m-2": Decimal("0.1"), "wm-2": Decimal("0.1"),
    "kw/m2": Decimal(100), "kw/m^2": Decimal(100),
    "w/cm2": Decimal(1000), "w/cm^2": Decimal(1000),
    "sun": Decimal(100), "suns": Decimal(100),
    "uw/cm2": Decimal("0.001"), "µw/cm2": Decimal("0.001"), "μw/cm2": Decimal("0.001"),
    "lux": None, "lx": None,
}


def is_am15(spectrum: str | None) -> bool:
    return bool(spectrum) and bool(re.search(r"am\s*1[.,]5", spectrum.lower()))


def pin_mw_cm2(ms: Measurement | None) -> tuple[Decimal | None, bool]:
    """Мощность падающего света в mW/cm2 и признак «взята по умолчанию».

    W/m2 x 0.1, sun x 100; единица не указана: значение > 200 считается W/m2 (1000 -> 100).
    Нет интенсивности: при AM1.5G или неизвестном спектре - 100 (в данные не пишется); при
    нестандартном спектре без интенсивности - None (правило пропускается).
    Интенсивность <= 0 возвращается как есть (правило пропускается, К-21)."""
    if ms is None:
        return HUNDRED, True
    li = ms.light_intensity
    if li is None or (isinstance(li, str) and not li.strip()):
        if ms.spectrum is None or is_am15(ms.spectrum) or "1 sun" in (ms.spectrum or "").lower():
            return HUNDRED, True
        return None, True
    v = num(li)
    if v is None:
        return None, False
    unit = (ms.intensity_unit or "").strip().lower().replace(" ", "") if ms.intensity_unit else ""
    if not unit:
        return (v * Decimal("0.1") if v > 200 else v), False
    factor = _PIN_UNITS.get(unit, _PIN_UNITS.get(unit.replace("²", "2")))
    if factor is None:
        return None, False
    return v * factor, False


def area_cm2(dev: Device | None) -> Decimal | None:
    if dev is None:
        return None
    a = num(dev.active_area)
    if a is None:
        return None
    unit = (dev.area_unit or "cm2").strip().lower().replace("²", "2").replace("^", "")
    if unit in ("mm2",):
        return a / 100
    if unit in ("m2",):
        return a * 10000
    return a


# --- индекс статьи ---------------------------------------------------------------------------------------------------

class PaperIndex:
    """Быстрый доступ к строкам статьи (одна сборка на прогон правил)."""

    def __init__(self, bundle: PaperBundle) -> None:
        self.bundle = bundle
        self.pid = bundle.publication_id
        self.results: dict[str, Result] = {}
        for r in bundle.results:
            self.results.setdefault(r.result_id, r)
        self.devices: dict[str, Device] = {}
        for d in bundle.devices:
            self.devices.setdefault(d.device_id, d)
        self.materials: dict[str, Material] = {}
        for m in bundle.materials:
            self.materials.setdefault(m.material_id, m)
        self.measurements: dict[str, Measurement] = {}
        for m in bundle.measurements:
            self.measurements.setdefault(m.measurement_id, m)
        self.order: dict[str, int] = {r.result_id: i for i, r in enumerate(
            sorted(bundle.results, key=lambda x: (x.meta.ml_order, x.result_id)))}
        # записи, участвующие в правилах: не отклонены, есть действующее число, известна метрика
        self.live: list[Result] = [r for r in self.sorted(bundle.results)
                                   if not is_rejected(r) and r.metric_name and effective(r) is not None
                                   and unit_ok(r)]
        # без повторов (duplicate_of) и чужих результатов
        self.main: list[Result] = [r for r in self.live if not r.duplicate_of and r.result_origin != "cited"]

    def sorted(self, rows: Iterable[Result]) -> list[Result]:
        return sorted(rows, key=lambda x: (x.meta.ml_order, x.result_id))

    def rank(self, rid: str) -> int:
        return self.order.get(rid, 1 << 30)

    def device(self, r: Result) -> Device | None:
        return self.devices.get(r.device_id) if r.device_id else None

    def measurement(self, r: Result) -> Measurement | None:
        return self.measurements.get(r.measurement_id) if r.measurement_id else None

    def material(self, r: Result) -> Material | None:
        dev = self.device(r)
        if dev is not None and dev.absorber_material_id and dev.absorber_material_id in self.materials:
            return self.materials[dev.absorber_material_id]
        if r.material_id and r.material_id in self.materials:
            return self.materials[r.material_id]
        return None

    def bandgap_ev(self, r: Result) -> Decimal | None:
        m = self.material(r)
        if m is None:
            return None
        if m.bandgap_unit and m.bandgap_unit.strip() != "eV":
            return None
        eg = num(m.bandgap_value)
        return eg if eg is not None and eg > 0 else None

    def device_label(self, r: Result) -> str | None:
        dev = self.device(r)
        if dev is not None:
            return dev.device_label or dev.device_id
        return r.device_id


def place_of(r: Result) -> tuple[str, str]:
    """Место записи в статье: (таблица, рисунок); текст - ('', '')."""
    return ((r.source_table or "").strip(), (r.source_figure or "").strip())


def is_table_row(r: Result) -> bool:
    return bool((r.source_table or "").strip())


def is_figure_only(r: Result) -> bool:
    return bool((r.source_figure or "").strip()) and not is_table_row(r)
