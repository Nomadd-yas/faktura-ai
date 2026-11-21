"""Цитирования статей разбора (CORE_API §4m): GET /runs/{id}/citations - для вкладки «Цитирования» в «Данных».

Только владелец разбора (чужой - 404). Разбор ещё идёт - 200 по уже готовым статьям. Статьи не по теме (out),
без данных, вне области (in_scope=false) и без DOI и названия пропускаются."""
from __future__ import annotations

import anyio
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...agent.service import _sources
from ...ask.context import load_ctx
from ...domain.entities import CurrentUser
from ...domain.services import Services
from ...sources.citations import paper_key, run_citations
from ..base import auth
from ..base.deps import JSON_MEDIA_TYPE, current_user, get_context, get_services
from ..base.serializers import iso
from .views import get_corpus

router = APIRouter(tags=["runs"])


@router.get("/runs/{run_id}/citations", summary="Цитирования статей разбора (OpenAlex)")
async def run_citations_view(run_id: str, request: Request, user: CurrentUser = Depends(current_user),
                             services: Services = Depends(get_services)):
    """`{data: [{pid, sources: [Source + pid]}], generated_at, warning}`; Source - как в §4l, relation this|cites|related.
    OpenAlex недоступен - 200, пустые sources и warning «Научные базы сейчас недоступны»."""
    def papers() -> list[tuple[str, dict]]:
        with services.db.uow() as uow:
            run = load_ctx(uow, get_corpus(uow, user, run_id))
        out = []
        for p in run.papers:
            pub = (p.tables.get("publications") or [{}])[0]
            if p.in_scope and paper_key(pub) is not None:
                out.append((p.pid, pub))
        return out

    items = await anyio.to_thread.run_sync(papers)
    client = _sources(get_context(request))
    data, warning = await anyio.to_thread.run_sync(run_citations, client, items)
    return JSONResponse({"data": data, "generated_at": iso(auth.utcnow()), "warning": warning},
                        media_type=JSON_MEDIA_TYPE)


__all__ = ["router"]
