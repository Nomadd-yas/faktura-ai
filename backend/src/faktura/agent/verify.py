"""Проверка ответа агента до отправки (§12.4): числа, маркеры, сноски.

Маркеры в тексте модели (наша схема; `fact_id` спеки = строка results, адресуется как `<pid>/<result_id>`):
  [[fact:D003/R010]]      значение (строка results);
  [[agg:a2]]              агрегат, выданный `group` в этом ответе;
  [[chunk:ID|цитата]]     фрагмент текста статьи (`search_text`/`read_page`); цитата необязательна, но если есть -
                          должна быть подстрокой текста фрагмента;
  [[page:D001/3|цитата]]  страница статьи, прочитанная `read_page`: цитата (обязательна) - подстрока текста страницы
                          (без учёта пробелов и регистра); числа из текста страницы допускаются, как у chunk;
  [[calc:c1]]             результат `calc` в этом ответе: число в тексте должно совпасть со значением с точностью округления;
  [[issue:D005/I03]]      замечание;
  [[row:devices/D001/DEV1]]  значение из другой таблицы (число должно стоять в ячейке строки).
Ответ режется на пункты (строки и предложения). Пункт с нарушением отбрасывается (`drop`) или помечается (`mark`).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from ..ask import solar
from ..ask.builder import to_float
from .tools import FACT_NUM_FIELDS, Toolbox

MARKER = re.compile(r"\[\[\s*(fact|agg|chunk|page|calc|issue|row)\s*:\s*([^\]|]+?)\s*(?:\|([^\]]*))?\]\]")
NUMBER = re.compile(r"\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?")
SENT = re.compile(r"(?<=[.!?;])\s+(?=[A-ZА-ЯЁ«\"(—-])")
FOOT = re.compile(r"\[\d+\]")
ORDINAL = re.compile(r"^(?:-?(?:й|я|е|го|му|м|ой|ей|ых|х|ю|ми)\b|(?:th|st|nd|rd)\b)", re.I)
REF_WORD = re.compile(r"(?:стр\.?|страниц[аеыу]?|с\.|табл(?:ица|ице|ицы|ицу|\.)?|рис(?:унок|унке|унка|\.)?|"
                      r"figure|fig\.?|table|page|pages|p\.|pp\.|раздел\w*|section|§|шаг\w*|step)\s*$", re.I)
LIST_ITEM = re.compile(r"^\s*(?:[-•*–]|\d+[.)])\s")
GAP = 40  # символов между числом и его маркером
PCT_UNITS = {"fraction", "ratio", "score", ""}


@dataclass
class Verified:
    text: str
    cites: list[dict]
    items_total: int = 0
    items_dropped: int = 0
    dropped: list[dict] = field(default_factory=list)
    used_facts: list[str] = field(default_factory=list)
    used_issues: list[str] = field(default_factory=list)
    used_aggs: list[str] = field(default_factory=list)
    used_calc_facts: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    @property
    def too_many_dropped(self) -> bool:
        return self.items_total > 0 and self.items_dropped * 2 > self.items_total


def _canon(s: str) -> float | None:
    return to_float(re.sub(r"[   ]", "", s))


def _decimals(s: str) -> int:
    m = re.search(r"[.,](\d+)$", s)
    return len(m.group(1)) if m else 0


def _close(w: float, d: int, v: float) -> bool:
    """Число текста w (d знаков) совпадает со значением v с точностью округления."""
    return abs(v - w) <= 0.5 * 10 ** (-d) + 1e-9 * max(1.0, abs(v))


def _value_match(w: float, d: int, pct: bool, v: float, unit: str) -> bool:
    if _close(w, d, v):
        return True
    return unit in PCT_UNITS and pct and 0 <= abs(v) <= 1 and _close(w, d, v * 100)  # 0,74 доли = 74 %


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


_WRAP = "\"'«»“”„‘’`"
_TYPO = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "‘": "'", "’": "'", "–": "-", "—": "-",
                       "\u00ad": None})


BARE_LIST_ITEM = re.compile(r"\s*(?:\d+[.)]|[-*•])\s*")
NUMBERED = re.compile(r"^(\s*)\d+([.)]\s)")


def _renumber(lines: list[str]) -> list[str]:
    """После отброшенных пунктов нумерация списка снова подряд: 1, 2, 3 (каждый список - с 1)."""
    out, n = [], 0
    for ln in lines:
        m = NUMBERED.match(ln)
        if m:
            n += 1
            ln = f"{m.group(1)}{n}{m.group(2)}{ln[m.end():]}"
        else:
            n = 0
        out.append(ln)
    return out


def clean_quote(q: str | None) -> str | None:
    """Цитата из маркера без обрамляющих кавычек и многоточий: модель пишет `|"текст"` или `|«текст…»`."""
    if q is None:
        return None
    t = q.strip()
    while t and (t[0] in _WRAP or t[-1] in _WRAP or t.endswith("…") or t.endswith("...")):
        t = t.strip(_WRAP).removesuffix("…").removesuffix("...").strip()
    return t


def _canon_text(s: str) -> str:
    t = unicodedata.normalize("NFKC", s or "").translate(_TYPO)
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)  # перенос на строке: «distri- bution»
    return re.sub(r"\s+", " ", t).strip().lower()


def quote_in(quote: str, text: str) -> bool:
    """Цитата есть в тексте: без учёта пробелов, регистра, типографики и переносов; иначе - по буквам и цифрам
    (разметка OCR, пунктуация). Конечная точка цитаты не обязательна."""
    q = _canon_text(clean_quote(quote) or "").rstrip(".,;:!?")
    if not q:
        return False
    t = _canon_text(text)
    if q in t:
        return True
    alnum = re.compile(r"[\W_]+")
    qa = alnum.sub("", q)
    return len(qa) >= 8 and qa in alnum.sub("", t)


def _nums_in(text: str) -> list[float]:
    return [x for x in (_canon(m.group(0)) for m in NUMBER.finditer(text)) if x is not None]


def _page(p: Any) -> Any:
    s = str(p or "").strip()
    return int(s) if s.isdigit() else (s or None)


def _mask(text: str) -> str:
    return MARKER.sub(lambda m: "x" * len(m.group(0)), text)


def split_items(line: str) -> list[str]:
    """Предложения строки; маркеры (в них бывают точки) не режутся."""
    cuts = [m.start() for m in SENT.finditer(_mask(line))]
    out, last = [], 0
    for c in cuts:
        out.append(line[last:c])
        last = c
    out.append(line[last:])
    return [x.strip() for x in out if x.strip()]


def number_spans(item: str, question_nums: set[float]) -> list[tuple[int, int, str, bool]]:
    """(начало, конец, текст, со знаком %) чисел пункта, которым нужен маркер: без годов, номеров страниц, таблиц,
    рисунков, порядковых, чисел из вопроса, кодов (D001, F1, Recall@5, GPT-4)."""
    masked = FOOT.sub(lambda m: " " * len(m.group(0)), _blank_markers(item))
    out = []
    for m in NUMBER.finditer(masked):
        s, e = m.span()
        txt = m.group(0)
        prev = masked[s - 1] if s else ""
        if prev and (prev.isalnum() or prev in "_@/:"):
            continue
        if prev in ("-", "–", "−") and s >= 2 and masked[s - 2].isalnum():
            continue
        tail = masked[e:e + 8]
        pct = bool(re.match(r"\s*(%|проц)", tail))
        if re.fullmatch(r"(?:19|20)\d\d", txt) and not pct:
            continue
        if REF_WORD.search(masked[max(0, s - 14):s]) or ORDINAL.match(tail):
            continue
        if not masked[:s].strip(" \t-•*") and re.match(r"[.)]\s", tail + " "):  # нумерация списка «1.» «2)»
            continue
        v = _canon(txt)
        if v is not None and any(abs(v - q) < 1e-9 for q in question_nums):
            continue
        out.append((s, e, txt, pct))
    return out


def _blank_markers(item: str) -> str:
    return MARKER.sub(lambda m: " " * len(m.group(0)), item)


def find_markers(item: str) -> list[tuple[int, int, str, str, str | None]]:
    return [(m.start(), m.end(), m.group(1), m.group(2).strip(), m.group(3)) for m in MARKER.finditer(item)]


def bind(spans, marks):
    """Каждому числу - маркеры рядом: сразу после (до следующего числа), иначе ближайший перед ним."""
    out = []
    for i, (s, e, txt, pct) in enumerate(spans):
        nxt = spans[i + 1][0] if i + 1 < len(spans) else 10 ** 9
        prev_end = spans[i - 1][1] if i else 0
        after = [m for m in marks if e <= m[0] < nxt and m[0] - e <= GAP]
        bound: list = []
        if after:
            bound.append(after[0])
            for m in after[1:]:
                if m[0] - bound[-1][1] <= 3:
                    bound.append(m)
        else:
            before = [m for m in marks if prev_end <= m[1] <= s and s - m[1] <= GAP]
            if before:
                bound.append(before[-1])
        out.append(((s, e, txt, pct), bound))
    return out


def agg_unit(tb: Toolbox, a: dict) -> str:
    if a.get("table") != "results" or a.get("col") != "value":
        return ""
    units = {solar.unit_of(tb.facts[i][1]) for i in a["ids"] if i in tb.facts}
    return units.pop() if len(units) == 1 else ""


_FN_RU = {"mean": "среднее", "median": "медиана", "min": "минимум", "max": "максимум", "sum": "сумма", "count": "число"}


def agg_text(a: dict, papers: int) -> str:
    base = f"{_FN_RU.get(a['fn'], a['fn'])}{' ' + a['col'] if a.get('col') else ''}"
    return f"{base} по {len(a['ids'])} строкам из {papers} статей"


class Verifier:
    def __init__(self, tb: Toolbox, question: str, mode: str = "drop") -> None:
        self.tb, self.mode = tb, mode
        self.qnums = {v for v in (_canon(m.group(0)) for m in NUMBER.finditer(question)) if v is not None}
        self.seen: list[float] = []  # числа, подтверждённые в уже принятых пунктах ответа

    # --- проверка маркера и числа ----------------------------------------------------------------------------

    def _ref(self, kind: str, ref: str, quote: str | None) -> tuple[str | None, Any]:
        """(ошибка, объект): (статья, строка) | агрегат | фрагмент."""
        tb = self.tb
        if kind == "fact":
            hit = tb.facts.get(ref)
            return (None, hit) if hit else (f"unknown fact {ref}", None)
        if kind == "agg":
            a = tb.aggs.get(ref)
            return (None, a) if a else (f"unknown agg {ref}", None)
        if kind == "calc":
            c = tb.calcs.get(ref)
            return (None, c) if c else (f"unknown calc {ref}", None)
        if kind == "page":
            pid, _, n = ref.partition("/")
            if not (quote or "").strip():
                return f"page marker {ref} needs a verbatim quote", None
            text = tb.page_text(pid, int(n)) if pid in tb.ctx.by_pid and n.strip().isdigit() else None
            if text is None:
                return f"unknown page {ref}", None
            if not quote_in(quote, text):
                return f"quote is not on page {ref}", None
            return None, {"pid": pid, "page": int(n), "text": text}
        if kind == "chunk":
            c = tb.chunks.get(ref)
            if not c:
                return f"unknown chunk {ref}", None
            if quote and not quote_in(quote, c["text"]):
                return f"quote is not in chunk {ref}", None
            return None, c
        if kind == "issue":
            pid, _, iid = ref.partition("/")
            p = tb.ctx.by_pid.get(pid)
            row = next((i for i in (p.issues if p else []) if i.get("issue_id") == iid), None)
            return (None, (p, row)) if row else (f"unknown issue {ref}", None)
        table, _, rest = ref.partition("/")
        pid, _, rid = rest.partition("/")
        p = tb.ctx.by_pid.get(pid)
        key = tb.spec.keys.get(table)
        row = next((r for r in (p.tables.get(table) or []) if r.get(key) == rid), None) if p and key else None
        return (None, (p, row)) if row else (f"unknown row {ref}", None)

    def _number_ok(self, kind: str, obj: Any, quote: str | None, txt: str, pct: bool) -> bool:
        w = _canon(txt)
        if w is None:
            return True
        d = _decimals(txt)
        if kind == "fact":
            row = obj[1]
            unit = solar.unit_of(row)
            v = solar.value_of(row)
            cands = [(v, unit)] if v is not None else []
            for f in FACT_NUM_FIELDS[2:]:
                x = to_float(row.get(f))
                if x is not None:
                    cands.append((x, "count" if f in ("n_devices", "sample_size", "metric_k") else unit))
            return any(_value_match(w, d, pct, x, u) for x, u in cands)
        if kind == "agg":
            unit = agg_unit(self.tb, obj)
            cands = [(obj["value"], unit)] if obj["value"] is not None else []
            cands += [(float(obj["n"]), "count"), (float(len(obj["ids"])), "count"),
                      (float(len({i.split("/", 1)[0] for i in obj["ids"]})), "count")]
            return any(_value_match(w, d, pct, x, u) for x, u in cands)
        if kind == "calc":
            return _value_match(w, d, pct, obj["value"], "")
        if kind in ("chunk", "page"):
            return any(abs(x - w) < 1e-9 for x in _nums_in((quote or "") + " " + obj["text"]))
        cells = " ".join(str(x) for x in obj[1].values())
        return any(abs(x - w) < 0.5 * 10 ** (-d) + 1e-9 for x in _nums_in(cells))

    def _seen_ok(self, txt: str) -> bool:
        """Число уже подтверждено выше в этом же ответе (итог повторяет значения из пунктов)."""
        w = _canon(txt)
        return w is not None and any(abs(w - x) < 1e-9 for x in self.seen)

    def remember(self, item: str) -> None:
        """Числа принятого пункта считаются подтверждёнными для следующих пунктов ответа."""
        for _s, _e, txt, _pct in number_spans(item, self.qnums):
            w = _canon(txt)
            if w is not None:
                self.seen.append(w)

    def check_item(self, item: str) -> str | None:
        """None - пункт верен; иначе причина отбрасывания."""
        marks = find_markers(item)
        for *_, kind, ref, quote in marks:
            quote = clean_quote(quote)
            err, _ = self._ref(kind, ref, quote)
            if err:
                return err
        objs = [(k, self._ref(k, r, clean_quote(q))[1], clean_quote(q)) for *_, k, r, q in marks]
        for (s, e, txt, pct), bound in bind(number_spans(item, self.qnums), marks):
            if not bound:
                # число без своего маркера: годится, если его подтверждает любой источник этого же пункта
                # (цитата «…+12%…» рядом с [[chunk:…|…+12%…]]) или оно уже подтверждено выше в ответе
                if self._seen_ok(txt) or any(self._number_ok(k, o, q, txt, pct) for k, o, q in objs):
                    continue
                return f"number {txt} without marker"
            if not any(self._number_ok(k, self._ref(k, r, q)[1], q, txt, pct) for *_, k, r, q in bound):
                return f"number {txt} does not match {', '.join(b[3] for b in bound)}"
        return None

    # --- ответ целиком ---------------------------------------------------------------------------------------

    def run(self, text: str) -> Verified:
        v = Verified("", [])
        self.seen = []
        lines_out: list[str | None] = []
        src: list[str] = []
        for line in text.strip().splitlines():
            if not line.strip():
                continue
            src.append(line)
            kept = []
            for sent in split_items(line):
                err = self.check_item(sent)
                v.items_total += not sent.rstrip().endswith(":")  # считаются все предложения: пересказ без чисел не должен давать ложный откат
                if err is None:
                    kept.append(sent)
                    self.remember(sent)
                    continue
                v.items_dropped += 1
                v.dropped.append({"item": sent[:300], "reason": err, "mode": self.mode})
                if self.mode == "mark":
                    kept.append(MARKER.sub("", sent).strip() + " (не подтверждено)")
                    v.unverified.append(sent)
            joined = " ".join(kept) if kept else None
            if joined is not None and BARE_LIST_ITEM.fullmatch(joined):  # от пункта списка остался только номер
                joined = None
            lines_out.append(joined)
        lines = []
        for i, ln in enumerate(lines_out):
            if ln is None:
                continue
            if ln.rstrip().endswith(":"):  # заголовок списка, чьи пункты все отброшены, уходит вместе с ними
                nxt = [j for j in range(i + 1, len(lines_out)) if LIST_ITEM.match(src[j])
                       and all(LIST_ITEM.match(src[k]) for k in range(i + 1, j + 1))] or list(range(i + 1, min(i + 2, len(lines_out))))
                if all(lines_out[j] is None for j in nxt):
                    continue
            lines.append(ln)
        v.text = "\n".join(_renumber(lines) if v.items_dropped else lines)
        self._footnotes(v)
        return v

    def _footnotes(self, v: Verified) -> None:
        keys: dict[tuple, int] = {}
        cites: list[dict] = []

        def repl(m: re.Match) -> str:
            kind, ref, quote = m.group(1), m.group(2).strip(), clean_quote(m.group(3))
            err, obj = self._ref(kind, ref, quote)
            if err:
                return ""
            key = (kind, ref, (quote or "").strip() if kind in ("chunk", "page") else "")
            if key not in keys:
                keys[key] = len(cites) + 1
                cites.append({"n": keys[key], **self._cite(kind, ref, obj, quote, v)})
            return f"\x00[{keys[key]}]"

        t = MARKER.sub(repl, v.text)
        t = re.sub(r"[ \t]*\x00", " ", t)
        t = re.sub(r"(\[\d+\])(?: \1)+", r"\1", t)
        t = re.sub(r"(\[\d+\]) (?=\[\d+\])", r"\1", t)
        v.text = re.sub(r"[ \t]+([.,;:!?)])", r"\1", t)
        v.cites = cites

    def _cite(self, kind: str, ref: str, obj: Any, quote: str | None, v: Verified) -> dict:
        dom = self.tb.ctx.domain
        base = {"domain": dom, "pid": None, "result_id": None, "issue_id": None, "page": None, "fragment": ""}
        if kind == "fact":
            p, r = obj
            v.used_facts.append(ref)
            return {**base, "pid": p.pid, "result_id": r.get("result_id"), "page": _page(r.get("source_page")),
                    "fragment": (r.get("source_fragment") or "")[:300]}
        if kind == "issue":
            p, r = obj
            v.used_issues.append(ref)
            return {**base, "pid": p.pid, "issue_id": r.get("issue_id"), "page": _page(r.get("source_page")),
                    "fragment": (r.get("source_fragment") or r.get("description") or "")[:300]}
        if kind == "agg":
            v.used_aggs.append(ref)
            pids = {i.split("/", 1)[0] for i in obj["ids"]}
            return {**base, "agg_id": ref, "text": agg_text(obj, len(pids)), "result_ids": obj["ids"][:50]}
        if kind == "page":
            return {**base, "pid": obj["pid"], "page": obj["page"], "fragment": (quote or "").strip()[:300]}
        if kind == "calc":
            v.used_calc_facts.extend(x["fact_id"] for x in obj["inputs"].values() if x.get("fact_id"))
            ins = ", ".join(f"{k} = {_fmt(x['value'])}{(' ' + x['unit']) if x.get('unit') else ''}"
                            for k, x in obj["inputs"].items())
            rids = [x["fact_id"] for x in obj["inputs"].values() if x.get("fact_id")]
            pid = rids[0].split("/", 1)[0] if rids else None
            return {**base, "pid": pid, "calc_id": ref, "expr": obj["expr"], "value": round(obj["value"], 6),
                    "inputs": obj["inputs"], "fact_ids": rids,
                    "fragment": f"{obj['expr']} = {_fmt(obj['value'])}; {ins}"[:300]}
        if kind == "chunk":
            return {**base, "pid": obj["pid"], "chunk_id": ref, "page": _page(obj["page"]),
                    "fragment": (quote or obj["text"]).strip()[:300]}
        p, r = obj
        return {**base, "pid": p.pid, "table": ref.split("/", 1)[0], "row_id": ref.split("/", 2)[-1],
                "page": _page(r.get("source_page")), "fragment": (r.get("source_fragment") or "")[:300]}
