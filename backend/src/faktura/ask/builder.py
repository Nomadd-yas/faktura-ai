"""Общие помощники ответа: подписи по-русски, числа, сборщик карточек и сносок CaseAnswer."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

METRIC_RU = {"PCE": "КПД", "Voc": "Voc", "Jsc": "Jsc", "FF": "FF", "Rs": "Rs", "Rsh": "Rsh", "HI": "индекс гистерезиса",
             "Jsc_EQE": "Jsc по EQE", "stabilized_PCE": "стабилизированный КПД", "T80": "время до 80 % КПД",
             "retained_PCE": "сохранённый КПД"}
UNIT_RU = {"%": "%", "V": "В", "mV": "мВ", "mA/cm2": "мА/см²", "Ohm*cm2": "Ом·см²", "Ohm cm2": "Ом·см²", "h": "ч",
           "min": "мин", "eV": "эВ", "nm": "нм", "fraction": "доля"}
CHECK_RU = {"jv_consistency": "КПД не сходится с Voc·Jsc·FF", "voc_limit": "Voc выше ширины зоны",
            "range": "значение вне допустимого диапазона", "eqe_consistency": "Jsc по EQE расходится с ВАХ",
            "champion_vs_mean": "лучший прибор хуже среднего", "hysteresis_index": "гистерезис",
            "contradiction": "противоречие в статье", "ambiguity": "неясно из текста",
            "unit_inconsistency": "ошибка в единицах", "missing_conditions": "не указаны условия измерения",
            "typo": "опечатка", "figure_mismatch": "рисунок расходится с текстом", "figure_only": "значение только на рисунке",
            "dedup_trap": "повтор значения", "rounding": "округление", "plausibility": "подозрительное значение"}
CLASS_RU = {"perovskite": "перовскиты", "CIGS": "CIGS", "CdTe": "CdTe", "kesterite": "кестериты", "Sb2Se3": "Sb₂Se₃",
            "thin-film Si": "тонкоплёночный кремний", "other": "другое"}
LOWER_BETTER = {"Rs", "HI"}


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    return one if a == 1 and b != 11 else few if 2 <= a <= 4 and not 12 <= b <= 14 else many


def n_papers(n: int) -> str:
    return f"{n} {plural(n, 'статья', 'статьи', 'статей')}"


def in_papers(n: int) -> str:
    """«в 3 статьях»: число с формой предложного падежа (без предлога)."""
    return f"{n} {plural(n, 'статье', 'статьях', 'статьях')}"


def of_papers(n: int) -> str:
    return f"{n} {plural(n, 'статьи', 'статей', 'статей')}"


def n_values(n: int) -> str:
    return f"{n} {plural(n, 'значение', 'значения', 'значений')}"


def n_issues(n: int) -> str:
    return f"{n} {plural(n, 'замечание', 'замечания', 'замечаний')}"


def to_float(v: Any) -> float | None:
    try:
        f = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    return f if f == f and abs(f) != float("inf") else None


def fnum(v: float) -> str:
    """Число для текста: до 4 значащих знаков, десятичная запятая."""
    s = f"{v:.4g}"
    if "e" in s:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def unit_ru(u: str) -> str:
    return UNIT_RU.get(u, u)


def metric_label(m: str) -> str:
    return METRIC_RU.get(m, m)


def check_label(c: str) -> str:
    return CHECK_RU.get(c) or c.replace("_", " ")


def place_of(row: dict) -> str:
    parts = []
    if row.get("source_page"):
        parts.append(f"стр. {row['source_page']}")
    for k in ("source_table", "source_figure"):
        if row.get(k):
            parts.append(re.sub(r"(?i)\btable\s*", "табл. ", re.sub(r"(?i)\b(?:figure|fig\.?)\s*", "рис. ", row[k])))
    return " · ".join(parts)


@dataclass
class Executed:
    """Результат исполнителя: то, что станет CaseAnswer, и факты для автора текста."""
    intent: str
    text: str
    cards: list[dict] = field(default_factory=list)
    cites: list[dict] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    total: int = 0
    shown: int = 0


class Builder:
    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.cites: list[dict] = []
        self.cards: list[dict] = []

    def _add(self, key: tuple, cite: dict) -> str:
        for i, c in enumerate(self.cites):
            if c["_key"] == key:
                return f"[{i + 1}]"
        cite["_key"] = key
        if self.domain == "rag":
            cite["domain"] = "rag"
        self.cites.append(cite)
        return f"[{len(self.cites)}]"

    def cite_result(self, pid: str, row: dict, label: str) -> str:
        page = row.get("source_page") or None
        return self._add(("r", pid, row.get("result_id")), {"pid": pid, "rid": row.get("result_id"), "page": page,
                                                            "label": label})

    def cite_issue(self, pid: str, issue: dict) -> str:
        page = issue.get("source_page") or None
        label = " · ".join(x for x in (pid, check_label(issue.get("check_name") or ""),
                                       f"стр. {page}" if page else "") if x)
        return self._add(("i", pid, issue.get("issue_id")), {"pid": pid, "iid": issue.get("issue_id"), "page": page,
                                                             "label": label})

    def cite_row(self, pid: str, table: str, key: str, row: dict) -> str:
        page = row.get("source_page") or None
        label = " · ".join(x for x in (table, key, f"стр. {page}" if page else "") if x)
        return self._add(("t", pid, table, key), {
            "pid": pid, "rid": key if table == "results" else None, "iid": key if table == "issues" else None,
            "table": table, "row": key, "page": page, "label": label})

    def cites_out(self) -> list[dict]:
        out = []
        for c in self.cites:
            d = {k: v for k, v in c.items() if k != "_key" and v is not None}
            out.append(d)
        return out

    def done(self, intent: str, text: str, facts: dict | None = None, total: int = 0, shown: int = 0) -> Executed:
        return Executed(intent, text, self.cards, self.cites_out(), facts or {}, total, shown)
