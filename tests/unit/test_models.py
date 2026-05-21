"""Unit tests for database models."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    User,
    Workspace,
    Project,
    Asset,
    AssetType,
    StyleProfile,
    EditSpec,
    EditSpecSource,
    Render,
    RenderStatus,
)


@pytest.mark.unit
class TestUserModel:
    """Test User model."""

    @pytest.mark.asyncio
    async def test_create_user(self, db_session: AsyncSession):
        """Test creating a user."""
        user = User(
            email="newuser@example.com",
            name="New User",
            plan="starter",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        
        assert user.id is not None
        assert user.email == "newuser@example.com"
        assert user.plan == "starter"
        assert user.created_at is not None

    @pytest.mark.asyncio
    async def test_user_workspace_relationship(self, db_session: AsyncSession, test_user: User):
        """Test user to workspace relationship."""
        workspace = Workspace(
            owner_id=test_user.id,
            name="Test Workspace",
        )
        db_session.add(workspace)
        await db_session.commit()
        
        # Refresh and load workspaces relationship
        await db_session.refresh(test_user, ["workspaces"])
        assert len(test_user.workspaces) == 1
        assert test_user.workspaces[0].name == "Test Workspace"


@pytest.mark.unit
class TestProjectModel:
    """Test Project model."""

    @pytest.mark.asyncio
    async def test_create_project(self, db_session: AsyncSession, test_workspace: Workspace):
        """Test creating a project."""
        project = Project(
            workspace_id=test_workspace.id,
            title="New Project",
            goal="Test goal",
        )
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)
        
        assert project.id is not None
        assert project.title == "New Project"
        assert project.status.value == "draft"

    @pytest.mark.asyncio
    async def test_project_cascade_delete(
        self,
        db_session: AsyncSession,
        test_project: Project,
        test_asset: Asset,
    ):
        """Test that deleting a project cascades to assets."""
        project_id = test_project.id
        asset_id = test_asset.id
        
        await db_session.delete(test_project)
        await db_session.commit()
        
        # Asset should be deleted too
        asset = await db_session.get(Asset, asset_id)
        assert asset is None


@pytest.mark.unit
class TestAssetModel:
    """Test Asset model."""

    @pytest.mark.asyncio
    async def test_create_asset(self, db_session: AsyncSession, test_project: Project):
        """Test creating an asset."""
        asset = Asset(
            project_id=test_project.id,
            type=AssetType.raw_video,
            filename="test.mp4",
            storage_url="/path/to/test.mp4",
            duration_sec=30.0,
            width=1080,
            height=1920,
        )
        db_session.add(asset)
        await db_session.commit()
        await db_session.refresh(asset)
        
        assert asset.id is not None
        assert asset.type == AssetType.raw_video
        assert asset.transcript_status.value == "pending"
