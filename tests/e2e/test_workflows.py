"""End-to-end tests for complete workflows."""

import pytest
from fastapi.testclient import TestClient

from app.models.db import Project


@pytest.mark.e2e
@pytest.mark.slow
class TestCompleteWorkflow:
    """Test complete user workflows from start to finish."""

    def test_project_creation_workflow(self, client: TestClient):
        """Test creating a project through the API."""
        # Step 1: Create project
        response = client.post(
            "/api/v1/projects/",
            json={
                "title": "E2E Test Project",
                "goal": "Test the complete workflow",
            },
        )
        
        assert response.status_code == 201
        project = response.json()
        project_id = project["id"]
        
        # Step 2: Verify project exists
        response = client.get(f"/api/v1/projects/{project_id}")
        assert response.status_code == 200
        
        # Step 3: List assets (should be empty)
        response = client.get(f"/api/v1/assets/{project_id}")
        assert response.status_code == 200
        assert response.json() == []
        
        # Step 4: Delete project
        response = client.delete(f"/api/v1/projects/{project_id}")
        assert response.status_code == 204
