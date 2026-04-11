"""Asset upload & management endpoints."""

from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status, Form
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.db import Asset, AssetType, Job, JobStatus, JobType, Project
from app.models.schemas import AssetOut, JobOut
from app.services.storage import get_storage, make_asset_key


class ImportUrlRequest(BaseModel):
    url: str

router = APIRouter(prefix="/assets", tags=["assets"])


@router.post(
    "/upload/{project_id}",
    response_model=AssetOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_asset(
    project_id: uuid.UUID,
    file: UploadFile = File(...),
    asset_type: str = Form("raw_video"),
    db: AsyncSession = Depends(get_db),
):
    """Upload a video/audio/image asset to a project.

    asset_type: reference_video | raw_video | audio | image
    """
    from app.config import get_settings
    _settings = get_settings()

    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    try:
        atype = AssetType(asset_type)
    except ValueError:
        raise HTTPException(400, f"Invalid asset_type: {asset_type}")

    # ── File size guard ──────────────────────────────────────────────
    if file.size and file.size > _settings.max_upload_size_bytes:
        raise HTTPException(
            413,
            f"File too large. Max {_settings.max_upload_size_mb}MB.",
        )

    # ── MIME type guard ──────────────────────────────────────────────
    ALLOWED_MIME_PREFIXES = ("video/", "audio/", "image/")
    if file.content_type and not file.content_type.startswith(ALLOWED_MIME_PREFIXES):
        raise HTTPException(400, f"Unsupported file type: {file.content_type}")

    # ── Sanitise filename ────────────────────────────────────────────
    import re as _re
    safe_filename = _re.sub(r'[^\w.\-]', '_', file.filename or "upload")

    storage = get_storage()
    key = make_asset_key(str(project_id), asset_type, safe_filename)
    storage_url = storage.save(file.file, key, file.content_type or "")

    asset = Asset(
        project_id=project.id,
        type=atype,
        filename=safe_filename,
        storage_url=key,  # store the key, not the full URL
        mime_type=file.content_type,
        file_size_bytes=file.size,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)
    return asset


@router.get("/{project_id}", response_model=List[AssetOut])
async def list_assets(project_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Asset).where(Asset.project_id == project_id)
        .order_by(Asset.created_at.desc())
    )
    return result.scalars().all()


@router.get("/detail/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    return asset


@router.delete("/detail/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")
    # Delete from storage
    storage = get_storage()
    try:
        storage.delete(asset.storage_url)
    except Exception:
        pass  # non-fatal
    await db.delete(asset)


@router.post(
    "/transcribe/{asset_id}",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def transcribe_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Trigger transcription for a specific asset."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(404, "Asset not found")

    job = Job(
        project_id=asset.project_id,
        type=JobType.transcribe,
        status=JobStatus.pending,
        payload={"asset_id": str(asset.id)},
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    from app.workers.tasks import transcribe_asset as transcribe_task
    task = transcribe_task.delay(str(asset.id), str(job.id))
    job.celery_task_id = task.id
    await db.flush()

    return job


@router.post(
    "/transcribe-all/{project_id}",
    response_model=List[JobOut],
    status_code=status.HTTP_202_ACCEPTED,
)
async def transcribe_all_assets(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Trigger transcription for all un-transcribed assets in a project."""
    result = await db.execute(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.transcript_status == "pending",
        )
    )
    assets = result.scalars().all()
    if not assets:
        raise HTTPException(400, "No assets pending transcription")

    jobs = []
    for asset in assets:
        job = Job(
            project_id=asset.project_id,
            type=JobType.transcribe,
            status=JobStatus.pending,
            payload={"asset_id": str(asset.id)},
        )
        db.add(job)
        await db.flush()
        await db.refresh(job)

        from app.workers.tasks import transcribe_asset as transcribe_task
        task = transcribe_task.delay(str(asset.id), str(job.id))
        job.celery_task_id = task.id
        jobs.append(job)

    await db.flush()
    return jobs


# ── Import from URL ──────────────────────────────────────────────────────

@router.post(
    "/import-url/{project_id}",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_from_url(
    project_id: uuid.UUID,
    body: ImportUrlRequest,
    db: AsyncSession = Depends(get_db),
):
    """Download a video from a URL (TikTok, direct MP4, etc.) and add as a reference asset.

    This dispatches a Celery task that uses yt-dlp to download the video.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    if not body.url.strip():
        raise HTTPException(400, "URL is required")

    job = Job(
        project_id=project.id,
        type=JobType.transcribe,  # reusing closest type; could add a new one
        status=JobStatus.pending,
        payload={"project_id": str(project.id), "url": body.url.strip(), "mode": "import_url"},
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    from app.workers.tasks import import_video_from_url
    task = import_video_from_url.delay(str(project.id), body.url.strip(), str(job.id))
    job.celery_task_id = task.id
    await db.flush()

    return job

