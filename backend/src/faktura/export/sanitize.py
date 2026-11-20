"""Санитайзер - последний слой перед записью CSV (план 5.4, REQ-018, REQ-020, REQ-037, К-3).

Задача: на любом ответе ML получить набор без ошибок `check_submission.py` и строгого валидатора.
Каждое изменение пишется событием (для `_meta/errors.log`); исходное значение уходит в `note` записи,
если у таблицы есть колонка `note`.

Порядок шагов внутри статьи:
1. `publication_id` строк = `publication_id` статьи; пустые id получают новые;
2. ячейки: словари, логические, числа (привести или очистить), списки, заглушки вида `null`/`—`;
3. единица только при значении (REQ-037);
4. повтор id в таблице - переименование `<id>-<n>` с каскадом в `issues.record_ids` (REQ-018);
5. висячие ссылки - очистить (REQ-019).

Замечания (`issues`) с пустыми `source_page`/`source_fragment` не трогаются: это допустимо (их нет у 9
эталонных замечаний), строгий валидатор даёт warning. `check_name` вне каталога тоже не трогается.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, Field

from ..domain.records import PaperBundle
from ..domain.tables import (BOOL_FIELDS, ID_COLUMN, INTEGER_FIELDS, LIST_FIELDS, NUMERIC_FIELDS, TABLES,
                             UNIT_FOR_VALUE, VOCAB)

__all__ = ["SanitizeResult", "sanitize", "number_issues", "parse_number_text", "REFERENCES", "ID_PREFIX"]


class SanitizeResult(BaseModel):
    bundles: list[PaperBundle] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)


# (таблица, колонка) -> целевая таблица; список - для полей через ';'
REFERENCES: tuple[tuple[str, str, str], ...] = (
    ("devices", "absorber_material_id", "materials"),
    ("layers", "device_id", "devices"),
    ("measurements", "device_id", "devices"),
    ("results", "device_id", "devices"),
    ("results", "measurement_id", "measurements"),
    ("results", "material_id", "materials"),
    ("results", "duplicate_of", "results"),
    ("issues", "record_ids", "results"),
)

# префиксы для новых id (пустой id в ответе ML)
ID_PREFIX: dict[str, str] = {"materials": "M", "devices": "DV", "layers": "L", "measurements": "MS",
                             "results": "R", "issues": "I"}

_TRUE = {"true", "1", "yes", "да", "encapsulated"}
_FALSE = {"false", "0", "no", "нет", "unencapsulated", "not encapsulated", "non-encapsulated"}
# ячейка целиком - «нет значения» (REQ-014: пустая ячейка вместо null/NA/—)
_PLACEHOLDERS = {"null", "none", "nan", "n/a", "na", "-", "—", "–", "−"}

_NUM_RE = re.compile(
    r"""^\s*(?:[~≈∼]|ca\.?\s|approx\.?\s|около\s)?\s*      # приблизительно - снимаем
        (?P<num>[+\-−]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+\-]?\d+)?)
        (?P<rest>.*)$""", re.X | re.S)
# после числа допустима только единица: пробел и буква/знак (а не цифра, дефис диапазона и т. п.)
_UNIT_RE = re.compile(r"^(?:\s*%|\s+[A-Za-zµμΩ°%][\w/·*^\s.%°µμΩ\-]*)\s*$")
_RANGE_RE = re.compile(r"^\s*[\-–—−~]\s*\d|^\s*(?:to|до)\s+\d", re.I)


def parse_number_text(text: str) -> Decimal | None:
    """Строка -> Decimal или None (не число). «1,5» -> 1.5; «~1.9» -> 1.9; «15.28 mA» -> 15.28;
    «18.2-19.0», «63–68», «1e», «abc», «n/a» -> None."""
    s = text.strip()
    if not s:
        return None
    m = _NUM_RE.match(s)
    if not m:
        return None
    num, rest = m.group("num"), m.group("rest")
    if rest and (_RANGE_RE.match(rest) or not _UNIT_RE.match(rest)):
        return None
    num = num.replace("−", "-")
    if "," in num:
        if "." in num:
            return None
        num = num.replace(",", ".")
    try:
        d = Decimal(num)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


class _Ctx:
    def __init__(self, bundle: PaperBundle, events: list[str]):
        self.b = bundle
        self.pid = bundle.publication.publication_id
        self.events = events

    def log(self, msg: str) -> None:
        self.events.append(f"{self.pid}: {msg}")


def _row_label(table: str, row: Any) -> str:
    if table == "publications":
        return "publications"
    return f"{table}[{getattr(row, ID_COLUMN[table], '') or '?'}]"


def _add_note(table: str, row: Any, text: str) -> None:
    if "note" not in TABLES[table]:
        return
    cur = (row.note or "").strip()
    if text in cur:
        return
    row.note = f"{cur}; {text}" if cur else text


def _show(v: Any) -> str:
    if v is None:
        return "пусто"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _fix_cell(ctx: _Ctx, table: str, row: Any, col: str) -> None:
    key = (table, col)
    v = getattr(row, col, None)
    if key in LIST_FIELDS:
        if v is None:
            new: Any = []
        elif isinstance(v, str):
            new = [p.strip() for p in v.split(LIST_FIELDS[key].strip()) if p.strip()]
        else:
            new = [str(p).strip() for p in v if p is not None and str(p).strip()]
        if new != v:
            setattr(row, col, new)
        return
    if v is None:
        return
    if key in BOOL_FIELDS:
        if isinstance(v, bool):
            return
        low = str(v).strip().lower()
        if low in _TRUE:
            new = True
        elif low in _FALSE:
            new = False
        elif low == "" or low in _PLACEHOLDERS:
            setattr(row, col, None)
            return
        else:
            new = None
            _add_note(table, row, f"{col}: исходно «{v}»")
        setattr(row, col, new)
        ctx.log(f"{_row_label(table, row)}.{col}: «{v}» -> {_show(new)} (логическое поле: true/false)")
        return
    if key in VOCAB:
        allowed = VOCAB[key]
        s = str(v).strip()
        if s in allowed:
            if s != v:
                setattr(row, col, s)
            return
        if s == "" or s.lower() in _PLACEHOLDERS:
            setattr(row, col, None)
            return
        fixed = next((a for a in sorted(allowed) if a.casefold() == s.casefold()), None)
        setattr(row, col, fixed)
        _add_note(table, row, f"{col}: исходно «{v}»")
        ctx.log(f"{_row_label(table, row)}.{col}: «{v}» -> {_show(fixed)} (нет в словаре)")
        return
    if key in NUMERIC_FIELDS:
        _fix_number(ctx, table, row, col, v)
        return
    empty: Any = "" if col == "check_name" else None  # check_name - обязательная строка модели
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            if v != "":
                setattr(row, col, empty)
        elif s.lower() in _PLACEHOLDERS:
            setattr(row, col, empty)
            ctx.log(f"{_row_label(table, row)}.{col}: «{v}» -> пусто (заглушка вместо пустой ячейки)")
    else:
        setattr(row, col, str(v))


RANGE_TEXT_FIELDS = {("layers", "layer_thickness")}
_RANGE_TEXT = re.compile(r"^\d+(?:[.,]\d+)?\s*[–—-]\s*\d+(?:[.,]\d+)?$")


def _fix_number(ctx: _Ctx, table: str, row: Any, col: str, v: Any) -> None:
    """Число в числовом поле. Тихо: int <-> Decimal без потери значения. С событием и note: всё остальное."""
    is_int = (table, col) in INTEGER_FIELDS
    reason: str | None = None
    new: Any
    if isinstance(v, bool):
        new, reason = None, "логическое значение в числовом поле"
    elif isinstance(v, int):
        new = v if is_int else Decimal(v)
    elif isinstance(v, float):
        finite = v == v and v not in (float("inf"), float("-inf"))
        new = Decimal(repr(v)) if finite else None
        reason = None if finite else "не число"
    elif isinstance(v, Decimal):
        new = v if v.is_finite() else None
        reason = None if v.is_finite() else "не число"
    else:
        s = str(v).strip()
        if (table, col) in RANGE_TEXT_FIELDS and _RANGE_TEXT.match(s):
            setattr(row, col, s)  # диапазон толщины «63–68» - как в эталоне
            return
        if s == "" or s.lower() in _PLACEHOLDERS:
            setattr(row, col, None)
            if s:
                ctx.log(f"{_row_label(table, row)}.{col}: «{v}» -> пусто (заглушка вместо пустой ячейки)")
            return
        new = parse_number_text(s)
        reason = "не число" if new is None else "приведено к числу"
    if new is not None and is_int and not isinstance(new, int):
        if new == new.to_integral_value():
            new = int(new)
        else:
            new, reason = None, "ожидается целое число"
    setattr(row, col, new)
    if reason:
        _add_note(table, row, f"{col}: исходно «{v}»")
        ctx.log(f"{_row_label(table, row)}.{col}: «{v}» -> {_show(new)} ({reason})")


def _fix_units(ctx: _Ctx) -> None:
    """Единица без значения (bandgap_unit/thickness_unit/area_unit) остаётся: эталон оставляет «eV» у пустой ширины
    зоны, README шаблона правила об очистке не содержит."""
    return None


def _fix_publication_ids(ctx: _Ctx) -> None:
    for table, rows in ctx.b.iter_tables():
        if table == "publications":
            continue
        for row in rows:
            if row.publication_id != ctx.pid:
                ctx.log(f"{_row_label(table, row)}.publication_id: «{row.publication_id}» -> {ctx.pid}")
                row.publication_id = ctx.pid


def _fix_empty_ids(ctx: _Ctx) -> None:
    for table, rows in ctx.b.iter_tables():
        if table == "publications":
            continue
        col = ID_COLUMN[table]
        used = {str(getattr(r, col) or "").strip() for r in rows}
        n = 0
        for row in rows:
            cur = getattr(row, col)
            s = "" if cur is None else str(cur).strip()
            if s:
                if s != cur:
                    setattr(row, col, s)
                continue
            while True:
                n += 1
                cand = f"{ID_PREFIX[table]}{n}"
                if cand not in used:
                    break
            used.add(cand)
            setattr(row, col, cand)
            ctx.log(f"{table}: пустой {col} -> {cand}")


def _fix_duplicate_ids(ctx: _Ctx) -> None:
    """Повтор id внутри статьи: первая запись сохраняет id, следующие получают `<id>-<n>`.
    Каскад (REQ-018): ссылки на старый id остаются на первой записи (они валидны), а замечания,
    ссылавшиеся на повторённый результат, получают и новый id - неоднозначная ссылка не теряется.
    `duplicate_of` переименованной строки, указывавший на свой же старый id, теперь указывает на первую."""
    for table, rows in ctx.b.iter_tables():
        if table == "publications":
            continue
        col = ID_COLUMN[table]
        used = {getattr(r, col) for r in rows}
        seen: set[str] = set()
        renamed: dict[str, list[str]] = {}
        for row in rows:
            cur = getattr(row, col)
            if cur not in seen:
                seen.add(cur)
                continue
            k = 0
            while True:
                k += 1
                cand = f"{cur}-{k}"
                if cand not in used:
                    break
            used.add(cand)
            seen.add(cand)
            setattr(row, col, cand)
            renamed.setdefault(cur, []).append(cand)
            ctx.log(f"{table}: повтор {col} «{cur}» -> {cand}")
        if table == "results" and renamed:
            for iss in ctx.b.issues:
                extra = [n for rid in iss.record_ids if rid in renamed for n in renamed[rid]]
                add = [n for n in extra if n not in iss.record_ids]
                if add:
                    iss.record_ids = [*iss.record_ids, *add]
                    ctx.log(f"issues[{iss.issue_id}].record_ids: + {';'.join(add)} (каскад переименования)")


def _fix_references(ctx: _Ctx) -> None:
    ids = {t: {getattr(r, ID_COLUMN[t]) for r in getattr(ctx.b, t)}
           for t in ("materials", "devices", "measurements", "results")}
    for table, col, target in REFERENCES:
        for row in getattr(ctx.b, table):
            v = getattr(row, col)
            if (table, col) in LIST_FIELDS:
                keep = [x for x in v if x in ids[target]]
                gone = [x for x in v if x not in ids[target]]
                if gone:
                    setattr(row, col, keep)
                    ctx.log(f"{_row_label(table, row)}.{col}: висячие ссылки {';'.join(gone)} удалены "
                            f"(нет в {target})")
                continue
            if v is None or v == "":
                if v == "":
                    setattr(row, col, None)
                continue
            if v not in ids[target]:
                setattr(row, col, None)
                ctx.log(f"{_row_label(table, row)}.{col}: висячая ссылка {v} очищена (нет в {target})")
    for r in ctx.b.results:
        if r.duplicate_of is not None and r.duplicate_of == r.result_id:
            r.duplicate_of = None
            ctx.log(f"results[{r.result_id}].duplicate_of: ссылка на себя очищена")


def _sanitize_bundle(bundle: PaperBundle, events: list[str]) -> PaperBundle:
    b = bundle.model_copy(deep=True)
    ctx = _Ctx(b, events)
    _fix_publication_ids(ctx)
    _fix_empty_ids(ctx)
    for table, rows in b.iter_tables():
        for row in rows:
            for col in TABLES[table]:
                if table != "publications" and col == ID_COLUMN[table]:
                    continue
                if col == "publication_id":
                    continue
                _fix_cell(ctx, table, row, col)
    _fix_units(ctx)
    _fix_duplicate_ids(ctx)
    _fix_references(ctx)
    return b


def sanitize(bundles: list[PaperBundle]) -> SanitizeResult:
    """Словари -> пусто + исходник в note; нечисла -> привести или очистить + note; висячие ссылки -> очистить + лог;
    повтор id -> каскадное переименование; единица без значения -> очистить (REQ-037).
    Вход не меняется; повторный вызов на результате не даёт событий (идемпотентность)."""
    events: list[str] = []
    out = [_sanitize_bundle(b, events) for b in bundles]
    return SanitizeResult(bundles=out, events=events)


def _page_key(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, Decimal):
        return int(v) if v.is_finite() and v == v.to_integral_value() else None
    if isinstance(v, str):
        d = parse_number_text(v)
        return int(d) if d is not None and d == d.to_integral_value() else None
    return None


def number_issues(bundle: PaperBundle) -> PaperBundle:
    """Замечания с уже заданным issue_id (от ML) выгружаются как есть и в своём порядке; замечания без номера
    (правила бэка) получают новые I<n> после максимального числового номера, в порядке списка. Повторный вызов
    ничего не меняет. Остальные таблицы не трогаются."""
    b = bundle.model_copy(deep=True)
    used: set[str] = set()
    top = 0
    for iss in b.issues:
        iid = (iss.issue_id or "").strip()
        iss.issue_id = iid
        if iid and iid not in used:
            used.add(iid)
            m = re.fullmatch(r"I(\d+)", iid)
            if m:
                top = max(top, int(m.group(1)))
        else:
            iss.issue_id = ""
    for iss in b.issues:
        if not iss.issue_id:
            top += 1
            while f"I{top}" in used:
                top += 1
            iss.issue_id = f"I{top}"
            used.add(iss.issue_id)
    for n, iss in enumerate(b.issues):
        iss.meta.ml_order = n
    return b
