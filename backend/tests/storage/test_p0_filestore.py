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


@pytest.fixture(params=["local", pytest.param("s3", marks=pytest.mark.s3)])
def store(request, tmp_path):
    if request.param == "local":
        return LocalFileStore(tmp_path / "root")
    return _s3_store()


@pytest.fixture
def prefix(request):
    """Уникальный префикс ключей, чтобы тесты на общем бакете не пересекались."""
    import uuid
    return f"t{uuid.uuid4().hex[:8]}"


def test_implements_protocol(store):
    assert isinstance(store, FileStore)


def test_put_get_bytes(store, prefix):
    store.put_bytes(f"{prefix}/a.bin", b"\x00\x01data")
    assert store.get_bytes(f"{prefix}/a.bin") == b"\x00\x01data"


def test_put_bytes_overwrites(store, prefix):
    k = f"{prefix}/a.txt"
    store.put_bytes(k, b"one")
    store.put_bytes(k, b"two-longer")
    assert store.get_bytes(k) == b"two-longer"


def test_put_empty_bytes(store, prefix):
    store.put_bytes(f"{prefix}/empty", b"")
    assert store.exists(f"{prefix}/empty") and store.get_bytes(f"{prefix}/empty") == b""


def test_put_bytes_content_type_accepted(store, prefix):
    store.put_bytes(f"{prefix}/x.pdf", b"%PDF", content_type="application/pdf")
    assert store.get_bytes(f"{prefix}/x.pdf") == b"%PDF"


def test_nested_keys_create_directories(store, prefix):
    k = f"{prefix}/ws/corpus/D001/deep/dir/file.pdf"
    store.put_bytes(k, b"x")
    assert store.exists(k)


def test_unicode_key(store, prefix):
    k = f"{prefix}/папка/статья №1.pdf"
    store.put_bytes(k, b"z")
    assert store.get_bytes(k) == b"z"


def test_put_file(store, prefix, tmp_path):
    src = tmp_path / "src.pdf"
    src.write_bytes(b"%PDF-1.4 content" * 1000)
    store.put_file(f"{prefix}/up.pdf", src, content_type="application/pdf")
    assert store.get_bytes(f"{prefix}/up.pdf") == src.read_bytes()
    assert src.exists()  # источник не удаляется


def test_put_file_missing_source(store, prefix, tmp_path):
    with pytest.raises((FileNotFoundError, OSError)):
        store.put_file(f"{prefix}/up.pdf", tmp_path / "nope.pdf")


def test_open_reads_stream(store, prefix):
    store.put_bytes(f"{prefix}/o.bin", b"abcdef")
    with store.open(f"{prefix}/o.bin") as f:
        assert f.read(3) == b"abc"
        assert f.read() == b"def"


def test_open_missing(store, prefix):
    with pytest.raises(MISSING):
        store.open(f"{prefix}/nope")


def test_get_bytes_missing(store, prefix):
    with pytest.raises(MISSING):
        store.get_bytes(f"{prefix}/nope")


def test_exists(store, prefix):
    assert store.exists(f"{prefix}/nope") is False
    store.put_bytes(f"{prefix}/yes", b"1")
    assert store.exists(f"{prefix}/yes") is True


def test_exists_false_for_directory_prefix(store, prefix):
    store.put_bytes(f"{prefix}/dir/file", b"1")
    assert store.exists(f"{prefix}/dir") is False


def test_delete(store, prefix):
    store.put_bytes(f"{prefix}/d", b"1")
    store.delete(f"{prefix}/d")
    assert store.exists(f"{prefix}/d") is False


def test_delete_missing_is_noop(store, prefix):
    store.delete(f"{prefix}/never-existed")  # идемпотентно, без исключения


def test_copy_to(store, prefix, tmp_path):
    store.put_bytes(f"{prefix}/c.pdf", b"payload")
    dest = tmp_path / "out" / "c.pdf"
    store.copy_to(f"{prefix}/c.pdf", dest)
    assert dest.read_bytes() == b"payload"
    assert store.exists(f"{prefix}/c.pdf")


def test_copy_to_missing_key(store, prefix, tmp_path):
    with pytest.raises(MISSING):
        store.copy_to(f"{prefix}/nope", tmp_path / "x")


def test_large_file_roundtrip(store, prefix, tmp_path):
    data = os.urandom(3 * 1024 * 1024)
    store.put_bytes(f"{prefix}/big", data)
    dest = tmp_path / "big.out"
    store.copy_to(f"{prefix}/big", dest)
    assert dest.read_bytes() == data


# ---- только LocalFileStore -------------------------------------------------------------------

@pytest.fixture
def local(tmp_path) -> LocalFileStore:
    return LocalFileStore(tmp_path / "root")


@pytest.mark.parametrize("key", ["../escape.txt", "a/../../escape.txt", "/abs/escape.txt", "..", "a/../..",
                                 "C:\\Windows\\x.txt", "..\\escape.txt", "a\\..\\..\\escape.txt", "", "\x00bad"])
def test_local_rejects_keys_outside_root(local, tmp_path, key):
    with pytest.raises(BAD_KEY):
        local.put_bytes(key, b"x")
    assert not (tmp_path / "escape.txt").exists()
    assert not Path("/abs/escape.txt").exists()


@pytest.mark.parametrize("op", ["get_bytes", "open", "exists", "delete"])
def test_local_all_operations_validate_key(local, op):
    with pytest.raises(BAD_KEY):
        getattr(local, op)("../outside")


def test_local_copy_to_validates_key(local, tmp_path):
    with pytest.raises(BAD_KEY):
        local.copy_to("../outside", tmp_path / "d")


def test_local_put_file_validates_key(local, tmp_path):
    src = tmp_path / "s"
    src.write_bytes(b"1")
    with pytest.raises(BAD_KEY):
        local.put_file("../outside", src)


def test_local_key_with_dot_segments_inside_root_normalized(local):
    local.put_bytes("a/./b/../c.txt", b"1")  # a/c.txt, остаётся внутри корня
    assert local.get_bytes("a/c.txt") == b"1"


def test_local_symlink_escape_rejected(local, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    link = local.root / "lnk"
    local.root.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен")
    with pytest.raises(BAD_KEY + (FileNotFoundError,)):
        local.get_bytes("lnk/secret.txt")


def test_local_creates_root_lazily(tmp_path):
    root = tmp_path / "new" / "root"
    s = LocalFileStore(root)
    s.put_bytes("k", b"1")
    assert (root / "k").read_bytes() == b"1"


def test_local_signed_url_is_none(local):
    local.put_bytes("k", b"1")
    assert local.signed_url("k", 60) is None


def test_local_open_returns_binary(local):
    local.put_bytes("k", b"abc")
    with local.open("k") as f:
        assert isinstance(f.read(), bytes)


def test_local_data_stored_under_root_only(local):
    local.put_bytes("x/y.bin", b"1")
    assert (local.root / "x" / "y.bin").is_file()


def test_local_concurrent_put_same_key_last_wins(local):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: local.put_bytes("race", bytes([i]) * 1000), range(8)))
    data = local.get_bytes("race")
    assert len(data) == 1000 and len(set(data)) == 1  # файл целый, без смешения записей


# ---- build_file_store ------------------------------------------------------------------------

def test_build_file_store_local(settings):
    s = build_file_store(settings)
    assert isinstance(s, LocalFileStore)
    s.put_bytes("k", b"1")
    assert (settings.storage_dir / "k").read_bytes() == b"1"


def test_build_file_store_s3(settings):
    st = Settings(_env_file=None, storage_backend="s3", s3_endpoint="http://localhost:9000", s3_bucket="b",
                  s3_access_key="k", s3_secret_key="s")
    s = build_file_store(st)
    assert isinstance(s, S3FileStore)
    assert s.bucket == "b" and s.endpoint == "http://localhost:9000"


@pytest.mark.parametrize("missing", ["s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key"])
def test_build_file_store_s3_requires_settings(missing):
    kw = dict(storage_backend="s3", s3_endpoint="http://localhost:9000", s3_bucket="b", s3_access_key="k",
              s3_secret_key="s")
    kw[missing] = None
    with pytest.raises((ValueError, AppError)):
        build_file_store(Settings(_env_file=None, **kw))
