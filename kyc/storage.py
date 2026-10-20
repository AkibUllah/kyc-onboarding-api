"""Document storage backends behind a small protocol.

``LocalStorage`` writes under ``STORAGE_LOCAL_DIR`` (default) and is what the tests
use. ``S3Storage`` targets any S3-compatible endpoint (AWS, MinIO) and imports boto3
lazily so the dependency is only needed when the backend is selected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str) -> None: ...

    def load(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("storage key escapes the storage root")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


class S3Storage:
    def __init__(self, bucket: str, endpoint_url: str | None = None) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - exercised only with the s3 extra
            raise RuntimeError("S3 storage requires `pip install kyc-onboarding-api[s3]`") from exc
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url or None)

    def save(self, key: str, data: bytes, content_type: str) -> None:  # pragma: no cover
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def load(self, key: str) -> bytes:  # pragma: no cover
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:  # pragma: no cover
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:  # pragma: no cover
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False


_storage: Storage | None = None


def get_storage(settings: Settings | None = None) -> Storage:
    global _storage
    if _storage is None:
        settings = settings or get_settings()
        if settings.storage_backend == "s3":
            _storage = S3Storage(settings.s3_bucket, settings.s3_endpoint_url)
        else:
            _storage = LocalStorage(settings.storage_local_dir)
    return _storage


def set_storage(storage: Storage | None) -> None:
    """Override the process-wide storage (used by tests)."""
    global _storage
    _storage = storage
