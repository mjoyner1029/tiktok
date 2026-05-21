"""Unit tests for FastAPI endpoints - Projects."""

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from app.models.db import Project, ProjectStatus


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

    def test_delete_project(self, client: TestClient, test_project: Project):
        """Test deleting a project."""
        response = client.delete(f"/api/v1/projects/{test_project.id}")
        
        assert response.status_code == status.HTTP_204_NO_CONTENT
        
        # Verify it's gone
        response = client.get(f"/api/v1/projects/{test_project.id}")
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.unit
class TestProjectPipeline:
    """Test project pipeline operations."""

    def test_analyze_requires_reference(self, client: TestClient, test_project: Project):
        """Test that analysis requires reference assets."""
        response = client.post(f"/api/v1/projects/{test_project.id}/analyze")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_render_requires_edit_spec(self, client: TestClient, test_project: Project):
        """Test that rendering requires an edit spec."""
        response = client.post(f"/api/v1/projects/{test_project.id}/render")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
