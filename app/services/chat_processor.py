"""Chat message processor - AI logic for conversational video creation."""

import logging
import re
from typing import Any, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    ChatConversation,
    User,
    Project,
    Asset,
    AssetType,
    Workspace,
)
from app.workers.tasks import analyze_and_generate, import_video_from_url

logger = logging.getLogger(__name__)


def extract_tiktok_urls(text: str) -> list[str]:
    """Extract TikTok URLs from text."""
    # Match TikTok URLs
    patterns = [
        r'https?://(?:www\.)?tiktok\.com/@[\w\._-]+/video/\d+',
        r'https?://vm\.tiktok\.com/[\w]+',
        r'https?://(?:www\.)?tiktok\.com/t/[\w]+',
    ]
    urls = []
    for pattern in patterns:
        urls.extend(re.findall(pattern, text))
    return urls


async def process_chat_message(
    conversation: ChatConversation,
    user_message: str,
    attachments: dict[str, Any],
    db: AsyncSession,
    user: User,
) -> Tuple[str, dict[str, Any]]:
    """Process user message and generate AI response.
    
    Returns (response_text, metadata)
    """
    metadata = {}
    
    # Extract TikTok URLs
    tiktok_urls = extract_tiktok_urls(user_message)
    
    # Check if we need to create a project
    if not conversation.project_id:
        # Create workspace if needed
        result = await db.execute(
            select(Workspace).where(Workspace.owner_id == user.id).limit(1)
        )
        workspace = result.scalar_one_or_none()
        if not workspace:
            workspace = Workspace(owner_id=user.id, name="My Workspace")
            db.add(workspace)
            await db.flush()
        
        # Create project
        project = Project(
            workspace_id=workspace.id,
            title=f"Chat: {conversation.title}",
            goal=user_message[:200],  # Use first part of message as goal
            target_platform="tiktok",
        )
        db.add(project)
        await db.flush()
        conversation.project_id = project.id
        metadata["created_project"] = str(project.id)
    
    project = await db.get(Project, conversation.project_id)
    
    # Handle TikTok URL references
    if tiktok_urls:
        logger.info(f"Found {len(tiktok_urls)} TikTok URL(s) in message")
        
        # Import reference video
        for url in tiktok_urls[:1]:  # Limit to first URL for now
            # Create job for importing video
            from app.models.db import Job, JobType, JobStatus
            job = Job(
                project_id=project.id,
                type=JobType.import_url,
                status=JobStatus.pending,
                payload={"url": url},
            )
            db.add(job)
            await db.flush()
            
            # Queue Celery task
            task = import_video_from_url.delay(str(project.id), url, str(job.id))
            job.celery_task_id = task.id
            metadata["import_job_id"] = str(job.id)
        
        response = f"🎯 Got it! I'm analyzing the style from that TikTok video.\n\n"
        
        # Check if user has uploaded content
        result = await db.execute(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type.in_([AssetType.raw_video, AssetType.image])
            ).limit(1)
        )
        has_content = result.scalar_one_or_none() is not None
        
        if has_content:
            response += "I see you've uploaded your videos/images. Once I finish analyzing the reference, I'll automatically generate an edit that matches that style!"
            
            # Trigger full pipeline
            from app.models.db import Job, JobType, JobStatus
            pipeline_job = Job(
                project_id=project.id,
                type=JobType.full_pipeline,
                status=JobStatus.pending,
            )
            db.add(pipeline_job)
            await db.flush()
            
            from app.workers.tasks import full_pipeline
            task = full_pipeline.delay(str(project.id), str(pipeline_job.id))
            pipeline_job.celery_task_id = task.id
            metadata["pipeline_job_id"] = str(pipeline_job.id)
        else:
            response += "Now upload your videos and images, and I'll create a TikTok matching that style!"
        
        return response, metadata
    
    # Check if user is asking about status
    status_keywords = ["status", "done", "ready", "progress", "finished", "complete"]
    if any(word in user_message.lower() for word in status_keywords):
        # Check job status
        from app.models.db import Job
        result = await db.execute(
            select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc()).limit(3)
        )
        jobs = result.scalars().all()
        
        if not jobs:
            return "No jobs running yet. Upload your content and share a reference TikTok URL to get started!", {}
        
        pending_or_running = [j for j in jobs if j.status in ["pending", "running"]]
        completed = [j for j in jobs if j.status == "completed"]
        
        if pending_or_running:
            response = f"⏳ Working on it! {len(pending_or_running)} task(s) in progress:\n"
            for job in pending_or_running:
                response += f"- {job.type}: {job.status}\n"
            return response, {"jobs": [str(j.id) for j in jobs]}
        elif completed:
            # Check for renders
            from app.models.db import Render, RenderStatus
            result = await db.execute(
                select(Render).where(
                    Render.project_id == project.id,
                    Render.status == RenderStatus.completed
                ).order_by(Render.created_at.desc()).limit(1)
            )
            render = result.scalar_one_or_none()
            
            if render:
                return (
                    f"✅ Your video is ready! Duration: {render.duration_sec:.1f}s\n\n"
                    f"Preview and download available below.",
                    {
                        "render_id": str(render.id),
                        "project_id": str(project.id),
                        "download_path": f"/api/v1/renders/{render.id}/download",
                        "thumbnail_path": f"/api/v1/renders/{render.id}/thumbnail" if render.thumbnail_url else None,
                        "duration_sec": render.duration_sec,
                    }
                )
            else:
                return "Analysis complete! Now rendering your video...", {}
        else:
            return "All tasks completed! Check your renders.", {}
    
    # Check if user wants to revert to a previous version
    msg_lower = user_message.lower()
    revert_keywords = ["revert", "go back", "undo", "previous version", "version"]
    if any(kw in msg_lower for kw in revert_keywords):
        import re as _re
        version_match = _re.search(r'v(\d+)|version\s*(\d+)', msg_lower)
        if version_match:
            target_version = int(version_match.group(1) or version_match.group(2))
            from app.models.db import EditSpec
            result = await db.execute(
                select(EditSpec).where(
                    EditSpec.project_id == project.id,
                    EditSpec.version == target_version,
                )
            )
            target_spec = result.scalar_one_or_none()
            if target_spec:
                return (
                    f"✅ Reverted to v{target_version}. Ready to render — say 'render' to produce the video.",
                    {"active_spec_version": target_version, "spec_id": str(target_spec.id)},
                )
            else:
                from app.models.db import EditSpec
                latest_result = await db.execute(
                    select(EditSpec).where(EditSpec.project_id == project.id)
                    .order_by(EditSpec.version.desc()).limit(1)
                )
                latest = latest_result.scalar_one_or_none()
                max_v = latest.version if latest else 0
                return f"I don't have a v{target_version}. Available versions: v1–v{max_v}.", {}

    # Check if user wants to make changes
    change_keywords = ["change", "edit", "revise", "faster", "slower", "zoom", "caption", "cut", "color", "brighter", "darker", "warmer", "cooler"]
    if any(word in msg_lower for word in change_keywords):
        # Check if we have an edit spec
        from app.models.db import EditSpec
        result = await db.execute(
            select(EditSpec).where(EditSpec.project_id == project.id).order_by(EditSpec.version.desc()).limit(1)
        )
        spec = result.scalar_one_or_none()
        
        if spec:
            # Trigger revision
            from app.services.ai_orchestrator import AIOrchestrator
            ai = AIOrchestrator()
            
            # Get style profile
            from app.models.db import StyleProfile
            style_result = await db.execute(
                select(StyleProfile).where(StyleProfile.project_id == project.id).order_by(StyleProfile.created_at.desc()).limit(1)
            )
            style = style_result.scalar_one_or_none()
            
            import asyncio
            revised = await asyncio.to_thread(
                ai.revise_edit_spec,
                current_spec=spec.spec_json,
                style_json=style.profile_json if style else {},
                feedback=user_message,
            )
            
            # Save new spec
            from app.models.db import EditSpecSource
            new_spec = EditSpec(
                project_id=project.id,
                version=spec.version + 1,
                spec_json=revised,
                source=EditSpecSource.revised,
                revision_note=user_message,
            )
            db.add(new_spec)
            await db.flush()
            
            # Automatically queue a new render for the revised spec
            from app.models.db import Job, JobType, JobStatus, Render, RenderStatus
            render = Render(
                project_id=project.id,
                edit_spec_id=new_spec.id,
                status=RenderStatus.queued,
            )
            db.add(render)
            await db.flush()
            render_job = Job(
                project_id=project.id,
                type=JobType.render,
                status=JobStatus.pending,
                payload={"render_id": str(render.id)},
            )
            db.add(render_job)
            await db.flush()
            from app.workers.tasks import render_project
            task = render_project.delay(str(render.id), str(render_job.id))
            render_job.celery_task_id = task.id

            return (
                f"✅ Updated the edit plan (v{new_spec.version}) and queued a new render. "
                f"Ask 'status' to check when it's ready.",
                {"spec_version": new_spec.version, "render_id": str(render.id)},
            )
        else:
            return "I don't have an edit plan yet. Let me analyze your reference video first!", {}
    
    # General helpful response
    if "help" in user_message.lower():
        return """Here's how to use me:

1️⃣ **Share a TikTok URL** you like (I'll analyze the editing style)
2️⃣ **Upload your videos/images** (your raw content)
3️⃣ **I'll automatically create** a TikTok matching that style!

You can also ask me to:
- "Make cuts faster"
- "Add more zoom effects"
- "Change the captions"
- Check status: "Is it done?"

What would you like to do?""", {}
    
    # Default response - encourage action
    result = await db.execute(
        select(Asset).where(Asset.project_id == project.id)
    )
    assets = result.scalars().all()
    references = [a for a in assets if a.type == AssetType.reference_video]
    content = [a for a in assets if a.type in [AssetType.raw_video, AssetType.image]]
    
    if not references and not content:
        return "I'm ready to help! Share a TikTok URL you like and upload your videos/images to get started.", {}
    elif not references:
        return "Great! Now share a TikTok URL you want to match, and I'll analyze its editing style.", {}
    elif not content:
        return "Nice! Now upload your videos and images, and I'll create a TikTok in that style.", {}
    else:
        return "I have everything I need! Let me generate your video. Ask 'status' to check progress.", {}
