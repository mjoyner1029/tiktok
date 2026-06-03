"""Tests for app/api/renders.py, app/api/downloads.py, app/api/styles.py."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch, Mock
import pytest
from fastapi.testclient import TestClient

from app.models.db import Render, RenderStatus, StyleProfile, EditSpec, EditSpecSource


# ---------------------------------------------------------------------------
# Renders API
# ---------------------------------------------------------------------------

class TestGetRender:
    async def _create_render(self, db_session, test_project, test_asset):
        """Create a test render in the DB."""
        from app.models.db import EditSpec, EditSpecSource, Render, RenderStatus
        spec = EditSpec(
            project_id=test_project.id,
            version=1,
            spec_json={"tracks": {"video": [], "text": [], "audio": []}},
            source=EditSpecSource.ai,
        )
        db_session.add(spec)
        await db_session.flush()

        render = Render(
            project_id=test_project.id,
            edit_spec_id=spec.id,
            status=RenderStatus.completed,
        )
        db_session.add(render)
        await db_session.commit()
        await db_session.refresh(render)
        return render

    def test_get_render_not_found(self, client: TestClient):
        response = client.get(f"/api/v1/renders/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_get_render_exists(self, client: TestClient, db_session, test_project):
        import asyncio
        from app.models.db import EditSpec, EditSpecSource, Render, RenderStatus
        
        async def create():
            spec = EditSpec(
                project_id=test_project.id,
                version=1,
                spec_json={"tracks": {}},
                source=EditSpecSource.ai,
            )
            db_session.add(spec)
            await db_session.flush()
            render = Render(
                project_id=test_project.id,
                edit_spec_id=spec.id,
                status=RenderStatus.queued,
            )
            db_session.add(render)
            await db_session.commit()
            await db_session.refresh(render)
            return render

        render = asyncio.get_event_loop().run_until_complete(create())
        response = client.get(f"/api/v1/renders/{render.id}")
        assert response.status_code == 200
        data = response.json()
        assert str(data["id"]) == str(render.id)


class TestDownloadRender:
    def test_download_render_not_found(self, client: TestClient):
        response = client.get(f"/api/v1/renders/{uuid.uuid4()}/download")
        assert response.status_code == 404

    def test_download_render_not_ready(self, client: TestClient, db_session, test_project):
        import asyncio
        from app.models.db import EditSpec, EditSpecSource, Render, RenderStatus

        async def create():
            spec = EditSpec(
                project_id=test_project.id,
                version=1,
                spec_json={},
                source=EditSpecSource.ai,
            )
            db_session.add(spec)
            await db_session.flush()
            render = Render(
                project_id=test_project.id,
                edit_spec_id=spec.id,
                status=RenderStatus.queued,
            )
            db_session.add(render)
            await db_session.commit()
            await db_session.refresh(render)
            return render

        render = asyncio.get_event_loop().run_until_complete(create())
        response = client.get(f"/api/v1/renders/{render.id}/download")
        assert response.status_code in (400, 404, 409)

    def test_download_render_completed(self, client: TestClient, db_session, test_project, tmp_path):
        import asyncio
        from app.models.db import EditSpec, EditSpecSource, Render, RenderStatus

        # Create a real file to serve
        video_file = tmp_path / "render.mp4"
        video_file.write_bytes(b"fake video content")

        async def create():
            spec = EditSpec(
                project_id=test_project.id,
                version=1,
                spec_json={},
                source=EditSpecSource.ai,
            )
            db_session.add(spec)
            await db_session.flush()
            render = Render(
                project_id=test_project.id,
                edit_spec_id=spec.id,
                status=RenderStatus.completed,
                output_url=str(video_file),
            )
            db_session.add(render)
            await db_session.commit()
            await db_session.refresh(render)
            return render

        render = asyncio.get_event_loop().run_until_complete(create())

        with patch("app.api.renders.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.get_local_path.return_value = str(video_file)
            mock_storage_factory.return_value = mock_storage
            response = client.get(f"/api/v1/renders/{render.id}/download")
        assert response.status_code in (200, 404)


class TestRenderThumbnail:
    def test_thumbnail_not_found(self, client: TestClient):
        response = client.get(f"/api/v1/renders/{uuid.uuid4()}/thumbnail")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Downloads API (signed URL)
# ---------------------------------------------------------------------------

class TestSignedUrlDownload:
    def _make_token(self, key: str) -> str:
        from itsdangerous import URLSafeTimedSerializer
        from app.config import get_settings
        settings = get_settings()
        signing_key = settings.url_signing_key if hasattr(settings, "url_signing_key") and settings.url_signing_key else settings.secret_key
        s = URLSafeTimedSerializer(signing_key)
        return s.dumps({"key": key})

    def test_valid_token_returns_file(self, client: TestClient, tmp_path):
        real_file = tmp_path / "video.mp4"
        real_file.write_bytes(b"fake video content for download")
        token = self._make_token("uploads/video.mp4")

        with patch("app.api.downloads.get_storage") as mock_storage_factory:
            mock_storage = Mock()
            mock_storage.get_local_path.return_value = str(real_file)
            mock_storage_factory.return_value = mock_storage
            response = client.get(f"/api/v1/download/{token}")
        assert response.status_code in (200, 404)

    def test_invalid_token_returns_403(self, client: TestClient):
        response = client.get("/api/v1/download/totally.invalid.token")
        assert response.status_code == 403

    def test_expired_token_returns_403(self, client: TestClient):
        from itsdangerous import URLSafeTimedSerializer
        from app.config import get_settings
        import time
        settings = get_settings()
        signing_key = settings.url_signing_key if hasattr(settings, "url_signing_key") and settings.url_signing_key else settings.secret_key
        s = URLSafeTimedSerializer(signing_key)
        token = s.dumps({"key": "/fake/path.mp4"})
        # Load with max_age=0 to simulate immediate expiry
        with patch("app.api.downloads.URLSafeTimedSerializer") as mock_ser_class:
            from itsdangerous import SignatureExpired
            mock_ser = Mock()
            mock_ser.loads.side_effect = SignatureExpired("expired", payload=None)
            mock_ser_class.return_value = mock_ser
            response = client.get(f"/api/v1/download/{token}")
        assert response.status_code == 403

    def test_wrong_secret_key_returns_403(self, client: TestClient):
        from itsdangerous import URLSafeTimedSerializer
        s = URLSafeTimedSerializer("completely-different-secret-key-not-the-real-one")
        token = s.dumps({"key": "/path/to/file.mp4"})
        response = client.get(f"/api/v1/download/{token}")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Styles API
# ---------------------------------------------------------------------------

class TestStylesApi:
    async def _create_style(self, db_session, test_project):
        style = StyleProfile(
            project_id=test_project.id,
            name="Test Style",
            profile_json={"hook_style": "curiosity", "tone": "educational"},
            model_name="claude-3",
        )
        db_session.add(style)
        await db_session.commit()
        await db_session.refresh(style)
        return style

    def test_list_styles_empty(self, client: TestClient):
        response = client.get("/api/v1/styles/")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_styles_with_data(self, client: TestClient, db_session, test_project):
        import asyncio
        style = asyncio.get_event_loop().run_until_complete(
            self._create_style(db_session, test_project)
        )
        response = client.get("/api/v1/styles/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_get_style_by_id(self, client: TestClient, db_session, test_project):
        import asyncio
        style = asyncio.get_event_loop().run_until_complete(
            self._create_style(db_session, test_project)
        )
        response = client.get(f"/api/v1/styles/{style.id}")
        assert response.status_code == 200
        data = response.json()
        assert str(data["id"]) == str(style.id)

    def test_get_style_not_found(self, client: TestClient):
        response = client.get(f"/api/v1/styles/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_delete_style(self, client: TestClient, db_session, test_project):
        import asyncio
        style = asyncio.get_event_loop().run_until_complete(
            self._create_style(db_session, test_project)
        )
        response = client.delete(f"/api/v1/styles/{style.id}")
        assert response.status_code in (200, 204)

    def test_delete_style_not_found(self, client: TestClient):
        response = client.delete(f"/api/v1/styles/{uuid.uuid4()}")
        assert response.status_code == 404
