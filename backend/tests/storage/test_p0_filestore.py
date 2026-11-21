"""Этап A: LocalFileStore (и тот же контракт для S3FileStore по маркеру s3), build_file_store."""
from __future__ import annotations

import io
import os
from pathlib import Path

import pytest

from faktura.config import Settings
from faktura.domain.errors import AppError
from faktura.domain.ports import FileStore
from faktura.storage import LocalFileStore, S3FileStore, build_file_store

MISSING = (FileNotFoundError, KeyError, AppError, OSError)
BAD_KEY = (ValueError, AppError, PermissionError)


def _s3_store():
    url = os.environ["FAKTURA_TEST_S3_URL"]
    return S3FileStore(endpoint=url, bucket=os.environ.get("FAKTURA_TEST_S3_BUCKET", "faktura-test"),
                       access_key=os.environ.get("FAKTURA_TEST_S3_KEY", "minioadmin"),
                       secret_key=os.environ.get("FAKTURA_TEST_S3_SECRET", "minioadmin"))


@pytest.fixture(params=["local"])
def store(request, tmp_path):
    if request.param == "local":
        return LocalFileStore(tmp_path / "root")
    return _s3_store()


@pytest.fixture
def prefix(request):
    """Уникальный префикс ключей, чтобы тесты на общем бакете не пересекались."""
    import uuid
    return f"t{uuid.uuid4().hex[:8]}"








































# ---- только LocalFileStore -------------------------------------------------------------------

@pytest.fixture
def local(tmp_path) -> LocalFileStore:
    return LocalFileStore(tmp_path / "root")


@pytest.mark.parametrize("key", ["../escape.txt", "/abs/escape.txt"])
def test_local_rejects_keys_outside_root(local, tmp_path, key):
    with pytest.raises(BAD_KEY):
        local.put_bytes(key, b"x")
    assert not (tmp_path / "escape.txt").exists()
    assert not Path("/abs/escape.txt").exists()






















# ---- build_file_store ------------------------------------------------------------------------





