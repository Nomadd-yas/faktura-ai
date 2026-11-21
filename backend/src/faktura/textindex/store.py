"""SQL-часть полнотекстового индекса: DDL (FTS5 / tsvector+GIN), запись страниц и чанков, поиск, чтение страницы."""
from __future__ import annotations

import logging
import re

import sqlalchemy as sa

from ..storage import schema as S
from .chunking import chunk_page, clean_text

log = logging.getLogger(__name__)

MAX_TERMS = 24
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
STOPWORDS = frozenset("""a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over
own same she should so some such than that the their theirs them then there these they this those through to too under until
up very was we were what when where which while who whom why will with would you your yours""".split())


def norm_corpus_id(corpus_id: str) -> str:
    cid = (corpus_id or "").strip()
    return "crp_" + cid[4:] if cid.startswith("run_") else cid


def query_terms(query: str) -> list[str]:
    """Свободный текст -> слова нижнего регистра без стоп-слов и знаков (без дублей, порядок сохраняется)."""
    out: list[str] = []
    for m in _TOKEN.finditer((query or "").casefold()):
        t = m.group(0)
        if t in STOPWORDS or (len(t) < 2 and not t.isdigit()) or t in out:
            continue
        out.append(t)
        if len(out) >= MAX_TERMS:
            break
    return out


def _phrases(terms: list[str]) -> list[list[str]]:
    """Пары соседних слов и (при 3..8 словах) вся фраза - для усиления ранжирования."""
    ph = [terms[i:i + 2] for i in range(len(terms) - 1)]
    if 3 <= len(terms) <= 8:
        ph.append(list(terms))
    return ph


def fts_query(terms: list[str]) -> str:
    """Запрос FTS5: слова и фразы в двойных кавычках через OR (внутри только буквы/цифры - инъекция исключена)."""
    parts = [f'"{t}"' for t in terms] + [f'"{" ".join(p)}"' for p in _phrases(terms)]
    return " OR ".join(parts)


def tsquery(terms: list[str]) -> str:
    """Запрос to_tsquery: слова через `|` плюс фразы `<->` (слова содержат только буквы и цифры)."""
    def q(t: str) -> str:
        return "'" + t + "'"

    parts = [q(t) for t in terms] + ["(" + " <-> ".join(q(t) for t in p) + ")" for p in _phrases(terms)]
    return " | ".join(parts)


# --- DDL -------------------------------------------------------------------------------------------

def ensure_fts(engine: sa.Engine) -> None:
    """Идемпотентно: SQLite - виртуальная таблица FTS5 (внешнее содержимое) и триггеры; PostgreSQL - generated
    tsvector и GIN-индекс. Без FTS5 поиск работает сканом LIKE."""
    d = engine.dialect.name
    try:
        with engine.begin() as c:
            if d == "sqlite":
                c.execute(sa.text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS text_chunks_fts USING fts5(text, content='text_chunks', "
                    "content_rowid='seq', tokenize='porter unicode61 remove_diacritics 2')"))
                c.execute(sa.text(
                    "CREATE TRIGGER IF NOT EXISTS text_chunks_ai AFTER INSERT ON text_chunks BEGIN "
                    "INSERT INTO text_chunks_fts(rowid, text) VALUES (new.seq, new.text); END"))
                c.execute(sa.text(
                    "CREATE TRIGGER IF NOT EXISTS text_chunks_ad AFTER DELETE ON text_chunks BEGIN "
                    "INSERT INTO text_chunks_fts(text_chunks_fts, rowid, text) VALUES ('delete', old.seq, old.text); END"))
            elif d == "postgresql":
                c.execute(sa.text(
                    "ALTER TABLE text_chunks ADD COLUMN IF NOT EXISTS tsv tsvector "
                    "GENERATED ALWAYS AS (to_tsvector('english', text)) STORED"))
                c.execute(sa.text(
                    "CREATE INDEX IF NOT EXISTS ix_text_chunks_tsv ON text_chunks USING GIN (tsv)"))
    except Exception:  # noqa: BLE001 - нет FTS5 / прав: остаётся запасной поиск
        log.warning("Полнотекстовый индекс не создан, поиск по тексту через LIKE", exc_info=True)


def _mode(c: sa.Connection) -> str:
    d = c.dialect.name
    if d == "postgresql":
        has = c.execute(sa.text("SELECT 1 FROM information_schema.columns WHERE table_name='text_chunks' "
                                "AND column_name='tsv'")).first()
        return "pg" if has else "like"
    if d == "sqlite":
        has = c.execute(sa.text("SELECT 1 FROM sqlite_master WHERE name='text_chunks_fts'")).first()
        return "fts5" if has else "like"
    return "like"


# --- запись ----------------------------------------------------------------------------------------

def delete_paper(uow, paper_id: str) -> None:
    c = uow.conn
    c.execute(S.text_chunks.delete().where(S.text_chunks.c.paper_id == paper_id))
    c.execute(S.text_pages.delete().where(S.text_pages.c.paper_id == paper_id))


def delete_corpus(uow, corpus_id: str) -> None:
    c = uow.conn
    c.execute(S.text_chunks.delete().where(S.text_chunks.c.corpus_id == corpus_id))
    c.execute(S.text_pages.delete().where(S.text_pages.c.corpus_id == corpus_id))


def replace_paper(uow, corpus_id: str, paper_id: str, pid: str, pages: list[str]) -> int:
    """Заменяет страницы и чанки статьи. `pages` - тексты страниц, индекс 0 = страница 1."""
    corpus_id = norm_corpus_id(corpus_id)
    delete_paper(uow, paper_id)
    n_pages = len(pages)
    page_rows, chunk_rows = [], []
    for i, text in enumerate(pages, start=1):
        pieces = chunk_page(text)
        page_rows.append({"corpus_id": corpus_id, "paper_id": paper_id, "pid": pid, "page": i,
                          "total_pages": n_pages, "text": clean_text(text)})
        for k, piece in enumerate(pieces, start=1):
            chunk_rows.append({"id": f"{paper_id}:{i}:{k}", "corpus_id": corpus_id, "paper_id": paper_id,
                               "pid": pid, "page": i, "chunk_no": k, "text": piece})
    if page_rows:
        uow.conn.execute(S.text_pages.insert(), page_rows)
    if chunk_rows:
        uow.conn.execute(S.text_chunks.insert(), chunk_rows)
    return len(chunk_rows)


# --- чтение ----------------------------------------------------------------------------------------

def indexed_paper_ids(uow, corpus_id: str) -> set[str]:
    return {r[0] for r in uow.conn.execute(sa.select(S.text_pages.c.paper_id).where(
        S.text_pages.c.corpus_id == corpus_id).distinct())}


def read_page(uow, corpus_id: str, pid: str, page: int) -> dict | None:
    corpus_id = norm_corpus_id(corpus_id)
    t = S.text_pages
    alive = sa.select(S.papers.c.id).where(S.papers.c.corpus_id == corpus_id, S.papers.c.deleted_at.is_(None))
    r = uow.conn.execute(sa.select(t.c.pid, t.c.page, t.c.text, t.c.total_pages).where(
        t.c.corpus_id == corpus_id, t.c.pid == pid, t.c.page == int(page), t.c.paper_id.in_(alive))).first()
    if r is None:
        return None
    return {"pid": r.pid, "page": r.page, "text": r.text, "pages": r.total_pages}


def search(uow, corpus_id: str, query: str, pids: list[str] | None, k: int) -> list[dict]:
    corpus_id = norm_corpus_id(corpus_id)
    terms = query_terms(query)
    if not terms or k <= 0 or (pids is not None and not pids):
        return []
    c = uow.conn
    mode = _mode(c)
    where_pid = " AND c.pid IN :pids" if pids else ""
    alive = "AND c.paper_id IN (SELECT id FROM papers WHERE corpus_id = :cid AND deleted_at IS NULL)"
    params: dict = {"cid": corpus_id, "k": int(k)}
    if mode == "fts5":
        sql = ("SELECT c.id, c.pid, c.page, c.text, -bm25(text_chunks_fts) AS score FROM text_chunks_fts "
               "JOIN text_chunks c ON c.seq = text_chunks_fts.rowid "
               f"WHERE text_chunks_fts MATCH :q AND c.corpus_id = :cid {alive}{where_pid} "
               "ORDER BY bm25(text_chunks_fts), c.seq LIMIT :k")
        params["q"] = fts_query(terms)
    elif mode == "pg":
        sql = ("SELECT c.id, c.pid, c.page, c.text, ts_rank_cd(c.tsv, q.q) AS score "
               "FROM text_chunks c, to_tsquery('english', :q) AS q(q) "
               f"WHERE c.corpus_id = :cid AND c.tsv @@ q.q {alive}{where_pid} "
               "ORDER BY score DESC, c.seq LIMIT :k")
        params["q"] = tsquery(terms)
    else:
        return _like_search(uow, corpus_id, terms, pids, k)
    stmt = sa.text(sql)
    if pids:
        stmt = stmt.bindparams(sa.bindparam("pids", expanding=True))
        params["pids"] = list(pids)
    return [{"chunk_id": r.id, "pid": r.pid, "page": r.page, "text": r.text, "score": round(float(r.score), 4)}
            for r in c.execute(stmt, params)]


def _like_search(uow, corpus_id: str, terms: list[str], pids: list[str] | None, k: int) -> list[dict]:
    """Запасной поиск без FTS: LIKE по словам, ранжирование - число найденных слов + бонус за фразу."""
    t = S.text_chunks
    alive = sa.select(S.papers.c.id).where(S.papers.c.corpus_id == corpus_id, S.papers.c.deleted_at.is_(None))
    cond = sa.or_(*[sa.func.lower(t.c.text).like("%" + w + "%") for w in terms])
    q = sa.select(t.c.id, t.c.pid, t.c.page, t.c.text).where(t.c.corpus_id == corpus_id, t.c.paper_id.in_(alive), cond)
    if pids:
        q = q.where(t.c.pid.in_(list(pids)))
    phrases = [" ".join(p) for p in _phrases(terms)]
    scored = []
    for r in uow.conn.execute(q.order_by(t.c.seq)):
        low = r.text.casefold()
        s = sum(1.0 + min(low.count(w), 5) * 0.1 for w in terms if w in low) + sum(2.0 for p in phrases if p in low)
        scored.append({"chunk_id": r.id, "pid": r.pid, "page": r.page, "text": r.text, "score": round(s, 4)})
    scored.sort(key=lambda d: -d["score"])
    return scored[:k]
