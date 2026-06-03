"""Unit tests for FastAPI endpoints - Projects."""

import uuid
from unittest.mock import patch, Mock

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    Asset, AssetType, EditSpec, EditSpecSource, Job, JobStatus, JobType,
    Project, ProjectStatus, Render, RenderStatus, StyleProfile,
)


@pytest.mark.unit
class TestProjectsEndpoints:
    """Test project CRUD operations."""

    def test_create_project(self, client: TestClient):
        """Test creating a new project."""
        response = client.post(
            "/api/v1/projects/",
            json={
                "title": "New Test Project",
                "goal": "Make viral TikTok",
                "target_platform": "tiktok",
            },
        )
        
        assert response.status_code == status.HTTP_201_CREATED
        data = response.json()
        assert data["title"] == "New Test Project"
        assert data["goal"] == "Make viral TikTok"
        assert data["status"] == "draft"
        assert "id" in data

    def test_list_projects(self, client: TestClient, test_project: Project):
        """Test listing projects."""
        response = client.get("/api/v1/projects/")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(p["id"] == str(test_project.id) for p in data)

    def test_get_project(self, client: TestClient, test_project: Project):
        """Test getting a specific project."""
        response = client.get(f"/api/v1/projects/{test_project.id}")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["id"] == str(test_project.id)
        assert data["title"] == test_project.title

    def test_get_nonexistent_project(self, client: TestClient):
        """Test getting a project that doesn't exist."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        response = client.get(f"/api/v1/projects/{fake_id}")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_project(self, client: TestClient, test_project: Project):
        """Test updating a project."""
        response = client.patch(
            f"/api/v1/projects/{test_project.id}",
            json={"title": "Updated Title"},
        )
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["title"] == "Updated Title"

    def test_update_project_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000001"
        response = client.patch(f"/api/v1/projects/{fake_id}", json={"title": "X"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_update_project_goal(self, client: TestClient, test_project: Project):
        response = client.patch(
            f"/api/v1/projects/{test_project.id}",
            json={"goal": "New goal"},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["goal"] == "New goal"

    def test_delete_project(self, client: TestClient, test_project: Project):
        """Test deleting a project."""
        response = client.delete(f"/api/v1/projects/{test_project.id}")
        
        assert response.status_code == status.HTTP_204_NO_CONTENT
        
        # Verify it's gone
        response = client.get(f"/api/v1/projects/{test_project.id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_project_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000002"
        response = client.delete(f"/api/v1/projects/{fake_id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
class TestProjectPipeline:
    """Test project pipeline operations."""

    def test_analyze_requires_reference(self, client: TestClient, test_project: Project):
        """Test that analysis requires reference assets."""
        response = client.post(f"/api/v1/projects/{test_project.id}/analyze")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_analyze_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000003"
        response = client.post(f"/api/v1/projects/{fake_id}/analyze")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_analyze_with_reference(self, client: TestClient, db_session: AsyncSession,
                                    test_project: Project):
        """Test analysis with a reference asset dispatches a job."""
        import asyncio

        async def _add_ref():
            ref = Asset(
                project_id=test_project.id,
                type=AssetType.reference_video,
                filename="ref.mp4",
                storage_url="/path/ref.mp4",
            )
            db_session.add(ref)
            await db_session.commit()

        asyncio.get_event_loop().run_until_complete(_add_ref())

        with patch("app.workers.tasks.analyze_and_generate.delay") as mock_delay:
            mock_delay.return_value = Mock(id="celery-task-id-123")
            response = client.post(f"/api/v1/projects/{test_project.id}/analyze")
        assert response.status_code in (200, 202)
        mock_delay.assert_called_once()

    def test_render_requires_edit_spec(self, client: TestClient, test_project: Project):
        """Test that rendering requires an edit spec."""
        response = client.post(f"/api/v1/projects/{test_project.id}/render")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_render_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000004"
        response = client.post(f"/api/v1/projects/{fake_id}/render")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_render_with_edit_spec(self, client: TestClient, db_session: AsyncSession,
                                   test_project: Project):
        """Test that render dispatches a job when edit spec exists."""
        import asyncio

        async def _add_spec():
            spec = EditSpec(
                project_id=test_project.id,
                version=1,
                spec_json={"tracks": {"video": [], "text": [], "audio": []}},
                source=EditSpecSource.ai,
            )
            db_session.add(spec)
            await db_session.commit()

        asyncio.get_event_loop().run_until_complete(_add_spec())

        with patch("app.workers.tasks.render_project.delay") as mock_delay:
            mock_delay.return_value = Mock(id="render-task-id")
            response = client.post(f"/api/v1/projects/{test_project.id}/render")
        assert response.status_code in (200, 202)
        mock_delay.assert_called_once()

    def test_full_pipeline(self, client: TestClient, test_project: Project):
        with patch("app.workers.tasks.full_pipeline.delay") as mock_delay:
            mock_delay.return_value = Mock(id="pipeline-task-id")
            response = client.post(f"/api/v1/projects/{test_project.id}/pipeline")
        assert response.status_code in (200, 202)
        mock_delay.assert_called_once()

    def test_full_pipeline_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000005"
        response = client.post(f"/api/v1/projects/{fake_id}/pipeline")
        assert response.status_code == 404


@pytest.mark.unit
class TestProjectSubResources:
    """Test sub-resource endpoints for projects."""

    def test_list_specs_empty(self, client: TestClient, test_project: Project):
        response = client.get(f"/api/v1/projects/{test_project.id}/specs")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_renders_empty(self, client: TestClient, test_project: Project):
        response = client.get(f"/api/v1/projects/{test_project.id}/renders")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_styles_empty(self, client: TestClient, test_project: Project):
        response = client.get(f"/api/v1/projects/{test_project.id}/styles")
        assert response.status_code == 200
        assert response.json() == []

    def test_list_jobs_empty(self, client: TestClient, test_project: Project):
        response = client.get(f"/api/v1/projects/{test_project.id}/jobs")
        assert response.status_code == 200
        assert response.json() == []

    def test_revise_no_spec(self, client: TestClient, test_project: Project):
        """Revise returns 400 if no edit spec exists."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/revise",
            json={"feedback": "Make it funnier"},
        )
        assert response.status_code == 400

    def test_revise_not_found(self, client: TestClient):
        fake_id = "00000000-0000-0000-0000-000000000006"
        response = client.post(
            f"/api/v1/projects/{fake_id}/revise",
            json={"feedback": "Make it funnier"},
        )
        assert response.status_code == 404

    def test_revise_with_spec(self, client: TestClient, db_session: AsyncSession,
                               test_project: Project):
        """Revise an edit spec with AI."""
        import asyncio

        async def _add_spec():
            spec = EditSpec(
                project_id=test_project.id,
                version=1,
                spec_json={"tracks": {"video": [], "text": [], "audio": []}},
                source=EditSpecSource.ai,
            )
            db_session.add(spec)
            await db_session.commit()

        asyncio.get_event_loop().run_until_complete(_add_spec())

        revised_spec = {"tracks": {"video": [{"clip": "x"}], "text": [], "audio": []}}
        with patch("app.services.ai_orchestrator.AIOrchestrator") as MockAI:
            instance = MockAI.return_value
            instance.revise_edit_spec.return_value = revised_spec
            response = client.post(
                f"/api/v1/projects/{test_project.id}/revise",
                json={"feedback": "Make it funnier"},
            )
        assert response.status_code in (200, 201)
