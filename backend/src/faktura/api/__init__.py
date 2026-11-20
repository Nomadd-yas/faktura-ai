"""Общая конфигурация API (план 4.5)."""
API_PREFIX = "/api/v1"
# у каждого пакета есть ROUTERS: list[APIRouter]; api.base.create_app подключает их лениво через importlib
ROUTER_MODULES = ("faktura.api.corpus", "faktura.api.papers", "faktura.api.review")
