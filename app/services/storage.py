"""File storage abstraction — local filesystem or S3-compatible."""

from __future__ import annotations

import os
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Optional

import boto3
from botocore.config import Config as BotoConfig

from app.config import get_settings


class StorageBackend(ABC):
    """Abstract interface for file storage."""

    @abstractmethod
    def save(self, data: BinaryIO, key: str, content_type: str = "") -> str:
        """Save file data and return storage URL/path."""
        pass

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Get URL for accessing the file."""
        pass

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete a file from storage."""
        pass

    @abstractmethod
    def get_local_path(self, key: str) -> str:
        """Return a local filesystem path — downloads from S3 if needed."""
        pass


class LocalStorage(StorageBackend):
    """Store files on the local filesystem."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, data: BinaryIO, key: str, content_type: str = "") -> str:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            shutil.copyfileobj(data, f)
        return str(dest)

    def get_url(self, key: str) -> str:
        """Return signed URL for secure download."""
        from itsdangerous import URLSafeTimedSerializer
        settings = get_settings()
        signing_key = settings.url_signing_key or settings.secret_key
        serializer = URLSafeTimedSerializer(signing_key)
        token = serializer.dumps({"key": key})
        return f"/api/v1/download/{token}"

    def delete(self, key: str) -> None:
        path = self.root / key
        if path.exists():
            path.unlink()

    def get_local_path(self, key: str) -> str:
        return str(self.root / key)


class S3Storage(StorageBackend):
    """Store files in S3-compatible object storage."""

    def __init__(
        self,
        bucket: str,
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        endpoint_url: Optional[str] = None,
    ):
        self.bucket = bucket
        self._tmp_dir = Path("/tmp/tiktok_engine_cache")
        self._tmp_dir.mkdir(parents=True, exist_ok=True)

        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key

        self.s3 = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=BotoConfig(signature_version="s3v4"),
            **session_kwargs,
        )

    def save(self, data: BinaryIO, key: str, content_type: str = "") -> str:
        extra = {}
        if content_type:
            extra["ContentType"] = content_type
        self.s3.upload_fileobj(data, self.bucket, key, ExtraArgs=extra or None)
        return f"s3://{self.bucket}/{key}"

    def get_url(self, key: str) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=3600,
        )

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def get_local_path(self, key: str) -> str:
        local = self._tmp_dir / key.replace("/", "_")
        if not local.exists():
            local.parent.mkdir(parents=True, exist_ok=True)
            self.s3.download_file(self.bucket, key, str(local))
        return str(local)


def get_storage() -> StorageBackend:
    """Factory — returns the configured storage backend."""
    settings = get_settings()
    if settings.storage_backend == "s3":
        return S3Storage(
            bucket=settings.s3_bucket,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            endpoint_url=settings.s3_endpoint_url,
        )
    return LocalStorage(settings.storage_local_root)


def make_asset_key(project_id: str, asset_type: str, filename: str) -> str:
    """Generate a unique storage key: projects/<id>/<type>/<uuid>_<filename>.

    Sanitises the filename to prevent path traversal attacks.
    """
    import re
    unique = uuid.uuid4().hex[:12]
    # Strip directory components and dangerous chars
    safe_name = os.path.basename(filename)
    safe_name = re.sub(r'[^\w.\-]', '_', safe_name)
    if not safe_name:
        safe_name = "upload"
    return f"projects/{project_id}/{asset_type}/{unique}_{safe_name}"
