"""Полнотекстовый индекс статей прогона: поиск по тексту со ссылкой на страницы (docs/design/TEXT_INDEX.md)."""
from __future__ import annotations

from . import sources, store
from .chunking import chunk_page, clean_text

__all__ = ["search_text", "read_page", "index_paper", "chunk_page", "clean_text"]


def search_text(uow, corpus_id: str, query: str, *, pids: list[str] | None = None, k: int = 8) -> list[dict]:
    """Полнотекстовый поиск в одном прогоне (корпусе).
    -> [{"chunk_id", "pid", "page", "text", "score"}], лучшие первыми. Пустой запрос или нет индекса -> []."""
    cid = store.norm_corpus_id(corpus_id)
    if not store.query_terms(query):
        return []
    sources.lazy_index(uow, cid)
    return store.search(uow, cid, query, pids, k)


def read_page(uow, corpus_id: str, pid: str, page: int) -> dict | None:
    """Текст страницы целиком -> {"pid", "page", "text", "pages"} (pages - всего страниц статьи) или None."""
    cid = store.norm_corpus_id(corpus_id)
    r = store.read_page(uow, cid, pid, page)
    if r is None:
        sources.lazy_index(uow, cid)
        r = store.read_page(uow, cid, pid, page)
    return r


def index_paper(uow, corpus_id: str, paper_id: str, pid: str, pages: list[str]) -> int:
    """(Пере)индексирует статью по текстам страниц (индекс 0 = страница 1). -> число чанков."""
    return store.replace_paper(uow, corpus_id, paper_id, pid, pages)
