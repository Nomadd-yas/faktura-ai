"""API: приложение, ошибки, вход, пользователи, общие механизмы (план 5.7).

Для модулей api.corpus|papers|review (импортируют только api.base, не наоборот):

* ``deps`` - ``current_user``, ``admin_user``, ``get_services``, ``get_uow``, ``page_params`` / ``make_page_params``,
  ``page_response``, ``mutation_response`` (``action_id`` + ``X-Action-Id``), ``stub_response``
  (``X-Faktura-Stub: 1``), ``idempotent`` / ``remember_idempotent`` / ``replay_response``, ``upload_rate_limit``.
* ``serializers`` - единая форма сущностей контракта §3 (``*_out``; у каждого есть ``extra`` для полей модуля).
* Ошибки - ``raise faktura.domain.errors.AppError(code, message, field=..., details=...)``; формат ответа,
  ``X-Request-Id``, CORS и 400 вместо 422 обеспечивает приложение.
* Роутеры - ``ROUTERS: list[APIRouter]`` в пакете модуля, пути без префикса ``/api/v1`` (его добавляет create_app).
"""
from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
