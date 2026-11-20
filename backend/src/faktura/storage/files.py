"""Файловое хранилище: диск и S3/MinIO (BACKEND §8.2). Реализуют протокол domain.ports.FileStore.

Ключ - относительный путь через «/» (например `ws_x/crp_y/D001.pdf`). Ключ не может выйти за корень:
пустые, абсолютные, с `..` за пределы корня, с NUL, с буквой диска или «:» - отвергаются (ValueError).
`.` и `..` внутри корня нормализуются (`a/./b/../c` -> `a/c`); обратная косая черта считается разделителем.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import BinaryIO

from ..config import Settings
from ..domain.errors import AppError
from ..domain.ports import FileStore

_CHUNK = 1024 * 1024


def normalize_key(key: str) -> str:
    """Проверка и нормализация ключа; ValueError, если ключ выходит за корень или недопустим."""
    if not isinstance(key, str) or not key or "\x00" in key:
        raise ValueError("Недопустимый ключ хранилища")
    k = key.replace("\\", "/")
    if k.startswith("/") or ":" in k:
        raise ValueError(f"Недопустимый ключ хранилища: {key!r}")
    parts: list[str] = []
    for seg in k.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if not parts:
                raise ValueError(f"Ключ выходит за корень хранилища: {key!r}")
            parts.pop()
            continue
        parts.append(seg)
    if not parts:
        raise ValueError(f"Пустой ключ хранилища: {key!r}")
    return "/".join(parts)


class LocalFileStore:
    """Файлы на диске под root. Ключи не выходят за корень root (в том числе через символические ссылки)."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        k = normalize_key(key)
        # корень создаётся до resolve(): на Windows resolve() несуществующего пути не раскрывает
        # короткие имена (ANDRE~1), и сравнение с уже созданным корнем ломается
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        p = (root / k)
        resolved = p.resolve()
        if resolved != root and root not in resolved.parents:
            raise PermissionError(f"Ключ выходит за корень хранилища: {key!r}")
        return resolved

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self._write(self._path(key), lambda f: f.write(data))

    def put_file(self, key: str, path: Path, content_type: str | None = None) -> None:
        dest = self._path(key)
        src = Path(path)
        with open(src, "rb") as fsrc:  # FileNotFoundError до создания временного файла
            self._write(dest, lambda f: shutil.copyfileobj(fsrc, f, _CHUNK))

    def _write(self, dest: Path, writer) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dest.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                writer(f)
            self._replace(tmp, dest)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _replace(tmp: str, dest: Path) -> None:
        # Windows: замена файла, который в этот момент заменяет другой поток, может дать PermissionError
        for attempt in range(50):
            try:
                os.replace(tmp, dest)
                return
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.01)

    def get_bytes(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p.read_bytes()

    def open(self, key: str) -> BinaryIO:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return open(p, "rb")

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def copy_to(self, key: str, path: Path) -> None:
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)

    def signed_url(self, key: str, ttl_s: int) -> str | None:
        self._path(key)
        return None  # файлы с диска отдаёт API (GET /papers/{id}/source)

    def local_path(self, key: str) -> Path:
        """Путь к файлу на диске (для PyMuPDF без копирования)."""
        p = self._path(key)
        if not p.is_file():
            raise FileNotFoundError(key)
        return p


class S3FileStore:
    """boto3, MinIO; тесты - по FAKTURA_TEST_S3_URL. Клиент и бакет создаются лениво."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 region: str | None = None, *, connect_timeout: float = 3.0, read_timeout: float = 60.0,
                 max_attempts: int = 2, create_bucket: bool = True) -> None:
        self.endpoint, self.bucket = endpoint, bucket
        self.region = region
        self._access_key, self._secret_key = access_key, secret_key
        self._timeouts = (connect_timeout, read_timeout, max_attempts)
        self._create_bucket = create_bucket
        self._client = None
        self._bucket_ready = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:  # без секретов
        return f"S3FileStore(endpoint={self.endpoint!r}, bucket={self.bucket!r})"

    @property
    def client(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        import boto3
                        from botocore.config import Config
                    except ImportError as e:  # pragma: no cover - зависит от окружения
                        raise AppError("internal_error", "Для S3 нужен пакет boto3 (pip install faktura[s3])") from e
                    ct, rt, attempts = self._timeouts
                    cfg = Config(connect_timeout=ct, read_timeout=rt, retries={"max_attempts": attempts},
                                 signature_version="s3v4", s3={"addressing_style": "path"})
                    self._client = boto3.client("s3", endpoint_url=self.endpoint, aws_access_key_id=self._access_key,
                                                aws_secret_access_key=self._secret_key,
                                                region_name=self.region or "us-east-1", config=cfg)
        return self._client

    @staticmethod
    def _key(key: str) -> str:
        return normalize_key(key)

    @staticmethod
    def _is_missing(e: Exception) -> bool:
        resp = getattr(e, "response", None) or {}
        code = str((resp.get("Error") or {}).get("Code", ""))
        status = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        return code in ("404", "NoSuchKey", "NotFound") or status == 404

    def _ensure_bucket(self) -> None:
        if self._bucket_ready or not self._create_bucket:
            return
        from botocore.exceptions import ClientError
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            if not self._is_missing(e):
                raise
            self.client.create_bucket(Bucket=self.bucket)
        self._bucket_ready = True

    def put_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        k = self._key(key)
        self._ensure_bucket()
        extra = {"ContentType": content_type} if content_type else {}
        self.client.put_object(Bucket=self.bucket, Key=k, Body=data, **extra)

    def put_file(self, key: str, path: Path, content_type: str | None = None) -> None:
        k = self._key(key)
        src = Path(path)
        if not src.is_file():
            raise FileNotFoundError(str(src))
        self._ensure_bucket()
        extra = {"ExtraArgs": {"ContentType": content_type}} if content_type else {}
        self.client.upload_file(str(src), self.bucket, k, **extra)

    def get_bytes(self, key: str) -> bytes:
        k = self._key(key)
        from botocore.exceptions import ClientError
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=k)
        except ClientError as e:
            if self._is_missing(e):
                raise FileNotFoundError(key) from e
            raise
        return obj["Body"].read()

    def open(self, key: str) -> BinaryIO:
        k = self._key(key)
        from botocore.exceptions import ClientError
        buf = tempfile.SpooledTemporaryFile(max_size=8 * _CHUNK)
        try:
            self.client.download_fileobj(self.bucket, k, buf)
        except ClientError as e:
            buf.close()
            if self._is_missing(e):
                raise FileNotFoundError(key) from e
            raise
        buf.seek(0)
        return buf  # type: ignore[return-value]

    def exists(self, key: str) -> bool:
        k = self._key(key)
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=k)
            return True
        except ClientError as e:
            if self._is_missing(e):
                return False
            raise

    def delete(self, key: str) -> None:
        k = self._key(key)
        from botocore.exceptions import ClientError
        try:
            self.client.delete_object(Bucket=self.bucket, Key=k)
        except ClientError as e:
            if not self._is_missing(e):
                raise

    def copy_to(self, key: str, path: Path) -> None:
        k = self._key(key)
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        from botocore.exceptions import ClientError
        try:
            with open(dest, "wb") as f:
                self.client.download_fileobj(self.bucket, k, f)
        except ClientError as e:
            dest.unlink(missing_ok=True)
            if self._is_missing(e):
                raise FileNotFoundError(key) from e
            raise

    def signed_url(self, key: str, ttl_s: int) -> str | None:
        k = self._key(key)
        return self.client.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": k},
                                                  ExpiresIn=int(ttl_s))


def build_file_store(settings: Settings) -> FileStore:
    """local -> LocalFileStore(storage_dir); s3 -> S3FileStore (нужны endpoint, bucket, ключи)."""
    if settings.storage_backend == "s3":
        missing = [n for n in ("s3_endpoint", "s3_bucket", "s3_access_key", "s3_secret_key")
                   if not getattr(settings, n)]
        if missing:
            raise ValueError("Для storage_backend=s3 не заданы: " + ", ".join(f"FAKTURA_{m.upper()}" for m in missing))
        return S3FileStore(endpoint=settings.s3_endpoint, bucket=settings.s3_bucket,  # type: ignore[arg-type]
                           access_key=settings.s3_access_key, secret_key=settings.s3_secret_key,  # type: ignore[arg-type]
                           region=settings.s3_region)
    return LocalFileStore(Path(settings.storage_dir))


__all__ = ["LocalFileStore", "S3FileStore", "build_file_store", "normalize_key"]
