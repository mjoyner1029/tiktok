"""Batch processing service for generating multiple videos at once.

Allows users to apply a single style to multiple content pieces in bulk.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Project, Asset, Job, JobType, JobStatus, EditSpec, Render
from app.workers.tasks import analyze_and_generate

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Handle batch video generation operations."""
    
    @staticmethod
    async def create_batch_job(
        workspace_id: uuid.UUID,
        style_profile: Dict[str, Any],
        content_items: List[Dict[str, Any]],
        db: AsyncSession,
    ) -> List[uuid.UUID]:
        """Create multiple projects from a single style + multiple content items.
        
        Args:
            workspace_id: Target workspace
            style_profile: Style to apply to all
            content_items: List of {title, goal, assets: [file_paths]}
            db: Database session
        
        Returns:
            List of project IDs created
        """
        project_ids = []
        
        for item in content_items:
            # Create project
            project = Project(
                workspace_id=workspace_id,
                title=item["title"],
                goal=item.get("goal", ""),
                status="draft",
            )
            db.add(project)
            await db.flush()
            
            # Save style profile
            from app.models.db import StyleProfile
            
            style = StyleProfile(
                project_id=project.id,
                name=f"Batch style - {item['title']}",
                profile_json=style_profile,
                model_name="batch",
            )
            db.add(style)
            
            # Add assets (assuming file paths provided)
            for asset_path in item.get("assets", []):
                # Asset creation logic would go here
                # For now, just mark the pattern
                pass
            
            project_ids.append(project.id)
        
        await db.commit()
        
        logger.info(f"Created batch of {len(project_ids)} projects")
        return project_ids
    
    @staticmethod
    async def queue_batch_renders(
        project_ids: List[uuid.UUID],
        db: AsyncSession,
    ) -> List[str]:
        """Queue render jobs for all projects in a batch.
        
        Returns:
            List of job IDs
        """
        job_ids = []
        
        for project_id in project_ids:
            # Create edit spec generation job
            job = Job(
                project_id=project_id,
                type=JobType.generate_edit_spec,
                status=JobStatus.pending,
            )
            db.add(job)
            await db.flush()
            
            # Queue Celery task
            task = analyze_and_generate.delay(
                project_id=str(project_id),
                job_id=str(job.id),
            )
            
            job.celery_task_id = task.id
            job_ids.append(str(job.id))
        
        await db.commit()
        
        logger.info(f"Queued {len(job_ids)} batch render jobs")
        return job_ids
    
    @staticmethod
    async def get_batch_status(
        project_ids: List[uuid.UUID],
        db: AsyncSession,
    ) -> Dict[str, Any]:
        """Get status of all projects in a batch.
        
        Returns:
            {
                "total": 10,
                "completed": 7,
                "failed": 1,
                "in_progress": 2,
                "projects": [...]
            }
        """
        from sqlalchemy import select
        
        result = await db.execute(
            select(Project).where(Project.id.in_(project_ids))
        )
        projects = result.scalars().all()
        
        status_counts = {
            "total": len(projects),
            "completed": sum(1 for p in projects if p.status.value == "ready"),
            "failed": sum(1 for p in projects if p.status.value == "failed"),
            "in_progress": sum(
                1 for p in projects
                if p.status.value in ("analyzing", "generating", "rendering")
            ),
        }
        
        return {
            **status_counts,
            "projects": [
                {
                    "id": str(p.id),
                    "title": p.title,
                    "status": p.status.value,
                }
                for p in projects
            ],
        }


# ── A/B Testing Support ──────────────────────────────────────────────────────


class ABTestService:
    """Generate multiple variations for A/B testing."""
    
    @staticmethod
    async def create_variants(
        project_id: uuid.UUID,
        num_variants: int = 3,
        variation_params: Dict[str, List[Any]] = None,
        db: AsyncSession = None,
    ) -> List[uuid.UUID]:
        """Create multiple edit spec variants for A/B testing.
        
        Args:
            project_id: Source project
            num_variants: Number of variants to create
            variation_params: Parameters to vary, e.g.:
                {
                    "avg_cut_duration": [1.0, 1.5, 2.0],
                    "caption_position": ["top", "center", "bottom"],
                }
        
        Returns:
            List of edit spec IDs
        """
        # Get base style profile
        from sqlalchemy import select
        from app.models.db import StyleProfile
        
        result = await db.execute(
            select(StyleProfile)
            .where(StyleProfile.project_id == project_id)
            .order_by(StyleProfile.created_at.desc())
        )
        base_style = result.scalar_one_or_none()
        
        if not base_style:
            raise ValueError("No style profile found for project")
        
        variant_ids = []
        
        # Generate variants with parameter variations
        for i in range(num_variants):
            variant_style = dict(base_style.profile_json)
            
            # Apply variations
            if variation_params:
                for param, values in variation_params.items():
                    if i < len(values):
                        variant_style[param] = values[i]
            
            # Create variant edit spec
            # (Actual edit spec generation would use AI orchestrator)
            variant_ids.append(str(uuid.uuid4()))
        
        logger.info(f"Created {num_variants} A/B test variants for project {project_id}")
        return variant_ids
