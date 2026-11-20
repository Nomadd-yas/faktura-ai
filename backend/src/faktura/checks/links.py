"""Автосвязывание повторов (REQ-032) и ключ сопоставимости (spec/05 §5.3, REQ-049/REQ-059)."""
from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel

from ..domain.records import PaperBundle, Result
from ._util import HUNDRED, PaperIndex, area_cm2, effective, fmt, is_am15, is_rejected, num, pin_mw_cm2, \
    rounding_explains


class DuplicateLink(BaseModel):
    result_id: str
    duplicate_of: str
    note: str = "связано бэком"


def link_duplicates(bundle: PaperBundle) -> list[DuplicateLink]:
    """Явные повторы, которые ML не связал: тот же прибор, показатель, измерение, агрегация, момент времени и
    значение в пределах округления. Повтор ссылается на самую раннюю запись (ml_order); цепочек нет.
    Уже связанные, отклонённые и чужие (cited) записи не трогаются. bundle не меняется."""
    idx = PaperIndex(bundle)
    already_main = {r.duplicate_of for r in bundle.results if r.duplicate_of}
    rows = [r for r in idx.sorted(bundle.results)
            if not is_rejected(r) and not r.duplicate_of and r.device_id and r.metric_name
            and r.result_origin != "cited" and effective(r) is not None]
    groups: dict[tuple, list[Result]] = defaultdict(list)
    for r in rows:
        groups[(r.device_id, r.metric_name, r.measurement_id, r.aggregation, r.time_point)].append(r)
    links: list[DuplicateLink] = []
    for grp in groups.values():
        mains: list[Result] = []
        for r in grp:
            v = effective(r)
            target = next((m for m in mains if rounding_explains(m, effective(m), r, v)), None)  # type: ignore[arg-type]
            if target is None or r.result_id in already_main:
                mains.append(r)
                continue
            links.append(DuplicateLink(result_id=r.result_id, duplicate_of=target.result_id))
    links.sort(key=lambda l: idx.rank(l.result_id))
    return links


def _area_bucket(a) -> str:
    if a < num(0.1):
        return "area<0.1"
    if a <= 1:
        return "area0.1-1"
    return "area>1"


def comparability_key(result: Result, bundle: PaperBundle) -> tuple[str, bool]:
    """(ключ, неполный). Ключ: material_class | device_type | спектр@интенсивность | корзина площади |
    aggregation | scan_direction | certified | stability. Пустое поле -> «?» и признак «неполный».
    certified не указан -> «не сертифицирован» (без признака неполноты). Только фильтр: замечаний не создаёт."""
    idx = PaperIndex(bundle)
    incomplete = False

    def part(v: object | None) -> str:
        nonlocal incomplete
        if v is None or (isinstance(v, str) and not v.strip()):
            incomplete = True
            return "?"
        return str(v)

    mat = idx.material(result)
    dev = idx.device(result)
    ms = idx.measurement(result)
    parts = [part(mat.material_class if mat is not None else None),
             part(dev.device_type if dev is not None else None)]
    if ms is None or not ms.spectrum:
        parts.append(part(None))
    else:
        spectrum = "AM1.5G" if is_am15(ms.spectrum) else ms.spectrum.strip().lower()
        pin, defaulted = pin_mw_cm2(ms)
        if pin is None or defaulted:
            parts.append(f"{spectrum}@?")
            incomplete = True
        else:
            pin_s = "100" if pin == HUNDRED else fmt(pin.normalize())
            parts.append(f"{spectrum}@{pin_s}mW/cm2")
    a = area_cm2(dev)
    parts.append(part(_area_bucket(a) if a is not None else None))
    parts.append(part(result.aggregation))
    parts.append(part(ms.scan_direction if ms is not None else None))
    cert = ms.certified if ms is not None else None
    parts.append("certified" if cert is True or str(cert).lower() == "true" else "not_certified")
    stab = None
    if ms is not None and (ms.stability_protocol or ms.measurement_type == "stability"):
        stab = f"stability:{ms.stability_protocol or 'other'}"
    parts.append(stab or "initial")
    return "|".join(parts), incomplete
