"""Клиент OpenAlex (синхронный, httpx): поиск работы по DOI/названию, связанные, цитирующие, поиск по запросу.

Ключ (`FAKTURA_OPENALEX_KEY`) необязателен: без него OpenAlex отвечает, но с меньшим лимитом. `mailto` - вежливый пул.
В OpenAlex уходят только названия статей, DOI и поисковые запросы: ни имён, ни почт пользователей.
Результат каждой работы нормализуется в `{openalex_id, title, authors, year, venue, doi, url, oa_url, cited_by_count,
relation[, abstract]}`. Любая сетевая ошибка -> `SourcesError` (инструмент вернёт модели {error})."""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import httpx

log = logging.getLogger("faktura.sources")
API = "https://api.openalex.org"
TIMEOUT_S = 8.0
CACHE_TTL_S = 3600.0
CACHE_MAX = 256
MAX_N = 8
ABSTRACT_MAX = 400
SELECT = ("id,doi,title,display_name,publication_year,authorships,primary_location,open_access,cited_by_count,"
          "abstract_inverted_index,related_works")
RELATIONS = ("this", "related", "cites", "search")


class SourcesError(Exception):
    """OpenAlex недоступен или ответил ошибкой."""


def short_id(v: str) -> str:
    return (v or "").strip().rstrip("/").rsplit("/", 1)[-1]


def norm_doi(v: str | None) -> str:
    d = (v or "").strip()
    d = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:)", "", d, flags=re.I)
    return d.strip().lower()


def abstract_of(work: dict) -> str | None:
    inv = work.get("abstract_inverted_index")
    if not isinstance(inv, dict) or not inv:
        return None
    pos: dict[int, str] = {}
    for word, idx in inv.items():
        for i in idx or []:
            pos[i] = word
    if not pos:
        return None
    text = " ".join(pos[i] for i in sorted(pos))
    return text if len(text) <= ABSTRACT_MAX else text[:ABSTRACT_MAX - 1].rstrip() + "…"


def normalize(work: dict, relation: str) -> dict:
    wid = short_id(work.get("id") or "")
    doi = norm_doi(work.get("doi"))
    loc = work.get("primary_location") or {}
    landing = str(loc.get("landing_page_url") or "")
    names = [(a.get("author") or {}).get("display_name") or "" for a in work.get("authorships") or []]
    oa = work.get("open_access") or {}
    oa_url = oa.get("oa_url") or (loc.get("pdf_url") if oa.get("is_oa") else None)
    out = {"openalex_id": wid, "title": work.get("title") or work.get("display_name") or "Без названия",
           "authors": [n for n in names if n][:3], "year": work.get("publication_year"),
           "venue": ((loc.get("source") or {}).get("display_name")) or None, "doi": doi or None,
           "url": f"https://doi.org/{doi}" if doi else (landing or (f"https://openalex.org/{wid}" if wid else None)),
           "oa_url": oa_url or None, "cited_by_count": int(work.get("cited_by_count") or 0), "relation": relation}
    ab = abstract_of(work)
    if ab:
        out["abstract"] = ab
    return out


class OpenAlex:
    def __init__(self, key: str | None = None, mailto: str | None = None, *, timeout_s: float = TIMEOUT_S,
                 client: httpx.Client | None = None, clock=time.monotonic) -> None:
        self.key, self.mailto = (key or None), (mailto or None)
        self.http = client or httpx.Client(timeout=timeout_s, headers={"User-Agent": "faktura-ai"})
        self._cache: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self._clock = clock

    # --- HTTP + кэш ----------------------------------------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict:
        params = {**params, "select": SELECT}
        if self.mailto:
            params["mailto"] = self.mailto
        ck = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        now = self._clock()
        with self._lock:
            hit = self._cache.get(ck)
            if hit and now - hit[0] < CACHE_TTL_S:
                return hit[1]
        full = {**params, **({"api_key": self.key} if self.key else {})}  # ключ в кэш-ключ и логи не попадает
        log.info("OpenAlex GET %s", path)
        try:
            r = self.http.get(f"{API}{path}", params=full)
        except httpx.HTTPError as e:
            raise SourcesError("OpenAlex недоступен") from e
        if r.status_code == 404:
            return {}
        if r.status_code == 429:
            raise SourcesError("OpenAlex ограничил число запросов")
        if r.is_error:
            raise SourcesError(f"OpenAlex вернул ошибку {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise SourcesError("OpenAlex вернул не JSON") from e
        with self._lock:
            if len(self._cache) >= CACHE_MAX:
                self._cache.pop(next(iter(self._cache)))
            self._cache[ck] = (now, data)
        return data

    def _list(self, params: dict) -> list[dict]:
        return (self._get("/works", params).get("results")) or []

    # --- операции ------------------------------------------------------------------------------------------

    def find_work(self, doi: str | None = None, title: str | None = None, year: int | None = None) -> dict | None:
        """Сырая работа OpenAlex по DOI, иначе по названию (+ год: берётся совпадающий, иначе первая)."""
        d = norm_doi(doi)
        if d:
            hits = self._list({"filter": f"doi:{d}", "per-page": 1})
            if hits:
                return hits[0]
        t = " ".join((title or "").split())
        if not t:
            return None
        hits = self._list({"filter": f"title.search:{re.sub(r'[,|:]', ' ', t)[:200]}", "per-page": 5})
        if year:
            same = [w for w in hits if w.get("publication_year") == year]
            hits = same or hits
        return hits[0] if hits else None

    def related(self, work: dict, n: int = 5) -> list[dict]:
        ids = [short_id(x) for x in (work.get("related_works") or [])][:n]
        if not ids:
            return []
        got = {short_id(w.get("id") or "").lower(): w for w in self._list({"filter": "ids.openalex:" + "|".join(ids),
                                                                           "per-page": len(ids)})}
        return [normalize(got[i.lower()], "related") for i in ids if i.lower() in got]

    def cited_by(self, work: dict, n: int = 5) -> list[dict]:
        wid = short_id(work.get("id") or "")
        if not wid:
            return []
        return [normalize(w, "cites") for w in
                self._list({"filter": f"cites:{wid}", "sort": "cited_by_count:desc", "per-page": min(n, MAX_N)})][:n]

    def search(self, query: str, n: int = 5) -> list[dict]:
        q = " ".join((query or "").split())[:300]
        if not q:
            return []
        return [normalize(w, "search") for w in self._list({"search": q, "per-page": min(n, MAX_N)})][:n]


_clients: dict[tuple, OpenAlex] = {}


def build_client(settings: Any) -> OpenAlex | None:
    """Клиент по настройкам (один на ключ/mailto - общий кэш); None - отключён (`openalex_enabled=false`)."""
    if not getattr(settings, "openalex_enabled", True):
        return None
    k = (getattr(settings, "openalex_key", None) or None, getattr(settings, "openalex_mailto", None) or None)
    if k not in _clients:
        _clients[k] = OpenAlex(*k)
    return _clients[k]
