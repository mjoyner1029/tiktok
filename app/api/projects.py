"""Project CRUD endpoints."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import (
    Asset,
    AssetType,
    EditSpec,
    Job,
    JobStatus,
    JobType,
    Project,
    ProjectStatus,
    Render,
    RenderStatus,
    StyleProfile,
    Workspace,
)
from app.models.schemas import (
    EditSpecOut,
    JobOut,
    ProjectCreate,
    ProjectOut,
    ProjectUpdate,
    RenderOut,
    RevisionRequest,
    StyleProfileOut,
)

router = APIRouter(prefix="/projects", tags=["projects"])


# ── helper to get a default workspace (MVP: single user) ─────────────────

async def _get_or_create_workspace(db: AsyncSession) -> Workspace:
    """For MVP, auto-create a default workspace if none exists."""
    result = await db.execute(select(Workspace).limit(1))
    ws = result.scalar_one_or_none()
    if ws:
        return ws
    from app.models.db import User
    # Create default user + workspace
    user = User(email="dev@localhost", name="Developer", plan="starter")
    db.add(user)
    await db.flush()
    ws = Workspace(owner_id=user.id, name="Default")
    db.add(ws)
    await db.flush()
    return ws


# ── CRUD ─────────────────────────────────────────────────────────────────

@router.post("/", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    ws = await _get_or_create_workspace(db)
    project = Project(
        workspace_id=ws.id,
        title=body.title,
        goal=body.goal,
        target_platform=body.target_platform,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


@router.get("/", response_model=List[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Project).order_by(Project.created_at.desc()).limit(50)
    )
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if body.title is not None:
        project.title = body.title
    if body.goal is not None:
        project.goal = body.goal
    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    await db.delete(project)
    await db.commit()


# ── Pipeline triggers ────────────────────────────────────────────────────

@router.post("/{project_id}/analyze", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def start_analysis(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger style analysis + edit spec generation."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    
    # Check for references
    from sqlalchemy import select
    ref_result = await db.execute(
        select(Asset).where(
            Asset.project_id == project.id,
            Asset.type == AssetType.reference_video
        ).limit(1)
    )
    if not ref_result.scalar_one_or_none():
        raise HTTPException(400, "Project must have at least one reference asset")

    job = Job(
        project_id=project.id,
        type=JobType.analyze_style,
        status=JobStatus.pending,
        payload={"project_id": str(project.id)},
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    # Dispatch Celery task
    from app.workers.tasks import analyze_and_generate
    task = analyze_and_generate.delay(str(project.id), str(job.id))
    job.celery_task_id = task.id
    await db.flush()

    return job


@router.post("/{project_id}/render", response_model=RenderOut, status_code=status.HTTP_202_ACCEPTED)
async def start_render(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger rendering from the latest edit spec."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Find latest edit spec
    result = await db.execute(
        select(EditSpec)
        .where(EditSpec.project_id == project.id)
        .order_by(EditSpec.version.desc())
        .limit(1)
    )
    edit_spec = result.scalar_one_or_none()
    if not edit_spec:
        raise HTTPException(400, "No edit spec available. Run /analyze first.")

    render = Render(
        project_id=project.id,
        edit_spec_id=edit_spec.id,
        status=RenderStatus.queued,
    )
    db.add(render)
    await db.flush()

    job = Job(
        project_id=project.id,
        type=JobType.render,
        status=JobStatus.pending,
        payload={"render_id": str(render.id)},
    )
    db.add(job)
    await db.flush()
    await db.refresh(render)

    from app.workers.tasks import render_project
    task = render_project.delay(str(render.id), str(job.id))
    job.celery_task_id = task.id
    await db.flush()

    return render


@router.post("/{project_id}/pipeline", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def start_full_pipeline(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Run the full pipeline: transcribe → analyse → render."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    job = Job(
        project_id=project.id,
        type=JobType.render,
        status=JobStatus.pending,
        payload={"project_id": str(project.id), "mode": "full_pipeline"},
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    from app.workers.tasks import full_pipeline
    task = full_pipeline.delay(str(project.id), str(job.id))
    job.celery_task_id = task.id
    await db.flush()

    return job


# ── Revisions ────────────────────────────────────────────────────────────

@router.post("/{project_id}/revise", response_model=EditSpecOut, status_code=status.HTTP_201_CREATED)
async def revise_edit_spec(
    project_id: uuid.UUID,
    body: RevisionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Revise the latest edit spec with user feedback."""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    # Get latest spec + style
    spec_result = await db.execute(
        select(EditSpec).where(EditSpec.project_id == project.id)
        .order_by(EditSpec.version.desc()).limit(1)
    )
    current_spec = spec_result.scalar_one_or_none()
    if not current_spec:
        raise HTTPException(400, "No edit spec to revise")

    style_result = await db.execute(
        select(StyleProfile).where(StyleProfile.project_id == project.id)
        .order_by(StyleProfile.created_at.desc()).limit(1)
    )
    style = style_result.scalar_one_or_none()

    # Run revision in a thread to avoid blocking the event loop
    import asyncio
    from app.services.ai_orchestrator import AIOrchestrator
    ai = AIOrchestrator()
    revised = await asyncio.to_thread(
        ai.revise_edit_spec,
        current_spec=current_spec.spec_json,
        style_json=style.profile_json if style else {},
        feedback=body.feedback,
    )

    from app.models.db import EditSpecSource
    new_spec = EditSpec(
        project_id=project.id,
        version=current_spec.version + 1,
        spec_json=revised,
        source=EditSpecSource.revised,
        revision_note=body.feedback,
    )
    db.add(new_spec)
    await db.flush()
    await db.refresh(new_spec)
    return new_spec


# ── Sub-resources ────────────────────────────────────────────────────────

@router.get("/{project_id}/specs", response_model=List[EditSpecOut])
async def list_edit_specs(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(EditSpec).where(EditSpec.project_id == project_id)
        .order_by(EditSpec.version.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/renders", response_model=List[RenderOut])
async def list_renders(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Render).where(Render.project_id == project_id)
        .order_by(Render.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/styles", response_model=List[StyleProfileOut])
async def list_styles(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(StyleProfile).where(StyleProfile.project_id == project_id)
        .order_by(StyleProfile.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{project_id}/jobs", response_model=List[JobOut])
async def list_jobs(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Job).where(Job.project_id == project_id)
        .order_by(Job.created_at.desc())
    )
    return result.scalars().all()
