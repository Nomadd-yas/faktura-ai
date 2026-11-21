"""Цитирования статей разбора по OpenAlex (CORE_API §4m): сама статья, кто её цитирует, связанные работы.

Для каждой разобранной статьи: поиск по DOI из publications, иначе по названию (+ год). Результат статьи кэшируется
в процессе на сутки (ключ - DOI, иначе название), чтобы вкладка «Цитирования» открывалась быстро и не упиралась в
лимиты OpenAlex. В набор для сдачи и паспорт ничего отсюда не попадает."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .openalex import OpenAlex, SourcesError, norm_doi, normalize

DAY_S = 24 * 3600.0
CITES_N = 10
RELATED_N = 10
WORKERS = 6  # параллельные статьи: OpenAlex допускает ~10 запросов в секунду
UNAVAILABLE = "Научные базы сейчас недоступны"

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def paper_key(pub: dict) -> str | None:
    """Ключ кэша статьи: DOI, иначе название; None - искать не по чему (статья пропускается)."""
    doi = norm_doi(pub.get("DOI") or pub.get("doi"))
    if doi:
        return f"doi:{doi}"
    title = " ".join(str(pub.get("title") or "").split()).lower()
    return f"title:{title}" if title else None


def _year(v: Any) -> int | None:
    try:
        return int(str(v).strip()[:4])
    except (TypeError, ValueError):
        return None


def paper_sources(client: OpenAlex, pub: dict, *, clock=time.monotonic) -> list[dict]:
    """Работы OpenAlex для одной статьи: [this] + до 10 cites (самые цитируемые) + до 10 related. SourcesError наружу."""
    key = paper_key(pub)
    if key is None:
        return []
    now = clock()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < DAY_S:
            return hit[1]
    work = client.find_work(doi=pub.get("DOI") or pub.get("doi"), title=pub.get("title"), year=_year(pub.get("year")))
    out: list[dict] = []
    if work:
        out.append(normalize(work, "this"))
        out += client.cited_by(work, CITES_N)
        out += client.related(work, RELATED_N)
    with _lock:
        _cache[key] = (now, out)
    return out


def run_citations(client: OpenAlex | None, papers: list[tuple[str, dict]]) -> tuple[list[dict], str | None]:
    """[(pid, строка publications)] -> ([{pid, sources:[… с pid]}], warning). Ошибка OpenAlex у любой статьи ->
    warning «Научные базы сейчас недоступны» (её sources пустые, остальные статьи отдаются)."""
    if client is None:
        return [{"pid": pid, "sources": []} for pid, _ in papers], UNAVAILABLE
    failed = False

    def one(item: tuple[str, dict]) -> dict:
        nonlocal failed
        pid, pub = item
        try:
            got = paper_sources(client, pub)
        except SourcesError:
            failed = True
            got = []
        return {"pid": pid, "sources": [{**s, "pid": pid} for s in got]}

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        data = list(ex.map(one, papers))
    return data, (UNAVAILABLE if failed else None)


def clear_cache() -> None:
    with _lock:
        _cache.clear()
