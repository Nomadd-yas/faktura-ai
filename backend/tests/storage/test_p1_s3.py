"""Этап B: S3FileStore на MinIO (FAKTURA_TEST_S3_URL; общий контракт - в test_p0_filestore.py)."""
from __future__ import annotations

import os
import uuid

import pytest

from faktura.domain.errors import AppError
from faktura.storage import S3FileStore

pytestmark = pytest.mark.s3


@pytest.fixture
def s3():
    return S3FileStore(endpoint=os.environ["FAKTURA_TEST_S3_URL"],
                       bucket=os.environ.get("FAKTURA_TEST_S3_BUCKET", "faktura-test"),
                       access_key=os.environ.get("FAKTURA_TEST_S3_KEY", "minioadmin"),
                       secret_key=os.environ.get("FAKTURA_TEST_S3_SECRET", "minioadmin"))


@pytest.fixture
def key():
    return f"t{uuid.uuid4().hex[:8]}/obj.pdf"


def test_signed_url_returned_and_fetchable(s3, key):
    import urllib.request
    s3.put_bytes(key, b"%PDF-signed")
    url = s3.signed_url(key, 60)
    assert isinstance(url, str) and url.startswith("http")
    with urllib.request.urlopen(url, timeout=10) as r:
        assert r.read() == b"%PDF-signed"


def test_signed_url_ttl_in_url(s3, key):
    s3.put_bytes(key, b"x")
    assert "Expires" in s3.signed_url(key, 60) or "X-Amz-Expires=60" in s3.signed_url(key, 60)


def test_content_type_stored(s3, key):
    import boto3
    s3.put_bytes(key, b"{}", content_type="application/json")
    c = boto3.client("s3", endpoint_url=s3.endpoint,
                     aws_access_key_id=os.environ.get("FAKTURA_TEST_S3_KEY", "minioadmin"),
                     aws_secret_access_key=os.environ.get("FAKTURA_TEST_S3_SECRET", "minioadmin"))
    assert c.head_object(Bucket=s3.bucket, Key=key)["ContentType"] == "application/json"


def test_key_traversal_rejected(s3):
    with pytest.raises((ValueError, AppError)):
        s3.put_bytes("../escape", b"x")


def test_wrong_credentials_fail():
    bad = S3FileStore(endpoint=os.environ["FAKTURA_TEST_S3_URL"], bucket="faktura-test", access_key="bad",
                      secret_key="bad")
    with pytest.raises(Exception) as ei:
        bad.put_bytes("x", b"1")
    assert ei.type is not NotImplementedError


def test_unreachable_endpoint_fails_fast():
    dead = S3FileStore(endpoint="http://127.0.0.1:1", bucket="b", access_key="k", secret_key="s")
    with pytest.raises(Exception) as ei:
        dead.exists("k")
    assert ei.type is not NotImplementedError
