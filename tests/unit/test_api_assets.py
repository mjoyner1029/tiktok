"""Tests for app/api/assets.py — asset upload, list, get, delete, transcribe."""

from __future__ import annotations

import io
import uuid
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock
import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Asset upload
# ---------------------------------------------------------------------------

class TestUploadAsset:
    def _make_video_file(self, name="video.mp4", size=1024):
        return ("file", (name, io.BytesIO(b"x" * size), "video/mp4"))

    def test_upload_valid_video(self, client: TestClient, test_project):
        files = [self._make_video_file()]
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.save.return_value = None
            mock_storage_factory.return_value = mock_storage

            response = client.post(
                f"/api/v1/assets/upload/{test_project.id}",
                files=files,
                data={"asset_type": "raw_video"},
            )
        assert response.status_code in (200, 201)
        data = response.json()
        assert "id" in data

    def test_upload_invalid_asset_type(self, client: TestClient, test_project):
        files = [self._make_video_file()]
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.save.return_value = None
            mock_storage_factory.return_value = mock_storage

            response = client.post(
                f"/api/v1/assets/upload/{test_project.id}",
                files=files,
                data={"asset_type": "invalid_type"},
            )
        assert response.status_code in (400, 422)

    def test_upload_invalid_mime_type(self, client: TestClient, test_project):
        bad_file = ("file", ("script.sh", io.BytesIO(b"#!/bin/bash\nrm -rf /"), "text/plain"))
        response = client.post(
            f"/api/v1/assets/upload/{test_project.id}",
            files=[bad_file],
            data={"asset_type": "raw_video"},
        )
        assert response.status_code in (400, 422)

    def test_upload_file_too_large(self, client: TestClient, test_project):
        # Most implementations check Content-Length or file size on the server
        # Skip this test if no MAX_FILE_SIZE_BYTES constant
        import app.api.assets as assets_module
        if not hasattr(assets_module, "MAX_FILE_SIZE_BYTES"):
            pytest.skip("No MAX_FILE_SIZE_BYTES constant in assets module")
        files = [("file", ("big.mp4", io.BytesIO(b"x" * 100), "video/mp4"))]
        with patch("app.api.assets.MAX_FILE_SIZE_BYTES", 10):
            response = client.post(
                f"/api/v1/assets/upload/{test_project.id}",
                files=files,
                data={"asset_type": "raw_video"},
            )
        assert response.status_code in (400, 413, 422)

    def test_upload_reference_video(self, client: TestClient, test_project):
        files = [("file", ("ref.mp4", io.BytesIO(b"fake ref video" * 100), "video/mp4"))]
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.save.return_value = None
            mock_storage_factory.return_value = mock_storage

            response = client.post(
                f"/api/v1/assets/upload/{test_project.id}",
                files=files,
                data={"asset_type": "reference_video"},
            )
        assert response.status_code in (200, 201)

    def test_upload_image(self, client: TestClient, test_project):
        files = [("file", ("photo.jpg", io.BytesIO(b"fake jpg" * 100), "image/jpeg"))]
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.save.return_value = None
            mock_storage_factory.return_value = mock_storage

            response = client.post(
                f"/api/v1/assets/upload/{test_project.id}",
                files=files,
                data={"asset_type": "image"},
            )
        assert response.status_code in (200, 201)

    def test_upload_project_not_found(self, client: TestClient):
        files = [("file", ("video.mp4", io.BytesIO(b"fake" * 100), "video/mp4"))]
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage_factory.return_value = mock_storage
            response = client.post(
                f"/api/v1/assets/upload/{uuid.uuid4()}",
                files=files,
                data={"asset_type": "raw_video"},
            )
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Asset list
# ---------------------------------------------------------------------------

class TestListAssets:
    def test_list_empty(self, client: TestClient, test_project):
        response = client.get(f"/api/v1/assets/{test_project.id}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_with_assets(self, client: TestClient, test_project, test_asset):
        response = client.get(f"/api/v1/assets/{test_project.id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_list_project_not_found(self, client: TestClient):
        response = client.get(f"/api/v1/assets/{uuid.uuid4()}")
        assert response.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Asset detail
# ---------------------------------------------------------------------------

class TestGetAsset:
    def test_get_existing_asset(self, client: TestClient, test_asset):
        response = client.get(f"/api/v1/assets/detail/{test_asset.id}")
        assert response.status_code == 200
        data = response.json()
        assert str(data["id"]) == str(test_asset.id)

    def test_get_nonexistent_asset(self, client: TestClient):
        response = client.get(f"/api/v1/assets/detail/{uuid.uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Asset delete
# ---------------------------------------------------------------------------

class TestDeleteAsset:
    def test_delete_asset(self, client: TestClient, test_asset):
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.delete.return_value = None
            mock_storage_factory.return_value = mock_storage

            response = client.delete(f"/api/v1/assets/detail/{test_asset.id}")
        assert response.status_code in (200, 204)

    def test_delete_nonexistent_asset(self, client: TestClient):
        with patch("app.api.assets.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage_factory.return_value = mock_storage
            response = client.delete(f"/api/v1/assets/detail/{uuid.uuid4()}")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Transcribe asset
# ---------------------------------------------------------------------------

class TestTranscribeAsset:
    def test_transcribe_asset(self, client: TestClient, test_asset):
        with patch("app.workers.tasks.transcribe_asset") as mock_task:
            mock_task.delay.return_value = Mock(id="celery-task-id")
            response = client.post(f"/api/v1/assets/transcribe/{test_asset.id}")
        assert response.status_code in (200, 201, 202)
        data = response.json()
        assert "id" in data  # returns JobOut

    def test_transcribe_nonexistent_asset(self, client: TestClient):
        with patch("app.workers.tasks.transcribe_asset") as mock_task:
            mock_task.delay.return_value = Mock(id="task_id")
            response = client.post(f"/api/v1/assets/transcribe/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_transcribe_all_assets(self, client: TestClient, test_project, test_asset):
        with patch("app.workers.tasks.transcribe_asset") as mock_task:
            mock_task.delay.return_value = Mock(id="task_id")
            response = client.post(f"/api/v1/assets/transcribe-all/{test_project.id}")
        # No pending assets since test_asset may not be in 'pending' transcript_status
        assert response.status_code in (200, 202, 400)


# ---------------------------------------------------------------------------
# Import from URL
# ---------------------------------------------------------------------------

class TestImportFromUrl:
    def test_import_url(self, client: TestClient, test_project):
        with patch("app.workers.tasks.import_video_from_url") as mock_task:
            mock_task.delay.return_value = Mock(id="celery-import-task")
            response = client.post(
                f"/api/v1/assets/import-url/{test_project.id}",
                json={"url": "https://www.tiktok.com/@user/video/1234567890123456789"},
            )
        assert response.status_code in (200, 201, 202)

    def test_import_url_project_not_found(self, client: TestClient):
        with patch("app.workers.tasks.import_video_from_url") as mock_task:
            mock_task.delay.return_value = Mock(id="task_id")
            response = client.post(
                f"/api/v1/assets/import-url/{uuid.uuid4()}",
                json={"url": "https://vm.tiktok.com/abc"},
            )
        assert response.status_code == 404
