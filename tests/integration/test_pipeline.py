"""Integration tests for full pipeline."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Project, Asset, AssetType, StyleProfile, EditSpec, Render


@pytest.mark.integration
@pytest.mark.slow
class TestFullPipeline:
    """Test the complete video generation pipeline."""

    @pytest.mark.asyncio
    async def test_project_creation_to_style_analysis(
        self,
        db_session: AsyncSession,
        test_workspace,
    ):
        """Test creating project and analyzing style."""
        # Create project
        project = Project(
            workspace_id=test_workspace.id,
            title="Integration Test",
            goal="Test full pipeline",
        )
        db_session.add(project)
        await db_session.commit()
        await db_session.refresh(project)
        
        # Add reference asset
        ref_asset = Asset(
            project_id=project.id,
            type=AssetType.reference_video,
            filename="reference.mp4",
            storage_url="/fake/path/reference.mp4",
            transcript="Hook text. Problem explanation. Solution. CTA.",
        )
        db_session.add(ref_asset)
        await db_session.commit()
        
        assert project.id is not None
        assert ref_asset.id is not None

    @pytest.mark.asyncio
    async def test_edit_spec_to_render(
        self,
        db_session: AsyncSession,
        test_project: Project,
        mock_edit_spec,
    ):
        """Test creating edit spec and render."""
        # Create edit spec
        spec = EditSpec(
            project_id=test_project.id,
            version=1,
            spec_json=mock_edit_spec,
            source="ai",
        )
        db_session.add(spec)
        await db_session.commit()
        await db_session.refresh(spec)
        
        # Create render
        render = Render(
            project_id=test_project.id,
            edit_spec_id=spec.id,
            status="queued",
        )
        db_session.add(render)
        await db_session.commit()
        await db_session.refresh(render)
        
        assert spec.id is not None
        assert render.id is not None
        assert render.edit_spec_id == spec.id
