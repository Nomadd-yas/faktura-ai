"""Выгрузки: /runs/{id}/dataset.json, /runs/{id}/dataset.xlsx, /submission.zip по всем разборам (CORE_API §4g)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ...domain.entities import CurrentUser
from ...domain.errors import AppError
from ...domain.registry import DOMAINS
from ...domain.services import Services
from ..base.deps import current_user, get_services
from .exports import collect_tables, dataset_json_response, dataset_xlsx_response, submission_response
from .passports import run_passport
from .views import get_corpus, run_id_of, user_corpora

router = APIRouter(tags=["export"])


@router.get("/runs/{run_id}/dataset.json", summary="Все 7 таблиц разбора одним JSON (пустое - null)")
def dataset_json(run_id: str, partial: bool = False, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        col = collect_tables(uow, [corpus], partial)
        stem = run_id_of(corpus.id)
    return dataset_json_response(col, stem)


@router.get("/runs/{run_id}/dataset.xlsx", summary="Таблица results одним листом Excel (только значения)")
def dataset_xlsx(run_id: str, partial: bool = False, user: CurrentUser = Depends(current_user),
                 services: Services = Depends(get_services)):
    with services.db.uow() as uow:
        corpus = get_corpus(uow, user, run_id)
        col = collect_tables(uow, [corpus], partial)
        stem = run_id_of(corpus.id)
    return dataset_xlsx_response(col, stem)


@router.get("/submission.zip", summary="Набор для сдачи по всем разборам области: 7 CSV, PASSPORT.md, passport.json")
def all_runs_submission(domain: str = Query(...), partial: bool = False, check: bool = False,
                        user: CurrentUser = Depends(current_user),
                        services: Services = Depends(get_services)) -> Response:
    if domain not in DOMAINS:
        raise AppError("domain_not_supported", field="domain")
    with services.db.uow() as uow:
        corpora = [c for c in user_corpora(uow, user) if c.collection == domain]  # новые первыми
        if not corpora:
            raise AppError("no_files", "Нет разборов в этой области")
        col = collect_tables(uow, corpora, partial)
        passport = run_passport(services, col.datas)
    return submission_response(services, col, passport, "все разборы", check)
