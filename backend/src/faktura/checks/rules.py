"""Правила бэка spec/05 §5.2 (REQ-050…REQ-052, К-21).

Все правила работают внутри одной статьи по действующему значению (corrected_value ?? normalized_value).
Отклонённые записи не участвуют; повторы (duplicate_of) и чужие результаты (result_origin=cited) участвуют
только в contradiction/rounding. Исходные записи не меняются: правила читают bundle и возвращают новые
IssueRecord с computed_by='backend'. Порядок выхода детерминирован: порядок BACKEND_RULES, внутри правила -
порядок записей (ml_order).

Калибровка на dev_gold (план 5.3, Р-4):
- jv_consistency сравнивает тройку Voc/Jsc/FF с PCE той же агрегации. Для средних (mean/median) правило по
  умолчанию не считается (произведение средних не равно среднему произведений - это и есть два ложных
  срабатывания на D021); включается параметром check_mean. Если у тройки агрегация не указана, а у прибора есть
  PCE нескольких агрегаций (D004: mean и champion), берётся агрегация, с которой согласуется большинство
  приборов статьи; запись выбивается, если не согласуется с ней (в record_ids - все PCE прибора).
- rounding вместо jv_consistency: расхождение не больше суммарной (в квадратуре) относительной погрешности
  округления последних знаков множителей и PCE. Вместо contradiction: более точное значение лежит в интервале
  округления грубого (57.49 и 57.5).
- Пары «таблица - значение только с рисунка» с расхождением не больше порога figure_mismatch (3 %) считаются
  точностью чтения графика и не дают contradiction.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Iterable

from ..domain.ids import sha8
from ..domain.ports import QuoteChecker
from ..domain.profile import RuleConfig
from ..domain.records import IssueRecord, Measurement, PaperBundle, Result
from ..domain.tables import CHECK_CATALOG, TARGET_UNITS
from ._util import (HUNDRED, ZERO, PaperIndex, area_cm2, effective, fmt, fmt_q, half_ulp, is_am15, is_figure_only,
                    is_rejected, is_table_row, pin_mw_cm2, place_of, rel_half_ulp, rounding_explains)
from .anchor import fill_paper_source, fill_record_source

RULES_VERSION: str = "1.0.0"

BACKEND_RULES: tuple[str, ...] = (
    "jv_consistency", "voc_limit", "range", "eqe_consistency", "champion_vs_mean", "hysteresis_index",
    "contradiction", "unit_inconsistency", "rounding", "voc_deficit_consistency", "missing_conditions",
    "plausibility", "figure_mismatch", "dedup_trap", "comparability", "ambiguity",
)

JV_METRICS = ("Voc", "Jsc", "FF", "PCE")
_AGG_ORDER = {None: 0, "champion": 1, "mean": 2, "median": 3}
_AGG_RU = {"champion": "лучший", "mean": "среднее", "median": "медиана", None: "агрегация не указана"}

# уровень мировых рекордов по классу поглотителя (spec/05: plausibility, «таблица рекордов»):
# замечание, если значение выше порога. Порог чуть выше сертифицированного рекорда класса.
PLAUSIBILITY_LIMITS: dict[str, dict[str, Decimal]] = {
    "perovskite": {"PCE": Decimal("27.5"), "FF": Decimal("86")},
    "CIGS": {"PCE": Decimal("24"), "FF": Decimal("84")},
    "CdTe": {"PCE": Decimal("23.5"), "FF": Decimal("83")},
    "kesterite": {"PCE": Decimal("16"), "FF": Decimal("76")},
    "Sb2Se3": {"PCE": Decimal("12"), "FF": Decimal("75")},
    "thin-film Si": {"PCE": Decimal("15"), "FF": Decimal("80")},
    "other": {"PCE": Decimal("35"), "FF": Decimal("90")},
}
_PLAUS_DEFAULT = {"PCE": Decimal("35"), "FF": Decimal("90")}


def _d(v: Any, default: str) -> Decimal:
    try:
        return Decimal(str(v)) if v is not None else Decimal(default)
    except Exception:  # noqa: BLE001 - неверный параметр профиля -> значение по умолчанию
        return Decimal(default)


def _unit(r: Result) -> str:
    u = TARGET_UNITS.get(r.metric_name or "") or (r.normalized_unit or "")
    return f" {u}" if u else ""


def _f(x: Decimal, places: int = 4) -> float:
    return float(round(x, places))


@dataclass
class _Ctx:
    bundle: PaperBundle
    idx: PaperIndex
    rules: dict[str, RuleConfig]
    quote: QuoteChecker | None
    out: list[IssueRecord] = field(default_factory=list)

    def on(self, name: str) -> bool:
        rc = self.rules.get(name)
        return rc is not None and rc.enabled

    def params(self, name: str) -> dict[str, Any]:
        rc = self.rules.get(name)
        return dict(rc.params) if rc is not None else {}

    def emit(self, check: str, rows: Iterable[Result | str], description: str,
             numbers: dict[str, Any] | None = None) -> IssueRecord:
        ids: list[str] = []
        for x in rows:
            rid = x if isinstance(x, str) else x.result_id
            if rid not in ids:
                ids.append(rid)
        info = CHECK_CATALOG.get(check)
        # детерминированный id правил бэка: I-{check}-{первая запись}; без записей - короткий хеш (spec §6)
        iss = IssueRecord(publication_id=self.idx.pid, record_ids=ids, check_name=check, description=description,
                          severity=info.severity if info else "warn", computed_by="backend",
                          numbers=numbers or {},
                          issue_id=f"I-{check}-{ids[0] if ids else sha8([check])[:6]}")
        if info is not None and info.scope == "paper":
            iss.record_ids = []
            fill_paper_source(iss, self.idx, self.quote)
        else:
            fill_record_source(iss, self.idx, self.quote)
        self.out.append(iss)
        return iss


def _group(rows: Iterable[Result], key: Callable[[Result], Any]) -> dict[Any, list[Result]]:
    g: dict[Any, list[Result]] = defaultdict(list)
    for r in rows:
        g[key(r)].append(r)
    return g


def _distinct(rows: list[Result]) -> list[Result]:
    """Строки с разными действующими значениями (первая по порядку на каждое значение)."""
    seen: set[Decimal] = set()
    out: list[Result] = []
    for r in rows:
        v = effective(r)
        if v not in seen:
            seen.add(v)  # type: ignore[arg-type]
            out.append(r)
    return out


# --- jv_consistency + rounding ---------------------------------------------------------------------------------------

@dataclass
class _JvItem:
    voc: Result
    jsc: Result
    ff: Result
    candidates: list[Result]  # PCE-кандидаты (по одному на агрегацию)
    pin: Decimal
    pin_default: bool


def _jv_items(ctx: _Ctx, check_mean: bool) -> list[_JvItem]:
    idx = ctx.idx
    rows = [r for r in idx.main if r.device_id and r.metric_name in JV_METRICS and r.time_point is None]
    items: list[_JvItem] = []
    for (_dev, _meas), grp in sorted(_group(rows, lambda r: (r.device_id, r.measurement_id)).items(),
                                     key=lambda kv: idx.rank(kv[1][0].result_id)):
        by_agg = _group(grp, lambda r: r.aggregation)
        pce_all = [r for r in grp if r.metric_name == "PCE"]
        if not pce_all:
            continue
        ms = idx.measurement(grp[0])
        pin, pin_default = pin_mw_cm2(ms)
        if pin is None or pin <= 0:  # К-21: Pin <= 0 или неизвестен - правило не считается
            continue
        for agg in sorted(by_agg, key=lambda a: _AGG_ORDER.get(a, 9)):
            if agg in ("mean", "median") and not check_mean:
                continue
            comp = by_agg[agg]
            triples = _triples(comp)
            for voc, jsc, ff in triples:
                place = place_of(voc)
                if agg is not None:
                    cands = [r for r in comp if r.metric_name == "PCE"]
                else:
                    cands = [r for r in comp if r.metric_name == "PCE"] or pce_all
                cands = _pick_per_agg(cands, place)
                if not cands:
                    continue
                items.append(_JvItem(voc, jsc, ff, cands, pin, pin_default))
    return items


def _triples(comp: list[Result]) -> list[tuple[Result, Result, Result]]:
    """Тройки (Voc, Jsc, FF): одна на группу, а при нескольких значениях - по месту в статье."""
    def one(rows: list[Result], metric: str) -> Result | None:
        d = _distinct([r for r in rows if r.metric_name == metric])
        return d[0] if len(d) == 1 else None

    t = [one(comp, m) for m in ("Voc", "Jsc", "FF")]
    if all(t):
        return [tuple(t)]  # type: ignore[list-item]
    out = []
    for _place, sub in _group(comp, place_of).items():
        t = [one(sub, m) for m in ("Voc", "Jsc", "FF")]
        if all(t):
            out.append(tuple(t))
    return out  # type: ignore[return-value]


def _pick_per_agg(cands: list[Result], place: tuple[str, str]) -> list[Result]:
    out: list[Result] = []
    for agg, rows in sorted(_group(cands, lambda r: r.aggregation).items(), key=lambda kv: _AGG_ORDER.get(kv[0], 9)):
        same = [r for r in rows if place_of(r) == place]
        out.append((same or rows)[0])
    return out


def _calc(item: _JvItem) -> Decimal:
    return effective(item.voc) * effective(item.jsc) * effective(item.ff) / item.pin  # type: ignore[operator]


def _disc(calc: Decimal, pce: Result) -> Decimal | None:
    p = effective(pce)
    if p is None or p <= 0:
        return None
    return abs(calc - p) / p


def _rule_jv(ctx: _Ctx) -> None:
    if not ctx.on("jv_consistency"):
        return
    p = ctx.params("jv_consistency")
    thr = _d(p.get("threshold_pct"), "3") / HUNDRED
    rounding_on = ctx.on("rounding")
    items = _jv_items(ctx, bool(p.get("check_mean", False)))

    # предпочтительная агрегация для троек без агрегации и с несколькими PCE (D004)
    votes: dict[str | None, int] = defaultdict(int)
    for it in items:
        if len(it.candidates) > 1:
            calc = _calc(it)
            for c in it.candidates:
                d = _disc(calc, c)
                if d is not None and d <= thr:
                    votes[c.aggregation] += 1
    preferred = max(votes, key=lambda a: (votes[a], -_AGG_ORDER.get(a, 9))) if votes else None

    for it in items:
        calc = _calc(it)
        if len(it.candidates) == 1:
            main = it.candidates[0]
        else:
            pref = [c for c in it.candidates if c.aggregation == preferred]
            if pref:
                main = pref[0]
            else:
                discs = [(_disc(calc, c), c) for c in it.candidates]
                if any(d is not None and d <= thr for d, _ in discs):
                    continue
                main = min(it.candidates, key=lambda c: (_disc(calc, c) or Decimal(10**9)))
        others = [c for c in it.candidates if c is not main]
        disc = _disc(calc, main)
        if disc is None or disc <= thr:
            continue
        pce = effective(main)
        rows = [main, it.voc, it.jsc, it.ff, *others]
        numbers = {"calc": _f(calc, 4), "pce": _f(pce), "diff_pct": _f(disc * 100, 2),  # type: ignore[arg-type]
                   "pin": _f(it.pin, 3), "pin_default": it.pin_default, "threshold_pct": _f(thr * 100, 3),
                   "aggregation": main.aggregation}
        pin_txt = "" if it.pin == HUNDRED else f" при Pin = {fmt(it.pin.normalize())} мВт/см2"
        rel = [(rel_half_ulp(r), r) for r in (it.voc, it.jsc, it.ff, main)]
        rss = sum((x * x for x, _ in rel), ZERO).sqrt()
        if rounding_on and disc <= rss:
            coarse = max(rel[:3], key=lambda t: t[0])[1]
            numbers["rounding_rel_pct"] = _f(rss * 100, 2)
            ctx.emit("rounding", rows,
                     f"PCE {fmt(pce)} % и Voc·Jsc·FF/Pin = {fmt_q(calc)} %{pin_txt} расходятся на "
                     f"{fmt_q(disc * 100, 1)} % из-за округления: {coarse.metric_name} {fmt(effective(coarse))}"
                     f"{_unit(coarse)} указан с точностью до последнего знака (погрешность округления до "
                     f"{fmt_q(rss * 100, 1)} %)", numbers)
            continue
        extra = ""
        if others:
            extra = "; другие КПД прибора: " + ", ".join(
                f"{fmt(effective(o))} % ({_AGG_RU.get(o.aggregation, o.aggregation)})" for o in others)
        ctx.emit("jv_consistency", rows,
                 f"PCE {fmt(pce)} % не согласуется с Voc·Jsc·FF/Pin = {fmt_q(calc)} %{pin_txt} "
                 f"(расхождение {fmt_q(disc * 100, 1)} %){extra}", numbers)


# --- voc_limit -------------------------------------------------------------------------------------------------------

def _rule_voc_limit(ctx: _Ctx) -> None:
    if not ctx.on("voc_limit"):
        return
    for r in ctx.idx.main:
        if r.metric_name != "Voc":
            continue
        eg = ctx.idx.bandgap_ev(r)
        v = effective(r)
        if eg is None or v is None:
            continue
        if v >= eg:
            ctx.emit("voc_limit", [r], f"Voc {fmt(v)} В не меньше ширины запрещённой зоны поглотителя "
                                       f"Eg/q = {fmt(eg)} В", {"voc": _f(v), "eg": _f(eg)})


# --- range -----------------------------------------------------------------------------------------------------------

def _range_violation(r: Result, v: Decimal, idx: PaperIndex) -> str | None:
    m = r.metric_name
    if m == "PCE" and (v <= 0 or v >= 100):
        return "(0; 100) %"
    if m == "FF" and (v <= 0 or v > 100):
        return "(0; 100] %"
    if m == "Jsc" and (v <= 0 or v > 50):
        return "(0; 50] mA/cm2"
    if m == "Voc":
        if v <= 0:
            return "(0; 3] В"
        dev = idx.device(r)
        dtype = dev.device_type if dev is not None else None
        if v > 3 and dtype in (None, "cell"):
            return "(0; 3] В для ячейки"
    if m in ("Rs", "Rsh") and v <= 0:
        return "(0; +∞) Ohm*cm2"
    if m == "retained_PCE" and v > 150:
        return "не больше 150 %"
    return None


def _rule_range(ctx: _Ctx) -> None:
    if not ctx.on("range"):
        return
    for r in ctx.idx.live:
        if r.duplicate_of:
            continue
        v = effective(r)
        why = _range_violation(r, v, ctx.idx)  # type: ignore[arg-type]
        if why:
            ctx.emit("range", [r], f"{r.metric_name} = {fmt(v)}{_unit(r)} вне физических пределов: {why}",
                     {"value": _f(v)})  # type: ignore[arg-type]


# --- eqe_consistency -------------------------------------------------------------------------------------------------

def _rule_eqe(ctx: _Ctx) -> None:
    if not ctx.on("eqe_consistency"):
        return
    thr = _d(ctx.params("eqe_consistency").get("threshold_pct"), "5") / HUNDRED
    main = [r for r in ctx.idx.main if r.device_id]
    jsc_by_dev = _group([r for r in main if r.metric_name == "Jsc"], lambda r: r.device_id)
    for e in main:
        if e.metric_name != "Jsc_EQE":
            continue
        cands = jsc_by_dev.get(e.device_id, [])
        if not cands:
            continue
        same = [c for c in cands if c.aggregation == e.aggregation]
        ve = effective(e)
        pool = [c for c in (same or cands) if (effective(c) or ZERO) > 0]
        if not pool or ve is None:
            continue
        jsc = min(pool, key=lambda c: abs(ve - effective(c)) / effective(c))  # type: ignore[operator]
        vj = effective(jsc)
        disc = abs(ve - vj) / vj  # type: ignore[operator]
        if disc > thr:
            ctx.emit("eqe_consistency", [jsc, e],
                     f"Jsc по ВАХ {fmt(vj)} и по EQE {fmt(ve)} mA/cm2 расходятся больше чем на "
                     f"{fmt((thr * 100).normalize())} % (расхождение {fmt_q(disc * 100, 1)} %)",
                     {"jsc": _f(vj), "jsc_eqe": _f(ve), "diff_pct": _f(disc * 100, 2)})  # type: ignore[arg-type]


# --- champion_vs_mean ------------------------------------------------------------------------------------------------

def _rule_champion(ctx: _Ctx) -> None:
    if not ctx.on("champion_vs_mean"):
        return
    rows = [r for r in ctx.idx.main if r.device_id and r.metric_name in ("PCE", "stabilized_PCE")]
    groups = _group(rows, lambda r: (r.device_id, r.measurement_id, r.metric_name, r.time_point))
    for _k, grp in sorted(groups.items(), key=lambda kv: ctx.idx.rank(kv[1][0].result_id)):
        champs = [r for r in grp if r.aggregation == "champion"]
        means = [r for r in grp if r.aggregation in ("mean", "median")]
        for c in champs:
            for m in means:
                vc, vm = effective(c), effective(m)
                if vc < vm:  # type: ignore[operator]
                    word = "среднее" if m.aggregation == "mean" else "медиана"
                    ctx.emit("champion_vs_mean", [c, m],
                             f"{word} {fmt(vm)}{_unit(m)} больше значения лучшего прибора {fmt(vc)}{_unit(c)} "
                             f"({c.metric_name})", {"champion": _f(vc), "mean": _f(vm)})  # type: ignore[arg-type]


# --- hysteresis_index ------------------------------------------------------------------------------------------------

def _rule_hysteresis(ctx: _Ctx) -> None:
    if not ctx.on("hysteresis_index"):
        return
    idx = ctx.idx
    thr = _d(ctx.params("hysteresis_index").get("threshold_pp"), "1")

    def scan(r: Result) -> str | None:
        ms = idx.measurement(r)
        return (ms.scan_direction or None) if ms is not None else None

    pce = [r for r in idx.main if r.device_id and r.metric_name == "PCE"]
    his = _group([r for r in idx.main if r.device_id and r.metric_name == "HI"], lambda r: r.device_id)
    groups = _group(pce, lambda r: (r.device_id, r.aggregation, r.time_point))
    for (dev, _agg, _t), grp in sorted(groups.items(), key=lambda kv: idx.rank(kv[1][0].result_id)):
        rev = [r for r in grp if scan(r) == "reverse"]
        fwd = [r for r in grp if scan(r) == "forward"]
        if not rev or not fwd:
            continue
        rv, fw = rev[0], next((f for f in fwd if f.measurement_id != rev[0].measurement_id), None)
        if fw is None:
            continue
        vr, vf = effective(rv), effective(fw)
        if vr is None or vf is None or vr <= 0:
            continue
        hi = (vr - vf) / vr * HUNDRED
        stated = his.get(dev, [])
        st = next((h for h in stated if h.measurement_id == rv.measurement_id), stated[0] if stated else None)
        base = f"Индекс гистерезиса по КПД (PCE_rev {fmt(vr)} − PCE_fwd {fmt(vf)})/PCE_rev = {fmt_q(hi, 1)} %"
        numbers: dict[str, Any] = {"pce_rev": _f(vr), "pce_fwd": _f(vf), "hi_calc": _f(hi, 3)}
        if st is not None:
            vs = effective(st)
            numbers["hi_stated"] = _f(vs)  # type: ignore[arg-type]
            if abs(hi - vs) > thr:  # type: ignore[operator]
                ctx.emit("hysteresis_index", [rv, fw, st],
                         f"{base}, а в статье HI = {fmt(vs)} % (расхождение больше {fmt(thr)} п.п.)", numbers)
                continue
            if vs < 0 and hi >= 0:
                ctx.emit("hysteresis_index", [rv, fw, st],
                         f"{base}, а в статье HI = {fmt(vs)} % — отрицательный индекс гистерезиса", numbers)
                continue
        if hi < 0:
            ctx.emit("hysteresis_index", [rv, fw],
                     f"{base}: отрицательный — прямой ход ({fmt(vf)} %) лучше обратного ({fmt(vr)} %)", numbers)


# --- contradiction / rounding / figure_mismatch по местам статьи ----------------------------------------------------

def _place_label(r: Result) -> str:
    t, f = place_of(r)
    if t:
        return t
    if f:
        return f
    page = r.source_page
    return f"текст, стр. {page}" if page not in (None, "") else "текст"


def _related(a: Result, b: Result) -> bool:
    if a.duplicate_of == b.result_id or b.duplicate_of == a.result_id:
        return True
    if a.duplicate_of or b.duplicate_of:
        # повтор сравнивается только со своей основной записью и другими её повторами
        return bool(a.duplicate_of) and a.duplicate_of == b.duplicate_of
    return place_of(a) != place_of(b)


def _rule_places(ctx: _Ctx) -> None:
    con_on, fm_on, round_on = ctx.on("contradiction"), ctx.on("figure_mismatch"), ctx.on("rounding")
    if not (con_on or fm_on):
        return
    idx = ctx.idx
    fm_thr = _d(ctx.params("figure_mismatch").get("threshold_pct"), "3") / HUNDRED
    rows = [r for r in idx.live if r.device_id and r.result_origin != "cited"]
    groups = _group(rows, lambda r: (r.device_id, r.measurement_id, r.metric_name, r.aggregation, r.time_point))
    # пары собираются по (прибор, измерение, агрегация, момент) через все показатели
    found: dict[tuple[str, Any], list[tuple[Result, Result]]] = defaultdict(list)
    for (dev, meas, _m, agg, tp), grp in groups.items():
        if len(grp) < 2:
            continue
        for i, a in enumerate(grp):
            for b in grp[i + 1:]:
                if not _related(a, b):
                    continue
                va, vb = effective(a), effective(b)
                if va == vb:
                    continue
                fig_pair = (is_table_row(a) and is_figure_only(b)) or (is_table_row(b) and is_figure_only(a))
                if rounding_explains(a, va, b, vb):  # type: ignore[arg-type]
                    if round_on:
                        found[("rounding", (dev, meas, agg, tp))].append((a, b))
                    continue
                base = va if is_table_row(a) or not is_table_row(b) else vb
                disc = abs(va - vb) / abs(base) if base else None  # type: ignore[operator]
                if fig_pair and disc is not None and disc <= fm_thr:
                    continue  # точность чтения графика
                if con_on:
                    found[("contradiction", (dev, meas, agg, tp))].append((a, b))
                elif fm_on and fig_pair:
                    found[("figure_mismatch", (dev, meas, agg, tp))].append((a, b))

    def first_rank(pairs: list[tuple[Result, Result]]) -> int:
        return min(min(idx.rank(a.result_id), idx.rank(b.result_id)) for a, b in pairs)

    for (check, (dev, _meas, agg, _tp)), pairs in sorted(found.items(), key=lambda kv: (
            BACKEND_RULES.index(kv[0][0]), first_rank(kv[1]))):
        members: list[Result] = []
        for a, b in pairs:
            members += [a, b]
        members = sorted({r.result_id: r for r in members}.values(),
                         key=lambda r: (bool(r.duplicate_of), not is_table_row(r), idx.rank(r.result_id)))
        label = idx.device_label(members[0]) or dev
        parts = []
        pnums = []
        for a, b in sorted(pairs, key=lambda p: (idx.rank(p[0].result_id), idx.rank(p[1].result_id))):
            if is_table_row(b) and not is_table_row(a):
                a, b = b, a
            parts.append(f"{a.metric_name} {fmt(effective(a))}{_unit(a)} ({_place_label(a)}) и "
                         f"{fmt(effective(b))}{_unit(b)} ({_place_label(b)})")
            pnums.append({"metric": a.metric_name, "a": a.result_id, "va": _f(effective(a)),  # type: ignore[arg-type]
                          "b": b.result_id, "vb": _f(effective(b))})  # type: ignore[arg-type]
        agg_txt = f", {_AGG_RU.get(agg, agg)}" if agg else ""
        if check == "rounding":
            desc = f"Расхождение из-за округления (прибор {label}{agg_txt}): " + "; ".join(parts)
        elif check == "figure_mismatch":
            desc = (f"Значения на рисунке не совпадают с таблицей (прибор {label}{agg_txt}, расхождение больше "
                    f"{fmt((fm_thr * 100).normalize())} %): " + "; ".join(parts))
        else:
            desc = f"Значения прибора {label}{agg_txt} в разных местах статьи расходятся: " + "; ".join(parts)
        ctx.emit(check, members, desc, {"pairs": pnums})


# --- unit_inconsistency ----------------------------------------------------------------------------------------------

def _rule_unit(ctx: _Ctx) -> None:
    if not ctx.on("unit_inconsistency"):
        return
    idx = ctx.idx
    for r in idx.sorted(idx.bundle.results):
        if is_rejected(r) or r.duplicate_of or not r.metric_name:
            continue
        if str(r.meta.norm_issue or "") == "unit_inconsistency":
            raw = " ".join(x for x in (r.raw_value, r.raw_unit) if x)
            ctx.emit("unit_inconsistency", [r],
                     f"Единица {r.metric_name} не согласуется со значением или не распознана: «{raw}» "
                     f"(ожидается {TARGET_UNITS.get(r.metric_name, 'целевая единица')})", {"raw": raw})
            continue
        v = effective(r)
        if r.metric_name == "FF" and v is not None and 0 < v <= 1 and (r.normalized_unit in (None, "%")):
            ctx.emit("unit_inconsistency", [r],
                     f"FF = {fmt(v)} % — по величине это доля (≈ {fmt_q(v * HUNDRED, 1)} %), а не проценты",
                     {"value": _f(v)})


# --- voc_deficit_consistency -----------------------------------------------------------------------------------------

def _rule_voc_deficit(ctx: _Ctx) -> None:
    if not ctx.on("voc_deficit_consistency"):
        return
    idx = ctx.idx
    p = ctx.params("voc_deficit_consistency")
    min_tol = _d(p.get("tolerance_v"), "0.001")
    vocs = _group([r for r in idx.main if r.device_id and r.metric_name == "Voc"], lambda r: r.device_id)
    for d in idx.main:
        if d.metric_name != "Voc_deficit" or not d.device_id:
            continue
        cands = vocs.get(d.device_id, [])
        if not cands:
            continue
        cands = sorted(cands, key=lambda c: (c.aggregation != d.aggregation, c.measurement_id != d.measurement_id,
                                             idx.rank(c.result_id)))
        voc = cands[0]
        eg = idx.bandgap_ev(d) or idx.bandgap_ev(voc)
        vv, vd = effective(voc), effective(d)
        if eg is None or vv is None or vd is None:
            continue
        calc = eg - vv
        mat = idx.material(d) or idx.material(voc)
        eg_tol = Decimal(1).scaleb(int(eg.as_tuple().exponent)) / 2 if mat is not None else ZERO
        tol = max(min_tol, half_ulp(voc) + half_ulp(d) + eg_tol)
        if abs(calc - vd) > tol:
            ctx.emit("voc_deficit_consistency", [voc, d],
                     f"Eg/q − Voc = {fmt(eg)} − {fmt(vv)} = {fmt(calc)} В, а в статье дефицит Voc {fmt(vd)} В",
                     {"eg": _f(eg), "voc": _f(vv), "calc": _f(calc), "stated": _f(vd)})


# --- missing_conditions (scope=paper) --------------------------------------------------------------------------------

def _has_intensity(m: Measurement) -> bool:
    """Интенсивность указана числом или словами в поле спектра («1 sun»)."""
    return m.light_intensity is not None or "sun" in (m.spectrum or "").lower()


def _rule_missing_conditions(ctx: _Ctx) -> None:
    if not ctx.on("missing_conditions"):
        return
    b = ctx.bundle
    if not b.results and not b.devices and not b.measurements:
        return
    if b.publication.in_scope is False:
        return
    problems: list[str] = []
    jv = [m for m in b.measurements if m.measurement_type == "J-V"]
    if not jv:
        problems.append("нет измерения ВАХ (J-V) со спектром и интенсивностью освещения")
    elif not any(m.spectrum and _has_intensity(m) for m in jv):
        miss = []
        if not any(m.spectrum for m in jv):
            miss.append("спектр")
        if not any(_has_intensity(m) for m in jv):
            miss.append("интенсивность освещения")
        if not miss:
            miss.append("спектр и интенсивность в одном измерении")
        problems.append("для ВАХ не указаны: " + ", ".join(miss))
    no_area = [d for d in b.devices if area_cm2(d) is None]
    if no_area:
        labels = [d.device_label or d.device_id for d in no_area]
        shown = ", ".join(labels[:5]) + (f" и ещё {len(labels) - 5}" if len(labels) > 5 else "")
        problems.append(f"не указана площадь приборов: {shown}")
    if problems:
        ctx.emit("missing_conditions", [], "Не указаны условия измерения: " + "; ".join(problems),
                 {"problems": problems})


# --- plausibility ----------------------------------------------------------------------------------------------------

def _rule_plausibility(ctx: _Ctx) -> None:
    if not ctx.on("plausibility"):
        return
    idx = ctx.idx
    overrides = ctx.params("plausibility").get("limits") or {}
    for r in idx.main:
        if r.metric_name not in ("PCE", "FF"):
            continue
        v = effective(r)
        if v is None or _range_violation(r, v, idx):
            continue
        mat = idx.material(r)
        cls = mat.material_class if mat is not None else None
        limits = dict(PLAUSIBILITY_LIMITS.get(cls or "", _PLAUS_DEFAULT))
        for k, x in (overrides.get(cls or "", {}) or {}).items():
            limits[k] = _d(x, str(limits.get(k, "100")))
        lim = limits.get(r.metric_name)
        if lim is not None and v > lim:
            who = f"для класса {cls}" if cls else "для тонкоплёночных элементов"
            ctx.emit("plausibility", [r],
                     f"{r.metric_name} = {fmt(v)} % выше уровня мировых рекордов {who} (порог {fmt(lim)} %)",
                     {"value": _f(v), "limit": _f(lim), "material_class": cls})


# --- dedup_trap ------------------------------------------------------------------------------------------------------

def _rule_dedup_trap(ctx: _Ctx) -> None:
    if not ctx.on("dedup_trap"):
        return
    idx = ctx.idx
    rows = [r for r in idx.main if r.device_id and r.metric_name in JV_METRICS]
    sets: dict[tuple, list[tuple[str, Result]]] = defaultdict(list)
    for (dev, _meas, agg), grp in _group(rows, lambda r: (r.device_id, r.measurement_id, r.aggregation)).items():
        vals = []
        pce_row = None
        for m in JV_METRICS:
            d = _distinct([r for r in grp if r.metric_name == m])
            if len(d) != 1:
                break
            vals.append(effective(d[0]))
            if m == "PCE":
                pce_row = d[0]
        else:
            sets[(agg, tuple(vals))].append((dev, pce_row))  # type: ignore[arg-type]
    for (agg, vals), members in sorted(sets.items(), key=lambda kv: min(idx.rank(p.result_id) for _, p in kv[1])):
        devs = sorted({d for d, _ in members})
        if len(devs) < 2:
            continue
        pces = sorted({p.result_id: p for _, p in members}.values(), key=lambda p: idx.rank(p.result_id))
        labels = ", ".join(idx.device_label(p) or p.device_id for p in pces)  # type: ignore[misc]
        v = dict(zip(JV_METRICS, vals))
        ctx.emit("dedup_trap", pces,
                 f"Одинаковые наборы Voc {fmt(v['Voc'])} В, Jsc {fmt(v['Jsc'])} mA/cm2, FF {fmt(v['FF'])} %, "
                 f"PCE {fmt(v['PCE'])} % у разных приборов ({labels}): проверьте, не один ли это прибор",
                 {"devices": devs})


# --- comparability (внутристатейное; в профиле solar выключено) ------------------------------------------------------

def _rule_comparability(ctx: _Ctx) -> None:
    if not ctx.on("comparability"):
        return
    idx = ctx.idx

    def ordered(rows: list[Result]) -> list[Result]:
        return sorted(rows, key=lambda r: (r.metric_name != "PCE", idx.rank(r.result_id)))

    for ms in ctx.bundle.measurements:
        rows = [r for r in idx.main if r.measurement_id == ms.measurement_id]
        if not rows or ms.measurement_type not in (None, "J-V", "MPP tracking"):
            continue
        pin, defaulted = pin_mw_cm2(ms)
        reasons = []
        if ms.spectrum and not is_am15(ms.spectrum) and "sun" not in ms.spectrum.lower():
            reasons.append(f"нестандартный источник света «{ms.spectrum}»")
        if pin is not None and not defaulted and pin > 0 and pin != HUNDRED:
            reasons.append(f"интенсивность {fmt(pin.normalize())} мВт/см2 вместо 100")
        if reasons:
            ctx.emit("comparability", ordered(rows),
                     "Результаты нельзя сравнивать с измерениями при стандартных условиях (AM1.5G, 100 мВт/см2): "
                     + "; ".join(reasons), {"measurement_id": ms.measurement_id, "reasons": reasons})
    for dev in ctx.bundle.devices:
        if dev.device_type not in ("minimodule", "module"):
            continue
        rows = [r for r in idx.main if r.device_id == dev.device_id]
        if rows:
            ctx.emit("comparability", ordered(rows),
                     f"Прибор {dev.device_label or dev.device_id} — {dev.device_type}: Voc и Jsc относятся ко всему "
                     f"модулю, сравнивать с ячейками нельзя", {"device_id": dev.device_id})


# --- ambiguity (диапазон вместо значения) ------------------------------------------------------------------------------

def _rule_ambiguity(ctx: _Ctx) -> None:
    if not ctx.on("ambiguity"):
        return
    idx = ctx.idx
    for r in idx.sorted(idx.bundle.results):
        if is_rejected(r) or r.duplicate_of or not r.metric_name:
            continue
        if str(r.meta.norm_issue or "") == "ambiguity":
            raw = " ".join(x for x in (r.raw_value, r.raw_unit) if x)
            ctx.emit("ambiguity", [r],
                     f"{r.metric_name}: в статье указан диапазон «{raw}», одно значение не выбрано — "
                     f"нормализованное значение оставлено пустым", {"raw": raw})


_RULES: tuple[Callable[[_Ctx], None], ...] = (
    _rule_jv, _rule_voc_limit, _rule_range, _rule_eqe, _rule_champion, _rule_hysteresis, _rule_places, _rule_unit,
    _rule_voc_deficit, _rule_missing_conditions, _rule_plausibility, _rule_dedup_trap, _rule_comparability,
    _rule_ambiguity,
)


def run_backend_rules(bundle: PaperBundle, rules: dict[str, RuleConfig], *,
                      quote: QuoteChecker | None = None) -> list[IssueRecord]:
    """Правила бэка по включённым RuleConfig. bundle не меняется; выход детерминирован."""
    if not rules or not any(rc.enabled for n, rc in rules.items() if n in BACKEND_RULES):
        return []
    ctx = _Ctx(bundle=bundle, idx=PaperIndex(bundle), rules=rules, quote=quote)
    for rule in _RULES:
        rule(ctx)
    return ctx.out
