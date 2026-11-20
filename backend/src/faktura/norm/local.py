"""LocalNormalizer: реализация протокола Normalizer (spec/06, план 5.1). Арифметика только Decimal.

Данные словарей - norm/data/*.json (синонимы метрик, единицы и множители, материалы, функции слоёв, спектры).
Правила:
  * не восстанавливаем неизвестное по догадке (spec/06 §6.4): нет единицы/неизвестная единица -> failed + unit_inconsistency;
  * диапазон не выбираем: failed + ambiguity;
  * spread после ± хранится в единицах статьи (не пересчитывается);
  * безразмерные нестандартные метрики без единицы (CRI, bifaciality_factor) - passthrough без замечаний.
"""
from __future__ import annotations

import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, localcontext
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..domain.ports import DictionarySnapshot, LayerFunction, NormPreview, NormResult, ParsedNumber
from ..domain.records import TraceStep

NORM_VERSION: str = "1.0.0"
DICTIONARY_VERSION: str = "solar-2025.1"

_DATA = Path(__file__).parent / "data"
_NUM = r"[-+]?(?:(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d+)?|[.,]\d+)"
_UNSIGNED = r"(?:(?:\d{1,3}(?: \d{3})+|\d+)(?:[.,]\d+)?|[.,]\d+)"
_SPACES = dict.fromkeys(map(ord, "       ​"), " ")
_MINUS = dict.fromkeys(map(ord, "−‒➖"), "-")
_PM = r"(?:±|\+/-|\+-|∓)"


@lru_cache(maxsize=None)
def _load(name: str) -> dict[str, Any]:
    with open(_DATA / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


def _factor(s: str) -> tuple[int, int]:
    num, _, den = s.partition("/")
    return int(num), int(den or 1)


def _clean(d: Decimal) -> Decimal:
    """Убирает хвостовые нули и экспоненту: 71.00 -> 71, 1.2E+3 -> 1200."""
    with localcontext() as ctx:
        ctx.prec = 60
        d = d.normalize()
        if d.as_tuple().exponent > 0:  # type: ignore[operator]
            d = d.quantize(Decimal(1))
        return d


def _prep(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).translate(_SPACES).translate(_MINUS).strip()
    if re.match(r"[–—]\d", t):
        t = "-" + t[1:]
    return t


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(" ", "").replace(",", ".").lstrip("+"))


def _unit_key(u: str) -> str:
    k = unicodedata.normalize("NFKC", u).translate(_SPACES).translate(_MINUS)
    k = k.replace(" ", "").replace("Ω", "ohm").replace("ω", "ohm").replace("µ", "u").replace("μ", "u")
    for ch in ("·", "⋅", "×", "∙"):
        k = k.replace(ch, "*")
    return k.replace("^", "").strip(".")


class LocalNormalizer:
    version: str = NORM_VERSION
    dictionary_version: str = DICTIONARY_VERSION

    def __init__(self) -> None:
        mets = _load("metrics")["metrics"]
        self._metrics = {m["metric_name"]: m for m in mets}
        self._syn_exact: dict[str, str] = {}
        phrases: list[tuple[str, str]] = []
        for m in mets:
            for s in [m["metric_name"], *m["synonyms"]]:
                self._syn_exact[self._collapse(s)] = m["metric_name"]
                phrases.append((self._words(s), m["metric_name"]))
        phrases.sort(key=lambda p: -len(p[0]))
        self._phrases = [(re.compile(r"(?<![\w])" + re.escape(p) + r"(?![\w])"), name) for p, name in phrases]
        u = _load("units")
        self._groups = u["groups"]
        self._fields = u["fields"]
        self._field_aliases = u["field_aliases"]
        self._display = u["display"]
        self._unit_exact: dict[str, str] = dict(u["aliases"])
        self._unit_lower: dict[str, str] = {}
        for k, v in u["aliases"].items():
            self._unit_lower.setdefault(k.lower(), v)
        mat = _load("materials")
        self._mat_alias = mat["aliases"]
        self._mat_known = {k.casefold(): k for k in mat["known"]}
        self._cations = mat["cations"]
        lf = _load("layer_functions")
        self._vocab = {v.casefold(): v for v in lf["vocab"]}
        self._rules = [(r, [re.compile(p) for p in r["patterns"]]) for r in lf["rules"]]
        self._substrate = [re.compile(p) for p in lf["substrate_patterns"]]
        self._spectra = [(s["value"], [re.compile(p) for p in s["patterns"]]) for s in _load("spectra")["spectra"]]

    # ---------- числа ----------
    def parse_number(self, text: str) -> ParsedNumber:
        if text is None or not str(text).strip():
            return ParsedNumber(ok=False, note="пусто")
        t = _prep(str(text))
        m = re.match(rf"\s*({_NUM})", t)
        if not m:
            return ParsedNumber(ok=False, note="не число")
        try:
            value = _to_decimal(m.group(1))
        except Exception:
            return ParsedNumber(ok=False, note="не число")
        rest = t[m.end():]
        if re.match(rf"\s*[-–—]\s*{_UNSIGNED}", rest):
            return ParsedNumber(ok=False, is_range=True, note="диапазон")
        spread = None
        ms = re.match(rf"\s*{_PM}\s*({_UNSIGNED})", rest)
        if ms:
            spread = _to_decimal(ms.group(1))
            rest = rest[ms.end():]
        rest = rest.strip()
        is_percent = False
        if rest.startswith("%"):
            is_percent = True
            rest = rest[1:].strip()
        if rest and re.match(r"[\d.,+\-–—±]", rest):
            return ParsedNumber(ok=False, note="неразобранный хвост")
        return ParsedNumber(ok=True, value=value, spread=spread, is_percent=is_percent, rest=rest or None)

    # ---------- единицы ----------
    def _canon_unit(self, unit: str | None) -> str | None:
        if unit is None or not str(unit).strip():
            return None
        k = _unit_key(str(unit))
        if not k:
            return None
        if k in self._unit_exact:
            return self._unit_exact[k]
        return self._unit_lower.get(k.lower(), k)

    @staticmethod
    def _apply(value: Decimal, fac: tuple[int, int]) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = 60
            return _clean(value * fac[0] / fac[1])

    def _failed(self, trace: list[TraceStep], issue: str | None, note: str) -> NormResult:
        trace.append(TraceStep(step="Итог", note=note))
        return NormResult(norm_status="failed", issue=issue, trace=trace)  # type: ignore[arg-type]

    def _convert(self, group: dict[str, Any] | None, raw_value: str | None, raw_unit: str | None,
                 header_unit: str | None, *, fraction_scale: bool = False, ff_check: bool = False,
                 passthrough: bool = False) -> NormResult:
        trace: list[TraceStep] = []
        if raw_value is None or not str(raw_value).strip():
            return NormResult(norm_status="not_applicable", trace=trace)
        trace.append(TraceStep(step="В статье", before=str(raw_value), note=raw_unit or header_unit))
        p = self.parse_number(str(raw_value))
        if p.is_range:
            return self._failed(trace, "ambiguity", "диапазон: значение не выбирается")
        if not p.ok or p.value is None:
            return self._failed(trace, None, p.note or "не число")
        value = p.value
        trace.append(TraceStep(step="Число", before=str(raw_value), after=str(value),
                               note=f"± {p.spread}" if p.spread is not None else None))
        unit = self._canon_unit(raw_unit) or self._canon_unit(header_unit)
        if unit is None:
            if p.rest:
                unit = self._canon_unit(p.rest)
            elif p.is_percent:
                unit = "%"
        factors: dict[str, str] = group["factors"] if group else {}
        target = group.get("target") if group else None

        if passthrough:
            trace.append(TraceStep(step="Итог", after=str(value), note="безразмерная метрика, без пересчёта"))
            return NormResult(normalized_value=value, normalized_unit=unit, norm_status="passthrough",
                              spread_value=p.spread, trace=trace)
        if group is None:  # метрика вне словаря: значение как в статье
            trace.append(TraceStep(step="Итог", after=str(value), note="метрика вне словаря"))
            status = "passthrough" if unit is None else "ok"
            return NormResult(normalized_value=value, normalized_unit=unit, norm_status=status,
                              spread_value=p.spread, trace=trace)  # type: ignore[arg-type]

        issue: str | None = None
        if unit is None:
            if fraction_scale and Decimal(0) <= value <= 1:
                unit = "fraction"
                trace.append(TraceStep(step="Перевод", note="единицы нет, значение <= 1: доля"))
            elif fraction_scale and Decimal(1) < value <= Decimal(100):
                unit = target
                trace.append(TraceStep(step="Перевод", note="единицы нет, шкала процентов"))
            else:
                return self._failed(trace, "unit_inconsistency", "единица не указана")
        if unit not in factors:
            return self._failed(trace, "unit_inconsistency", f"единица «{unit}» не пересчитывается")
        if ff_check and unit == "%" and Decimal(0) <= value <= 1:
            unit = "fraction"
            issue = "unit_inconsistency"
            trace.append(TraceStep(step="Перевод", note="значение <= 1 с единицей %: нормализуем по шкале долей"))
        fac = _factor(factors[unit])
        if fac == (1, 1):
            out, status = value, "ok"
        else:
            out, status = self._apply(value, fac), "converted"
            trace.append(TraceStep(step="Перевод", before=f"{value} {unit}", after=f"{out} {target}",
                                   note=f"× {factors[unit]}"))
        trace.append(TraceStep(step="Итог", after=f"{out} {target}"))
        return NormResult(normalized_value=out, normalized_unit=target, norm_status=status,  # type: ignore[arg-type]
                          spread_value=p.spread, issue=issue, trace=trace)  # type: ignore[arg-type]

    def normalize(self, metric_name: str, raw_value: str | None, raw_unit: str | None,
                  header_unit: str | None = None) -> NormResult:
        """norm_status ok|converted|failed|not_applicable|passthrough; issue unit_inconsistency|ambiguity|None;
        spread_value - число после ± в единицах статьи."""
        name = self.match_metric(metric_name or "") or (metric_name or "").strip()
        m = self._metrics.get(name)
        if m is None:
            return self._convert(None, raw_value, raw_unit, header_unit)
        if m["group"] == "dimensionless":
            return self._convert(None, raw_value, raw_unit, header_unit, passthrough=True)
        return self._convert(self._groups[m["group"]], raw_value, raw_unit, header_unit,
                             fraction_scale=bool(m.get("fraction_scale")), ff_check=(name == "FF"))

    def normalize_field(self, table: str, column: str, raw_value: str | None, raw_unit: str | None) -> NormResult:
        """bandgap (eV), thickness (nm), area (cm2)."""
        key = f"{table}.{column}"
        if key not in self._fields:
            key = self._field_aliases.get(column, key)
        grp = self._fields.get(key)
        if grp is None:
            return NormResult(norm_status="not_applicable")
        return self._convert(grp, raw_value, raw_unit, None)

    # ---------- метрики ----------
    @staticmethod
    def _collapse(text: str) -> str:
        return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())

    @staticmethod
    def _words(text: str) -> str:
        t = unicodedata.normalize("NFKC", text).casefold().replace("_", "")
        return re.sub(r"\s+", " ", t).strip()

    def match_metric(self, text: str) -> str | None:
        if not text or not text.strip():
            return None
        c = self._collapse(text)
        if c in self._syn_exact:
            return self._syn_exact[c]
        w = self._words(text)
        for rx, name in self._phrases:
            if rx.search(w):
                return name
        return None

    # ---------- материалы ----------
    def canon_material(self, text: str) -> str | None:
        if not text or not text.strip():
            return None
        t = unicodedata.normalize("NFKC", text).strip()
        key = re.sub(r"\s+", "", t).casefold()
        if key in self._mat_alias:
            return self._mat_alias[key]
        if key in self._mat_known:
            return self._mat_known[key]
        m = re.match(r"^(MA|FA)((?:Pb|Sn)(?:[A-Z][a-z]?\d*(?:\.\d+)?)+)$", t)
        if m:
            return self._cations[m.group(1)] + m.group(2)
        if re.fullmatch(r"[A-Z][A-Za-z0-9().,:\-/]*", t):
            return t
        return None

    # ---------- слои ----------
    def layer_function(self, text: str, position: int | None = None) -> LayerFunction:
        if not text or not text.strip():
            return LayerFunction()
        t = unicodedata.normalize("NFKC", text).strip().casefold()
        if t in self._vocab:
            return LayerFunction(value=self._vocab[t])
        for rule, pats in self._rules:
            if any(p.search(t) for p in pats):
                note = f"исходно: {text.strip()}" if rule.get("note") else None
                return LayerFunction(value=rule["function"], note=note)
        return LayerFunction()  # подложка и неизвестное: в layers не пишем

    # ---------- условия ----------
    def normalize_encapsulation(self, value: object) -> tuple[bool | None, str | None]:
        if value is None:
            return None, None
        if isinstance(value, bool):
            return value, None
        s = str(value).strip()
        if not s:
            return None, None
        low = unicodedata.normalize("NFKC", s).casefold()
        if re.fullmatch(r"(false|no|нет|0)", low) or re.search(
                r"un-?encapsulat|non-?encapsulat|not encapsulat|without encapsulation|no encapsulation|"
                r"без инкапсуляц|не инкапсулир|неинкапсулир", low):
            return False, None
        if re.fullmatch(r"(true|yes|да|1|encapsulated|инкапсулирован\w*)", low):
            return True, None
        return None, f"исходно: {s}"

    def normalize_spectrum(self, text: str | None) -> str | None:
        if not text or not str(text).strip():
            return None
        low = unicodedata.normalize("NFKC", str(text)).casefold()
        for value, pats in self._spectra:
            if any(p.search(low) for p in pats):
                return value
        return None

    # ---------- показ ----------
    def format_display(self, value: Decimal | None, unit: str | None, *, digits: int | None = None) -> str:
        """Decimal('1.1'), 'V', digits=2 -> '1,10 В'."""
        if value is None:
            return "—"
        with localcontext() as ctx:
            ctx.prec = 60
            d = Decimal(value)
            if digits is not None:
                d = d.quantize(Decimal(1).scaleb(-max(digits, 0)), rounding=ROUND_HALF_UP)
        s = format(d, "f").replace(".", ",")
        if unit is None or not str(unit).strip():
            return s
        cu = self._canon_unit(unit) or str(unit)
        return f"{s} {self._display.get(cu, cu)}"

    def preview(self, collection: str, param: str, raw: str) -> NormPreview:
        p = self.parse_number(raw or "")
        if not p.ok:
            return NormPreview(ok=False)
        key = self._field_aliases.get(param)
        if key:
            table, column = key.split(".")
            r = self.normalize_field(table, column, raw, None)
        else:
            r = self.normalize(param, raw, None)
        if r.norm_status in ("ok", "converted", "passthrough") and r.normalized_value is not None:
            return NormPreview(ok=True, value=r.normalized_value, unit=r.normalized_unit,
                               text=self.format_display(r.normalized_value, r.normalized_unit))
        return NormPreview(ok=False)

    # ---------- словарь ----------
    def dictionary(self) -> DictionarySnapshot:
        metrics = [{"metric_name": m["metric_name"], "synonyms": list(m["synonyms"]), "group": m["group"],
                    "target_unit": (self._groups[m["group"]].get("target")), "evaluated": bool(m.get("evaluated"))}
                   for m in _load("metrics")["metrics"]]
        units: list[dict[str, Any]] = []
        for gname, g in self._groups.items():
            units.append({"group": gname, "target": g.get("target"), "factors": dict(g["factors"])})
        for fname, g in self._fields.items():
            units.append({"field": fname, "target": g.get("target"), "factors": dict(g["factors"])})
        mat = _load("materials")
        materials = [{"alias": k, "formula": v} for k, v in sorted(mat["aliases"].items())]
        materials += [{"formula": k} for k in mat["known"]]
        lf = _load("layer_functions")
        layer_functions = [{"id": r["id"], "layer_function": r["function"], "patterns": list(r["patterns"]),
                            "note": bool(r.get("note"))} for r in lf["rules"]]
        spectra = [{"value": s["value"], "patterns": list(s["patterns"])} for s in _load("spectra")["spectra"]]
        return DictionarySnapshot(version=DICTIONARY_VERSION, metrics=metrics, units=units, materials=materials,
                                  layer_functions=layer_functions, spectra=spectra)
