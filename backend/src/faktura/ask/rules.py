"""Правиловый планировщик: вопрос -> план по ключевым словам. Запасной путь при недоступном ML и мок `POST /ask/plan`.

Работает по описанию разбора (`RunCtx.describe()`), поэтому одинаков в бэкенде и в моке ML. Возвращает «сырой» план:
вызывающий пропускает его через `plan.normalize_plan`."""
from __future__ import annotations

import re

from ..domain.registry import get_domain
from .plan import empty_plan

_W = r"(?<![a-zа-я0-9])"


def _low(s: str) -> str:
    return s.lower().replace("ё", "е").replace("₂", "2").replace("₃", "3")


def _rx(src: str) -> re.Pattern:
    return re.compile(src.replace("\\W", _W), re.I)


METRIC_RULES = [
    ("retained_PCE", _rx(r"\Wсохран[а-я]* (кпд|эффективн)|\Wretained|\Wостаточн[а-я]* кпд")),
    ("stabilized_PCE", _rx(r"\Wстабилизир|\Wstabilized")),
    ("Jsc_EQE", _rx(r"jsc[ _]?(по )?eqe|\Weqe")),
    ("T80", _rx(r"\Wt80|\Wстабильн|\Wдеградац|\Wсрок службы|\Wресурс")),
    ("HI", _rx(r"\Wгистерезис|\Whi(?![a-z])|\Whysteresis")),
    ("Rsh", _rx(r"\Wrsh|\Wшунт|\Wпараллельн[а-я]* сопротивл")),
    ("Rs", _rx(r"\Wrs(?![a-z])|\Wпоследовательн[а-я]* сопротивл")),
    ("FF", _rx(r"\Wff(?![a-z])|\Wфактор[а-я]* заполн|\Wкоэффициент[а-я]* заполн|\Wfill factor")),
    ("Jsc", _rx(r"\Wjsc|\Wisc|\Wток|\Wплотност[а-я]* тока")),
    ("Voc", _rx(r"\Wvoc|\Wuoc|\Wнапряжен|\Wхолост")),
    ("PCE", _rx(r"\Wкпд|\Wpce|\Wэффективн|\Wefficien")),
]
CLASS_RULES = [
    ("perovskite", _rx(r"\Wперовскит|\Wperovsk")),
    ("CIGS", _rx(r"\Wcigs|\Wcu\(in|\Wcuinga|\Wмедь-индий")),
    ("CdTe", _rx(r"\Wcdte|\Wтеллурид[а-я]* кадми")),
    ("kesterite", _rx(r"\Wкестерит|\Wkesterit|\Wczts")),
    ("Sb2Se3", _rx(r"\Wsb2se3|\Wселенид[а-я]* сурьм|\Wсурьм")),
    ("thin-film Si", _rx(r"\Wкремни|\Wa-si|\Wsilicon")),
]
NOT_LAYER = {"pce", "voc", "uoc", "jsc", "isc", "ff", "rs", "rsh", "hi", "t80", "eqe", "cigs", "cdte", "czts", "cztse",
             "cztsse", "sb2se3", "si", "a-si", "vs", "pdf", "cited", "retained", "stabilized", "efficiency", "champion",
             "best", "max", "mean", "perovskite", "kesterite", "silicon", "hysteresis"}
RAG_LISTS = [("datasets", _rx(r"\Wдатасет|\Wнабор[а-я]* данных|\Wбенчмарк")), ("generators", _rx(r"\Wгенератор|\Wllm")),
             ("embeddings", _rx(r"\Wэмбеддинг|\Wembedding")), ("retrievers", _rx(r"\Wретривер")),
             ("rerankers", _rx(r"\Wреранкер")), ("tasks", _rx(r"\Wзадач")), ("configs", _rx(r"\Wконфигурац"))]
TABLE_STEMS = [("publications", r"публикац"), ("materials", r"материал"), ("devices", r"прибор"), ("layers", r"слое|слои"),
               ("measurements", r"измерени|услови"), ("results", r"результат|значени"), ("issues", r"замечани"),
               ("configurations", r"конфигурац"), ("datasets", r"датасет"), ("tasks", r"задач"), ("experiments", r"эксперимент")]
BEST = _rx(r"\Wлучш|\Wмаксим|\Wрекорд|\Wнаибольш|\Wнаивысш|\Wсамы[йе] (высок|больш)|\Wчемпион|\Wtop|\Wbest|\Wmax|\Wтоп")
WORST = _rx(r"\Wхудш|\Wминим|\Wнаименьш|\Wсамы[йе] (низк|мал)")


def _field_names(domain: str) -> dict[str, str]:
    """имя поля -> таблица (results приоритетнее)."""
    out: dict[str, str] = {}
    for t, cols in get_domain(domain).tables.items():
        for c in cols:
            if c != "publication_id" and (c not in out or t == "results"):
                out[c] = t
    return out


def _bounds(q: str, metric: str | None) -> tuple[float | None, float | None]:
    nq = re.sub(r"[a-zа-я]+\d[a-z0-9]*", " ", q).replace(",", ".")
    lo = hi = None
    m = re.search(r"(?:от|между)\s*(\d+(?:\.\d+)?)\s*(?:%|в|мв)?\s*(?:до|и)\s*(\d+(?:\.\d+)?)", nq)
    if m:
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
    else:
        for m in re.finditer(r"(не\s+ниже|не\s+меньше|не\s+менее|не\s+выше|не\s+больше|не\s+более|выше|больше|более|свыше|ниже|меньше|менее|>=|<=|>|<)\s*(?:чем\s*)?(\d+(?:\.\d+)?)", nq):
            up = re.match(r"(не\s+(ниже|меньше|менее)|выше|больше|более|свыше|>)", m.group(1))
            if up:
                lo = float(m.group(2))
            else:
                hi = float(m.group(2))
    if metric == "Voc":
        lo = lo / 1000 if lo is not None and lo > 10 else lo
        hi = hi / 1000 if hi is not None and hi > 10 else hi
    return lo, hi


def keyword_plan(question: str, desc: dict) -> dict:
    q = _low(question)
    domain = desc.get("domain") or "solar"
    plan = empty_plan()
    pids = {str(p).upper(): str(p) for p in desc.get("pids") or [p["pid"] for p in desc.get("papers") or []]}
    plan["pids"] = list(dict.fromkeys(c.upper() for c in re.findall(r"(?<![a-z0-9])[dp]\d{3}(?![0-9])", q, re.I)))
    for k in ("pids",):
        plan[k] = [pids.get(c, c) for c in plan[k]]

    # метрики
    metrics: list[str] = []
    if domain == "solar":
        have = {m["name"] for m in desc.get("metrics") or []}
        for name, rx in METRIC_RULES:
            if rx.search(q):
                if not have or name in have:
                    metrics.append(name)
                elif not plan["unknown_metric"]:
                    plan["unknown_metric"] = name  # показатель назван, но в данных его нет
        if "PCE" in metrics and ({"retained_PCE", "stabilized_PCE"} & set(metrics)):
            metrics.remove("PCE")
        if "Jsc" in metrics and "Jsc_EQE" in metrics:
            metrics.remove("Jsc")
    else:
        stripped = re.sub(r"(?<![a-z0-9])d\d{3}(?![0-9])", " ", q)
        best_base = ""
        for m in desc.get("metrics") or []:
            base = _low(str(m["name"]).split("@")[0]).strip()
            if len(base) >= 2 and len(base) > len(best_base) and re.search(rf"{_W}{re.escape(base)}(?![a-zа-я0-9])", stripped):
                best_base = base
        if best_base:
            metrics = [m["name"] for m in desc.get("metrics") or [] if _low(str(m["name"]).split("@")[0]).strip() == best_base]
            k = re.search(r"@\s*(\d+)", q)
            if k:
                narrowed = [m for m in metrics if re.search(rf"@\s*{k.group(1)}(?!\d)", m)]
                if narrowed or not any("@" in m for m in metrics):
                    metrics = narrowed or metrics
                else:  # «Recall@5», а в данных только @3 и @6: не подставляем чужое k
                    plan["unknown_metric"] = f"{best_base}@{k.group(1)}"
                    metrics = []
    plan["metrics"] = metrics[:6]
    if domain == "rag":
        low_q = re.sub(r"(?<![a-z0-9])d\d{3}(?![0-9])", " ", q)
        for ds in sorted(desc.get("datasets") or [], key=len, reverse=True):
            d = _low(str(ds)).strip()
            if len(d) >= 3 and re.search(rf"{_W}{re.escape(d)}(?![a-zа-я0-9])", low_q):
                plan["rag_dataset"] = str(ds)
                break
    if domain == "solar":
        plan["material_class"] = [c for c, rx in CLASS_RULES if rx.search(q)]
        stripped = re.sub(r"(?<![a-z0-9])[dp]\d{3}(?![0-9])", " ", q)
        layers = []
        for tok in re.findall(r"[a-z][a-z0-9\-:()]*[a-z0-9)]|[a-z]{2,}", stripped):
            t = tok.rstrip("()").lower()
            if len(t) < 2 or t in NOT_LAYER or re.search(r"[(),]", t) or any(rx.search(" " + t) for _, rx in CLASS_RULES):
                continue
            if t not in layers:
                layers.append(t)
        for rx, name in ((r"(?<![а-я])углерод", "carbon"), (r"(?<![а-я])графен", "graphene"), (r"(?<![а-я])золот", "au"),
                         (r"(?<![а-я])серебр", "ag")):
            if re.search(rx, q):
                layers.append(name)
        plan["layers"] = layers[:6]
    plan["min_value"], plan["max_value"] = _bounds(q, metrics[0] if metrics else None)
    if re.search(r"цитир|из литератур|чуж[а-я]* (работ|значен)|cited", q):
        plan["origin"] = "cited"
    if re.search(r"средн|mean|average", q):
        plan["aggregation"] = "mean"
    elif re.search(r"champion|чемпион", q):
        plan["aggregation"] = "champion"
    worst = bool(WORST.search(q))
    best = bool(BEST.search(q)) or worst
    plan["sort"] = "worst" if worst else "best"
    ck = re.search(r"противореч|hysteresis|гистерезис", q)
    if ck:
        plan["check"] = "contradiction" if "противореч" in q else "hysteresis"

    # таблица / поле
    fields = _field_names(domain)
    tables = get_domain(domain).tables
    table = next((t for t in tables if re.search(rf"(?<![a-z0-9_]){t}(?![a-z0-9_])", q)), "")
    show_q = bool(re.search(r"покажи|показать|примеры|пример|строки|строк", q))
    if not table and ("таблиц" in q or (show_q and not metrics)):
        table = next((t for t, rx in TABLE_STEMS if t in tables and re.search(rx, q)), "")
    field = next((f for f in fields if f != "publication_id" and re.search(rf"(?<![a-z0-9_]){re.escape(f)}(?![a-z0-9_])", q)), "")
    schema_q = bool(re.search(r"что (такое|значит|означает|в|за|лежит)|поля|полей|поле\b|колонк|столбц|обязательн|таблиц|формат|схем|паспорт", q))
    if (table or field) and (schema_q or show_q) and not (metrics and best):
        plan["table"], plan["field"] = table or fields.get(field, ""), field
        plan["intent"] = "table_rows" if show_q and not re.search(r"что (такое|значит|в )|поля|полей|поле\b|колонк|обязательн", q) else "schema"
        plan["limit"] = 10
        plan["layers"], plan["material_class"] = [], []
        return plan
    if re.search(r"какие таблиц|сколько таблиц|из каких таблиц|формат (набора|сдачи)|что сдава|обязательн", q):
        plan["intent"] = "schema"
        plan["field"] = "@required" if "обязательн" in q else ""
        plan["layers"], plan["material_class"] = [], []
        return plan

    # намерение
    if re.search(r"замечан|ошибк|проблем|противореч|несоглас|нестыков|проверк|issue", q):
        plan["intent"] = "issues"
    elif domain == "rag" and not metrics and any(rx.search(q) for _, rx in RAG_LISTS):
        plan["intent"] = "rag_list"
        plan["rag_list"] = next(n for n, rx in RAG_LISTS if rx.search(q))
    elif re.search(r"сколько|количеств|число (значен|стат|результ)", q):
        plan["intent"] = "count"
    elif best:
        plan["intent"], plan["per_paper"] = "best", True
        plan["limit"] = 5
    elif metrics or plan["min_value"] is not None or plan["max_value"] is not None:
        plan["intent"], plan["limit"] = "results", 50
    elif plan["unknown_metric"]:
        plan["intent"] = "best" if best else "results"
    elif plan["pids"]:
        plan["intent"] = "paper"
    elif domain == "solar" and (plan["material_class"] or plan["layers"]):
        plan["intent"], plan["limit"] = "results", 50
    return plan
