"""Unit tests for storage services."""

import io
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services.storage import LocalStorage, S3Storage


@pytest.mark.unit
class TestLocalStorage:
    """Test local filesystem storage."""

    def test_save_file(self, temp_storage: Path):
        """Test saving a file to local storage."""
        storage = LocalStorage(root=temp_storage)
        
        data = io.BytesIO(b"test content")
        key = "test/file.txt"
        
        result = storage.save(data, key)
        
        assert result == str(temp_storage / key)
        assert (temp_storage / key).exists()
        assert (temp_storage / key).read_bytes() == b"test content"

    def test_get_url(self, temp_storage: Path):
        """Test getting a file URL."""
        storage = LocalStorage(root=temp_storage)
        
        key = "test/file.txt"
        url = storage.get_url(key)
        
        # Should return a signed download URL, not a direct path
        assert url.startswith("/api/v1/download/")
        assert "eyJ" in url  # Base64 encoded token

    def test_delete_file(self, temp_storage: Path):
        """Test deleting a file."""
        storage = LocalStorage(root=temp_storage)
        
        # Create a file
        key = "test/delete.txt"
        (temp_storage / key).parent.mkdir(parents=True, exist_ok=True)
        (temp_storage / key).write_text("to be deleted")
        
        # Delete it
        storage.delete(key)
        
        assert not (temp_storage / key).exists()

    def test_get_local_path(self, temp_storage: Path):
        """Test getting local file path."""
        storage = LocalStorage(root=temp_storage)
        
        key = "test/file.txt"
        path = storage.get_local_path(key)
        
        assert path == str(temp_storage / key)


@pytest.mark.unit
class TestS3Storage:
    """Test S3 storage (mocked)."""

    @pytest.fixture
    def mock_s3_client(self, monkeypatch):
        """Mock boto3 S3 client."""
        mock_client = Mock()
        
        def mock_client_factory(*args, **kwargs):
            return mock_client
        
        monkeypatch.setattr("boto3.client", mock_client_factory)
        return mock_client

    def test_save_to_s3(self, mock_s3_client, tmp_path):
        """Test saving to S3."""
        storage = S3Storage(bucket="test-bucket")
        
        data = io.BytesIO(b"test content")
        key = "test/file.txt"
        
        result = storage.save(data, key)
        
        assert result == "s3://test-bucket/test/file.txt"
        assert mock_s3_client.upload_fileobj.called

    def test_get_presigned_url(self, mock_s3_client, tmp_path):
        """Test generating presigned URL."""
        mock_s3_client.generate_presigned_url.return_value = "https://s3.amazonaws.com/..."
        
        storage = S3Storage(bucket="test-bucket")
        url = storage.get_url("test/file.txt")
        
        assert url.startswith("https://")
        assert mock_s3_client.generate_presigned_url.called

    def test_delete_s3(self, mock_s3_client, tmp_path):
        """Test deleting from S3."""
        storage = S3Storage(bucket="test-bucket")
        storage.delete("test/file.txt")
        mock_s3_client.delete_object.assert_called_once_with(
            Bucket="test-bucket", Key="test/file.txt"
        )

    def test_get_local_path_downloads_from_s3(self, mock_s3_client, tmp_path):
        """S3 get_local_path downloads file if not cached."""
        import os
        storage = S3Storage(bucket="test-bucket")
        # Patch tmp dir to use tmp_path
        storage._tmp_dir = tmp_path

        key = "folder/file.mp4"
        local_path = storage.get_local_path(key)
        assert mock_s3_client.download_file.called


class TestLocalStorageDeleteNotExist:
    def test_delete_nonexistent_file(self, tmp_path):
        """Deleting a file that doesn't exist should not raise."""
        from app.services.storage import LocalStorage
        storage = LocalStorage(root=tmp_path)
        storage.delete("nonexistent/file.txt")  # Should not raise


class TestGetStorageFactory:
    def test_returns_local_storage_by_default(self):
        """get_storage returns LocalStorage by default."""
        from app.services.storage import get_storage, LocalStorage
        storage = get_storage()
        assert isinstance(storage, LocalStorage)

    def test_make_asset_key_sanitizes_filename(self):
        """make_asset_key sanitizes dangerous filenames."""
        from app.services.storage import make_asset_key
        key = make_asset_key("proj-123", "raw_video", "../../../etc/passwd")
        assert ".." not in key
        assert "etc" not in key or "passwd" in key  # basename used
        assert key.startswith("projects/proj-123/raw_video/")
