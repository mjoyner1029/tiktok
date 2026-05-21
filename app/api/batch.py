"""Batch Processing API — Generate multiple videos at once."""

from __future__ import annotations

import uuid
from typing import List, Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_plan
from app.database import get_db
from app.models.db import User, SubscriptionPlan
from app.services.batch import BatchProcessor, ABTestService

router = APIRouter(prefix="/batch", tags=["batch"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class ContentItem(BaseModel):
    """Single content item in a batch."""
    
    title: str = Field(..., max_length=200)
    goal: str = Field("", max_length=1000)
    assets: List[str] = Field(default_factory=list, description="Asset file paths")


class CreateBatchRequest(BaseModel):
    """Request to create a batch of videos."""
    
    workspace_id: uuid.UUID
    style_profile: Dict[str, Any]
    content_items: List[ContentItem] = Field(..., min_items=2, max_items=50)


class QueueBatchRequest(BaseModel):
    """Request to queue batch renders."""
    
    project_ids: List[uuid.UUID] = Field(..., min_items=1)


class ABVariantRequest(BaseModel):
    """Request to create A/B test variants."""
    
    project_id: uuid.UUID
    num_variants: int = Field(3, ge=2, le=5)
    variation_params: Dict[str, List[Any]] = Field(
        default_factory=dict,
        description="Parameters to vary across variants",
        example={
            "avg_cut_duration": [1.0, 1.5, 2.0],
            "caption_position": ["top", "center", "bottom"],
        },
    )


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_batch(
    req: CreateBatchRequest,
    current_user: User = Depends(require_plan("creator_pro", "agency")),
    db: AsyncSession = Depends(get_db),
):
    """Create a batch of projects with the same style.
    
    **Requires**: Creator Pro plan or higher
    """
    if len(req.content_items) > 20 and current_user.subscription_plan != SubscriptionPlan.agency:
        raise HTTPException(
            status_code=403,
            detail="Enterprise plan required for batches > 20 items",
        )
    
    project_ids = await BatchProcessor.create_batch_job(
        workspace_id=req.workspace_id,
        style_profile=req.style_profile,
        content_items=[item.dict() for item in req.content_items],
        db=db,
    )
    
    return {
        "message": f"Created {len(project_ids)} projects",
        "project_ids": [str(pid) for pid in project_ids],
    }


@router.post("/queue", status_code=status.HTTP_202_ACCEPTED)
async def queue_batch_renders(
    req: QueueBatchRequest,
    current_user: User = Depends(require_plan("creator_pro", "agency")),
    db: AsyncSession = Depends(get_db),
):
    """Queue render jobs for a batch of projects.
    
    **Requires**: Creator Pro plan or higher
    """
    job_ids = await BatchProcessor.queue_batch_renders(
        project_ids=req.project_ids,
        db=db,
    )
    
    return {
        "message": f"Queued {len(job_ids)} render jobs",
        "job_ids": job_ids,
    }


@router.post("/status")
async def get_batch_status(
    project_ids: List[uuid.UUID],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get status of all projects in a batch."""
    status = await BatchProcessor.get_batch_status(
        project_ids=project_ids,
        db=db,
    )
    
    return status


@router.post("/ab-variants", status_code=status.HTTP_201_CREATED)
async def create_ab_variants(
    req: ABVariantRequest,
    current_user: User = Depends(require_plan("creator_pro", "agency")),
    db: AsyncSession = Depends(get_db),
):
    """Create multiple edit spec variants for A/B testing.
    
    **Requires**: Creator Pro plan or higher
    """
    variant_ids = await ABTestService.create_variants(
        project_id=req.project_id,
        num_variants=req.num_variants,
        variation_params=req.variation_params,
        db=db,
    )
    
    return {
        "message": f"Created {len(variant_ids)} variants",
        "variant_ids": variant_ids,
    }
