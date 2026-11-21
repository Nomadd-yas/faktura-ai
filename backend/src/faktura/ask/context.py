"""Данные разбора для вопросов: строки статей с правками v2 и краткое описание для планировщика."""
from __future__ import annotations

from collections import Counter, defaultdict
from functools import cached_property
from typing import Any

from ..domain.entities import CorpusFileStatus
from ..review import runs_v2 as V2

DESC_PAPERS = 40
DESC_METRICS = 40


class PaperData:
    """Одна статья разбора: таблицы (без отклонённых строк, исправленные значения подставлены) и открытые замечания."""

    def __init__(self, pid: str, pub: str, tables: dict[str, list[dict]], issues: list[dict], corrected: set[str]):
        self.pid, self.pub, self.tables, self.issues, self.corrected = pid, pub, tables, issues, corrected
        p = (tables.get("publications") or [{}])[0]
        self.title = p.get("title") or ""
        self.in_scope = (p.get("in_scope") or "").strip().lower() != "false"

    @property
    def results(self) -> list[dict]:
        return self.tables.get("results") or []

    @property
    def has_data(self) -> bool:
        return bool(self.results)

    def index(self, table: str, key: str) -> dict[str, dict]:
        return {r.get(key, ""): r for r in self.tables.get(table) or []}

    @cached_property
    def devices(self) -> dict[str, dict]:
        return self.index("devices", "device_id")

    @cached_property
    def materials(self) -> dict[str, dict]:
        return self.index("materials", "material_id")

    @cached_property
    def configs(self) -> dict[str, dict]:
        return self.index("configurations", "config_id")

    @cached_property
    def datasets(self) -> dict[str, dict]:
        return self.index("datasets", "dataset_id")

    @cached_property
    def tasks(self) -> dict[str, dict]:
        return self.index("tasks", "task_id")

    @cached_property
    def layers_of(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for r in self.tables.get("layers") or []:
            out[r.get("device_id", "")].append(r)
        return out


class RunCtx:
    def __init__(self, domain: str, papers: list[PaperData], title: str = "") -> None:
        self.domain, self.papers, self.title = domain, papers, title
        self.by_pid = {p.pid: p for p in papers}

    @property
    def has_data(self) -> bool:
        return any(p.has_data for p in self.papers)

    def target(self, pids: list[str] | None = None) -> list[PaperData]:
        """Названные статьи с данными, иначе все статьи «по теме» с данными."""
        if pids:
            return [self.by_pid[p] for p in pids if p in self.by_pid and self.by_pid[p].has_data]
        return [p for p in self.papers if p.has_data and p.in_scope]

    def metric_stats(self) -> list[tuple[str, int, int]]:
        n: Counter[str] = Counter()
        papers: dict[str, set[str]] = defaultdict(set)
        for p in self.papers:
            if not p.in_scope:
                continue
            for r in p.results:
                m = r.get("metric_name") or ""
                if m and not r.get("duplicate_of"):
                    n[m] += 1
                    papers[m].add(p.pid)
        return sorted(((m, c, len(papers[m])) for m, c in n.items()), key=lambda x: (-x[2], -x[1], x[0]))

    def describe(self) -> dict[str, Any]:
        """Описание разбора для планировщика: без строк, только названия и счётчики."""
        checks: Counter[str] = Counter()
        classes: Counter[str] = Counter()
        datasets: Counter[str] = Counter()
        tasks: Counter[str] = Counter()
        for p in self.papers:
            if not p.in_scope:
                continue
            checks.update(i.get("check_name") or "" for i in p.issues)
            classes.update(m.get("material_class") or "" for m in p.tables.get("materials") or [])
            datasets.update(d.get("dataset_name") or "" for d in p.tables.get("datasets") or [])
            tasks.update(t.get("task_type") or "" for t in p.tables.get("tasks") or [])
        stats = self.metric_stats()
        papers = sorted(self.papers, key=lambda p: -len(p.results))
        d: dict[str, Any] = {
            "domain": self.domain,
            "pids": [p.pid for p in self.papers],
            "papers_total": len(self.papers),
            "papers": [{"pid": p.pid, "title": p.title[:70], "results": len(p.results), "issues": len(p.issues),
                        "in_scope": p.in_scope} for p in papers[:DESC_PAPERS]],
            "metrics": [{"name": m, "n": n, "papers": k} for m, n, k in stats[:DESC_METRICS]],
            "metrics_total": len(stats),
            "tables": list(("publications", "materials", "devices", "layers", "measurements", "results", "issues")
                           if self.domain == "solar" else
                           ("publications", "configurations", "datasets", "tasks", "experiments", "results", "issues")),
            "checks": [[c, n] for c, n in checks.most_common(12) if c],
        }
        if self.domain == "solar":
            d["classes"] = [c for c, _ in classes.most_common() if c]
        else:
            d["datasets"] = [x for x, _ in datasets.most_common(15) if x]
            d["tasks"] = [x for x, _ in tasks.most_common(8) if x]
        return d


def llm_view(desc: dict) -> dict:
    """То, что уходит в LLM: без полного списка кодов."""
    return {k: v for k, v in desc.items() if k != "pids"}


def load_ctx(uow, corpus, only: set[str] | None = None) -> RunCtx:
    """Статьи разбора с применёнными правками v2: отклонённые строки убраны, исправленные значения подставлены;
    замечания - только открытые. `only` - коды файлов, которые нужны (остальные не читаются)."""
    from ..api.corpus.views import _file_ids  # noqa: PLC0415 - как у остальных представлений разбора

    domain = corpus.collection
    files = [f for f in uow.files.list(corpus.id) if f.status != CorpusFileStatus.skipped_duplicate]
    ids = _file_ids(files)
    papers = {p.id: p for p in uow.papers.list(corpus_id=corpus.id)}
    revs_all = V2.reviews_of(uow.conn, corpus.id, domain)
    states_all = V2.states_of(uow.conn, corpus.id, domain)
    extra_all = V2.extra_of(uow.conn, corpus.id, domain)
    out: list[PaperData] = []
    for f in files:
        pid = ids[f.id]
        paper = papers.get(f.paper_id) if f.paper_id else None
        if (only is not None and pid not in only) or paper is None or f.status != CorpusFileStatus.done \
                or paper.status.value != "done":
            continue
        tables = uow.sub.paper_tables(paper.id, domain)
        pub = f.publication_id
        revs = {k[2]: v for k, v in revs_all.items() if k[1] == pub}
        states = {k[2]: v for k, v in states_all.items() if k[1] == pub}
        extra = extra_all.get((domain, pub), [])
        eff = V2.export_tables(tables, revs, states, extra) if (revs or states or extra) else tables
        issues = [i for i in V2.effective_issues(tables, extra, states, revs) if i.pop("_state") == "open"]
        corrected = {rid for rid, r in revs.items() if r.get("corrected_value") is not None and r["status"] != "rejected"}
        out.append(PaperData(pid, pub, {**eff, "issues": issues}, issues, corrected))
    return RunCtx(domain, out, corpus.name or "")
