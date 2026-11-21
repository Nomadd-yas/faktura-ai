"""API v2 (спека BACKEND_ONLY §9): правки значений, состояния замечаний, журнал и отмена, статистика замечаний.

PATCH  /runs/{id}/papers/{domain}/{pid}/results/{rid}/review   {status, corrected_value?}
POST   /runs/{id}/reviews/batch                               {items: [{domain, pid, rid, status}]}
DELETE /runs/{id}/reviews
PATCH  /runs/{id}/papers/{domain}/{pid}/issues/{iid}          {state: open|fixed|ok}
GET    /runs/{id}/papers/{domain}/{pid}/review                 правки и состояния статьи
GET    /history?run=           POST /history/undo?run=          POST /actions/{id}/undo
GET    /issues/stats?run=
Каждая мутация пишет действие в журнал (`action_id` в теле и заголовке X-Action-Id).
"""
from __future__ import annotations

from time import perf_counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.services import Services
from ...review import runs_v2 as V
from ..base.deps import current_user, get_services, mutation_response
from ..corpus.views import get_corpus, run_id_of

router = APIRouter(tags=["review"])


class ReviewIn(BaseModel):
    status: Literal["new", "accepted", "rejected"]
    corrected_value: float | int | str | None = None


class BatchItem(BaseModel):
    domain: str
    pid: str
    rid: str
    status: Literal["new", "accepted", "rejected"]


class BatchIn(BaseModel):
    items: list[BatchItem] = Field(default_factory=list)


class IssueStateIn(BaseModel):
    state: Literal["open", "fixed", "ok"]


# ------------------------------------------------------------------ помощники
def _paper(uow, user, run_id: str, domain: str, pid: str):
    """(corpus, paper, tables) статьи разбора; статья должна быть разобрана (done)."""
    from ..papers.routes_data import _find
    corpus, paper, f = _find(uow, user, pid, run_id, domain)
    if corpus.collection != domain:
        raise AppError("not_found", f"Статья {pid} не найдена")
    if paper is None or getattr(paper.status, "value", paper.status) != "done":
        raise AppError("conflict", "Статья ещё не разобрана или разобрана с ошибкой")
    tables = uow.sub.paper_tables(paper.id, domain)
    pub = (tables.get("publications") or [{}])[0].get("publication_id") or pid
    return corpus, paper, tables, pub


def _who(user: CurrentUser) -> tuple[str, str | None]:
    return user.id, getattr(user, "name", None) or getattr(user, "email", None)


def _open_set(conn, corpus_id: str, domain: str, pid: str, tables: dict) -> set[str]:
    revs = {k[2]: v for k, v in V.reviews_of(conn, corpus_id, domain, pid).items()}
    states = {k[2]: v for k, v in V.states_of(conn, corpus_id, domain, pid).items()}
    extra = V.extra_of(conn, corpus_id, domain, pid).get((domain, pid), [])
    return {i["issue_id"] for i in V.effective_issues(tables, extra, states, revs) if i["_state"] == "open"}


def _record(uow, user, corpus_id: str, action: str, label: str, changes: list[dict], paper_id: str | None = None):
    return uow.actions.record(user, V.ACTION_PREFIX + action, label, changes, paper_id=paper_id, group_id=corpus_id)


def _set_review(uow, tr: V.Tracker, user, corpus, domain: str, pid: str, tables: dict, rid: str, status: str,
                corrected: Any, *, set_value: bool) -> tuple[str, dict]:
    """Правка одной строки results; повтор перенаправляется на основную запись. -> (applied_to, review)."""
    rows = {r.get("result_id"): r for r in tables.get("results") or []}
    if rid not in rows:
        raise AppError("not_found", f"Строка {rid} не найдена")
    main = rows[rid].get("duplicate_of") or ""
    target = main if main and main in rows else rid
    who, name = _who(user)
    key = {"corpus_id": corpus.id, "domain": domain, "publication_id": pid, "result_id": target}
    prev = V.reviews_of(uow.conn, corpus.id, domain, pid).get((domain, pid, target))
    value = V.parse_value(corrected) if set_value else (prev or {}).get("corrected_value")
    if status == "new" and value is None:
        tr.put("run_reviews", key, None)
        return target, V.review_out(None, target)
    row = {"status": status, "corrected_value": value, "at": V._now(), "who": who, "who_name": name}
    tr.put("run_reviews", key, row)
    return target, V.review_out(row, target)


# ------------------------------------------------------------------ правки
@router.patch("/runs/{run_id}/papers/{domain}/{pid}/results/{rid}/review", summary="Правка значения (v2)")
def patch_review(run_id: str, domain: str, pid: str, rid: str, body: ReviewIn,
                 user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    t0 = perf_counter()
    with services.db.uow() as uow:
        corpus, paper, tables, pub = _paper(uow, user, run_id, domain, pid)
        before = _open_set(uow.conn, corpus.id, domain, pub, tables)
        tr = V.Tracker(uow.conn)
        applied, review = _set_review(uow, tr, user, corpus, domain, pub, tables, rid, body.status,
                                      body.corrected_value, set_value="corrected_value" in body.model_fields_set)
        V.recompute(uow.conn, tr, corpus.id, domain, pub, tables, user.id)
        after = _open_set(uow.conn, corpus.id, domain, pub, tables)
        label = f"{pid} {applied}: {body.status}" + (f", значение {review['corrected_value']}"
                                                       if review["corrected_value"] is not None else "")
        action = _record(uow, user, corpus.id, "review", label, tr.changes(), paper.id)
    payload = {"review": review, "applied_to": applied if applied != rid else None,
               "issues_changed": {"opened": sorted(after - before), "closed": sorted(before - after)},
               "elapsed_ms": round((perf_counter() - t0) * 1000, 1)}
    return mutation_response(payload, action)


@router.post("/runs/{run_id}/reviews/batch", summary="Пакетная правка статусов (v2)")
def batch_reviews(run_id: str, body: BatchIn, user: CurrentUser = Depends(current_user),
                  services: Services = Depends(get_services)):
    if not body.items:
        raise AppError("validation_error", "Пустой список items", field="items")
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        tr = V.Tracker(uow.conn)
        papers: dict[tuple, tuple] = {}
        for it in body.items:
            k = (it.domain, it.pid)
            if k not in papers:
                c, paper, tables, pub = _paper(uow, user, run_id, it.domain, it.pid)
                papers[k] = (tables, pub)
            tables, pub = papers[k]
            _set_review(uow, tr, user, corpus, it.domain, pub, tables, it.rid, it.status, None, set_value=False)
        for (domain, _), (tables, pub) in papers.items():
            V.recompute(uow.conn, tr, corpus.id, domain, pub, tables, user.id)
        action = _record(uow, user, corpus.id, "batch", f"Пакетная правка: {len(body.items)}", tr.changes())
    return mutation_response({"count": len(body.items)}, action)


@router.delete("/runs/{run_id}/reviews", summary="Сбросить все правки разбора (v2)")
def reset_reviews(run_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        tr = V.Tracker(uow.conn)
        n = 0
        for (domain, pid, rid) in V.reviews_of(uow.conn, corpus.id):
            tr.put("run_reviews", {"corpus_id": corpus.id, "domain": domain, "publication_id": pid,
                                   "result_id": rid}, None)
            n += 1
        for (domain, pid, iid) in V.states_of(uow.conn, corpus.id):
            tr.put("run_issue_states", {"corpus_id": corpus.id, "domain": domain, "publication_id": pid,
                                        "issue_id": iid}, None)
        for (domain, pid), rows in V.extra_of(uow.conn, corpus.id).items():
            for d in rows:
                tr.put("run_extra_issues", {"corpus_id": corpus.id, "domain": domain, "publication_id": pid,
                                            "issue_id": d.get("issue_id")}, None)
        action = _record(uow, user, corpus.id, "reset", f"Сброс правок разбора: {n}", tr.changes())
    return mutation_response({"count": n}, action)


@router.get("/runs/{run_id}/papers/{domain}/{pid}/review", summary="Правки и состояния замечаний статьи (v2)")
def paper_review(run_id: str, domain: str, pid: str, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus, paper, tables, pub = _paper(uow, user, run_id, domain, pid)
        revs = {k[2]: v for k, v in V.reviews_of(uow.conn, corpus.id, domain, pub).items()}
        states = {k[2]: v for k, v in V.states_of(uow.conn, corpus.id, domain, pub).items()}
        extra = V.extra_of(uow.conn, corpus.id, domain, pub).get((domain, pub), [])
        issues = V.effective_issues(tables, extra, states, revs)
    return {"reviews": {rid: V.review_out(r, rid) for rid, r in revs.items()},
            "issues": [{"issue_id": i["issue_id"], "state": i["_state"], "auto": i["_auto"],
                        "extra": i["issue_id"] in {e.get("issue_id") for e in extra}} for i in issues],
            "extra_issues": extra,
            "open_issue_count": sum(1 for i in issues if i["_state"] == "open")}


# ------------------------------------------------------------------ замечания
@router.patch("/runs/{run_id}/papers/{domain}/{pid}/issues/{iid}", summary="Состояние замечания (v2)")
def patch_issue(run_id: str, domain: str, pid: str, iid: str, body: IssueStateIn,
                user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus, paper, tables, pub = _paper(uow, user, run_id, domain, pid)
        revs = {k[2]: v for k, v in V.reviews_of(uow.conn, corpus.id, domain, pub).items()}
        states = {k[2]: v for k, v in V.states_of(uow.conn, corpus.id, domain, pub).items()}
        extra = V.extra_of(uow.conn, corpus.id, domain, pub).get((domain, pub), [])
        issues = {i["issue_id"]: i for i in V.effective_issues(tables, extra, states, revs)}
        if iid not in issues:
            raise AppError("not_found", f"Замечание {iid} не найдено")
        cur = states.get(iid)
        if body.state == "open" and cur is not None and cur["state"] == "fixed" and cur["auto"]:
            raise AppError("issue_auto_fixed")
        tr = V.Tracker(uow.conn)
        key = {"corpus_id": corpus.id, "domain": domain, "publication_id": pub, "issue_id": iid}
        tr.put("run_issue_states", key, None if body.state == "open" else
               {"state": body.state, "auto": False, "at": V._now(), "who": user.id})
        action = _record(uow, user, corpus.id, "issue", f"{pid} {iid}: {body.state}", tr.changes(), paper.id)
    return mutation_response({"issue": {"issue_id": iid, "state": body.state, "auto": False}}, action)


@router.get("/issues/stats", summary="Замечания разбора по проверкам (v2)")
def issues_stats(run: str = Query(...), user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    from ..corpus.views import RunData
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run)
        d = RunData(uow, corpus)
        domain = corpus.collection
        revs_all = V.reviews_of(uow.conn, corpus.id, domain)
        states_all = V.states_of(uow.conn, corpus.id, domain)
        extra_all = V.extra_of(uow.conn, corpus.id, domain)
        agg: dict[str, dict[str, Any]] = {}
        small = uow.sub.tables_of_corpus(corpus.id, domain, ("publications", "issues"))  # 1 запрос вместо статья-за-статьёй
        for p in d.papers.values():
            tables = small.get(p.id) or {"publications": [], "issues": []}
            pub = (tables.get("publications") or [{}])[0].get("publication_id") or p.publication_id
            revs = {k[2]: v for k, v in revs_all.items() if k[1] == pub}
            states = {k[2]: v for k, v in states_all.items() if k[1] == pub}
            for i in V.effective_issues(tables, extra_all.get((domain, pub), []), states, revs):
                if i["_state"] != "open":
                    continue
                a = agg.setdefault(i.get("check_name") or "", {"check_name": i.get("check_name") or "", "count": 0,
                                                              "papers": set()})
                a["count"] += 1
                a["papers"].add(pub)
    data = sorted(({**a, "papers": len(a["papers"])} for a in agg.values()), key=lambda a: (-a["count"], a["check_name"]))
    return {"data": data}


# ------------------------------------------------------------------ журнал и отмена
def _action_view(a) -> dict:
    return {"id": a.id, "action": a.action.removeprefix(V.ACTION_PREFIX), "label": a.label,
            "run_id": run_id_of(a.group_id) if a.group_id else None, "undone": a.undone,
            "at": a.created_at.isoformat() if a.created_at else None, "changes": len(a.changes or [])}


@router.get("/history", summary="Журнал правок (v2)")
def history(run: str | None = None, limit: int = Query(50, ge=1, le=200), user: CurrentUser = Depends(current_user),
            services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus_id = get_corpus(uow, user, run).id if run else None
        rows = [a for a in uow.actions.list(user.workspace_id, user_id=user.id, limit=500)
                if a.action.startswith(V.ACTION_PREFIX) and (corpus_id is None or a.group_id == corpus_id)]
    return {"data": [_action_view(a) for a in rows[:limit]]}


def _undo(uow, user, a) -> dict:
    if a is None or a.user_id != user.id or not a.action.startswith(V.ACTION_PREFIX):
        raise AppError("not_found", "Действие не найдено")
    if a.undone:
        raise AppError("conflict", "Действие уже отменено")
    get_corpus(uow, user, run_id_of(a.group_id or ""))  # разбор ещё доступен пользователю
    V.apply_changes(uow.conn, a.changes or [], use="before")
    uow.actions.mark_undone(a.id, by=user.id)
    undo = uow.actions.record(user, "undo", f"Отмена: {a.label}", [], group_id=a.group_id, undoable=False,
                              undo_of=a.id)
    return {"undone": _action_view(uow.actions.get(a.id)), "undo_action_id": undo.id}


@router.post("/actions/{action_id}/undo", summary="Отменить действие (v2)")
def undo_action(action_id: str, user: CurrentUser = Depends(current_user), services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        return _undo(uow, user, uow.actions.get(action_id))


@router.post("/history/undo", summary="Отменить последнее действие (v2)")
def undo_last(run: str | None = None, user: CurrentUser = Depends(current_user),
              services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus_id = get_corpus(uow, user, run).id if run else None
        cand = [a for a in uow.actions.list(user.workspace_id, user_id=user.id, limit=200)
                if a.action.startswith(V.ACTION_PREFIX) and not a.undone
                and (corpus_id is None or a.group_id == corpus_id)]
        if not cand:
            raise AppError("not_found", "Нечего отменять")
        return _undo(uow, user, cand[0])
