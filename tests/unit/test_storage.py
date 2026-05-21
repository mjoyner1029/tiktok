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
