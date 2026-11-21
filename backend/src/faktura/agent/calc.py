"""Калькулятор и справочник правил агента: безопасное вычисление арифметики и описания проверок."""
from __future__ import annotations

import ast
import json
import operator
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_EXPR = 200
MAX_NAMES = 12
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,30}$")


class CalcError(ValueError):
    pass


def safe_eval(expr: str, values: dict[str, float]) -> float:
    """Только числа, имена из `values`, + - * / и скобки. Вызовы, атрибуты, степени, индексы и прочее - ошибка."""
    expr = (expr or "").strip()
    if not expr or len(expr) > MAX_EXPR:
        raise CalcError(f"expr: non-empty arithmetic expression up to {MAX_EXPR} chars")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise CalcError(f"syntax error in expr: {e.msg}") from None

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return float(n.value)
        if isinstance(n, ast.Name):
            if n.id not in values:
                raise CalcError(f"name {n.id!r} is not in inputs")
            return values[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Div) and b == 0:
                raise CalcError("division by zero")
            return _BIN[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UN:
            return _UN[type(n.op)](ev(n.operand))
        raise CalcError(f"not allowed in expr: {type(n).__name__} (only numbers, input names, + - * / and parentheses)")

    out = ev(tree)
    if out != out or out in (float("inf"), float("-inf")):
        raise CalcError("result is not a finite number")
    return out


# --- справочник правил проверок -------------------------------------------------------------------------------------

THRESHOLDS = {
    "jv_consistency": "порог расхождения 3 %; Pin = 100 мВт/см² (1 солнце), если в статье не указано иное; "
                      "для средних (mean/median) правило по умолчанию не считается",
    "eqe_consistency": "порог расхождения 5 %",
    "hysteresis_index": "расхождение HI с указанным в статье > 1 п.п. или HI < 0",
    "figure_mismatch": "порог 3 %",
    "range": "КПД в (0; 100), FF в (0; 100], Jsc в (0; 50] мА/см², Voc в (0; 3] В, Rs и Rsh > 0",
}
FIX_HINTS = {
    "jv_consistency": "Типичные причины: FF в долях вместо % (0,71 вместо 71) или Voc в мВ вместо В; значения от разных приборов "
                      "или разных измерений; среднее смешано с лучшим; опечатка в одной цифре.",
    "unit_inconsistency": "Единица не совпадает со значением (доля подписана как %, мВ как В): правится единица или шкала.",
    "champion_vs_mean": "Лучший прибор не может быть хуже среднего: вероятно, значения перепутаны или относятся к разным приборам.",
    "hysteresis_index": "HI = (PCE_rev − PCE_fwd) / PCE_rev; HI < 0 бывает при перепутанных направлениях развёртки.",
}


@lru_cache(maxsize=4)
def _passport_rules(domain: str) -> list[dict]:
    path = Path(__file__).resolve().parent.parent / "passport" / "data" / f"{domain}.json"
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("rules") or [])
    except (OSError, ValueError):
        return []


def rules_for(domain: str, check: str | None = None) -> dict[str, Any]:
    """Определения проверок области: id (= check_name замечания), название, описание, уровень, формула, пороги."""
    from ..passport.build import RULE_FORMULAS  # noqa: PLC0415

    out = []
    for r in _passport_rules(domain):
        rid = str(r.get("id") or "")
        if re.fullmatch(r"f\d+", rid):  # правила оформления набора, а не проверки значений
            continue
        if check and check.lower() not in rid.lower():
            continue
        item = {"check_name": rid, "title": r.get("title", ""), "description": r.get("description", ""),
                "level": r.get("level", "")}
        for k, src in (("formula", RULE_FORMULAS), ("thresholds", THRESHOLDS), ("typical_fix", FIX_HINTS)):
            if rid in src:
                item[k] = src[rid]
        out.append(item)
    return {"domain": domain, "rules": out}
