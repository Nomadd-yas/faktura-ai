"""Middleware X-Request-Id, единый формат ошибок, CORS, лимит 600/мин (api_contract §1.3, §1.5, §1.7)."""
from __future__ import annotations

import re

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from faktura.api import API_PREFIX
from faktura.domain.errors import AppError

ORIGIN = "http://localhost:5200"
EXPOSE = {"x-action-id", "x-request-id", "content-disposition", "x-check-status", "x-faktura-stub"}
ALLOW = {"authorization", "content-type", "x-request-id", "last-event-id", "idempotency-key"}


def _csv(value: str) -> set[str]:
    return {p.strip().lower() for p in value.split(",") if p.strip()}


# --- X-Request-Id ------------------------------------------------------------------------------

class TestRequestId:
    def test_generated_when_absent(self, client, h):
        r = client.get(h.url("/health"))
        rid = r.headers.get("X-Request-Id")
        assert rid and len(rid) >= 8







# --- единый формат ошибок ----------------------------------------------------------------------



# --- CORS --------------------------------------------------------------------------------------



# --- лимит запросов ----------------------------------------------------------------------------

