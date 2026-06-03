"""Async Celery tasks — media analysis, AI generation, rendering.

Each task operates in a worker process, reads/writes via the DB + storage,
and updates job status throughout.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.workers.celery_app import celery_app
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Visual analysis aggregation helper ───────────────────────────────────────

def _aggregate_visual_analyses(analyses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Average visual style metrics across multiple reference videos."""
    if not analyses:
        return {}
    if len(analyses) == 1:
        return analyses[0]

    avg_cut = sum(a.get("avg_cut_duration_sec", 2.0) for a in analyses) / len(analyses)
    all_cuts = []
    for a in analyses:
        all_cuts.extend(a.get("cut_timestamps", []))

    grade_keys = ["brightness", "contrast", "saturation", "gamma", "luma_avg"]
    aggregated_grade: Dict[str, float] = {}
    for k in grade_keys:
        values = [
            a.get("color_grade", {}).get(k)
            for a in analyses
            if a.get("color_grade", {}).get(k) is not None
        ]
        if values:
            aggregated_grade[k] = round(sum(values) / len(values), 3)

    return {
        "cut_timestamps": sorted(all_cuts),
        "avg_cut_duration_sec": round(avg_cut, 2),
        "num_cuts": sum(a.get("num_cuts", 0) for a in analyses),
        "color_grade": aggregated_grade,
    }


# ── DB helpers (sync, for Celery workers) ────────────────────────────────────

# Module-level sync engine — created once, reused across tasks.
_sync_engine = None
_SyncSessionLocal = None


def _get_sync_session():
    """Return a synchronous SQLAlchemy session for worker context.

    Uses a module-level engine to avoid creating a new connection pool
    on every single task invocation.
    """
    global _sync_engine, _SyncSessionLocal
    if _sync_engine is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        sync_url = settings.database_url.replace("+asyncpg", "").replace("+aiosqlite", "")
        _sync_engine = create_engine(sync_url, pool_size=5, max_overflow=10, pool_pre_ping=True)
        _SyncSessionLocal = sessionmaker(bind=_sync_engine)
    return _SyncSessionLocal()


def _update_job_status(
    session: Session,
    job_id: str,
    status: str,
    error: str | None = None,
    result: dict | None = None,
):
    from app.models.db import Job, JobStatus
    job = session.get(Job, uuid.UUID(job_id))
    if job:
        job.status = JobStatus(status)
        if status == "running" and not job.started_at:
            job.started_at = datetime.now(timezone.utc)
        if status in ("completed", "failed"):
            job.finished_at = datetime.now(timezone.utc)
        if error:
            job.error_message = error
        if result:
            job.result = result
        session.commit()


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 1: TRANSCRIBE
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.workers.tasks.transcribe_asset")
def transcribe_asset(self, asset_id: str, job_id: str):
    """Transcribe a single asset (video/audio) and store results in DB."""
    session = _get_sync_session()
    try:
        _update_job_status(session, job_id, "running")

        from app.models.db import Asset, TranscriptStatus
        asset = session.get(Asset, uuid.UUID(asset_id))
        if not asset:
            raise ValueError(f"Asset {asset_id} not found")

        asset.transcript_status = TranscriptStatus.processing
        session.commit()

        # Resolve local path
        from app.services.storage import get_storage
        storage = get_storage()
        local_path = storage.get_local_path(asset.storage_url)

        # Run analysis
        from app.services.media_analyzer import analyze_asset
        analysis = analyze_asset(local_path)

        # Update asset
        info = analysis["media_info"]
        asset.duration_sec = info["duration_sec"]
        asset.width = info["width"]
        asset.height = info["height"]
        asset.transcript = analysis["transcript"]["text"]
        asset.transcript_segments = analysis["transcript"]
        asset.silence_map = {"silences": analysis["silences"]}
        asset.transcript_status = TranscriptStatus.completed
        asset.metadata_extra = {
            "sentences": analysis["sentences"],
            "media_info": info,
        }
        session.commit()

        _update_job_status(session, job_id, "completed", result={"asset_id": asset_id})
        logger.info("Transcription complete for asset %s", asset_id)

    except Exception as exc:
        logger.exception("Transcription failed for asset %s", asset_id)
        _update_job_status(session, job_id, "failed", error=str(exc))
        # Update asset status
        try:
            from app.models.db import Asset, TranscriptStatus
            asset = session.get(Asset, uuid.UUID(asset_id))
            if asset:
                asset.transcript_status = TranscriptStatus.failed
                session.commit()
        except Exception:
            pass
        raise
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 2: STYLE ANALYSIS + EDIT SPEC GENERATION
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.workers.tasks.analyze_and_generate")
def analyze_and_generate(self, project_id: str, job_id: str):
    """Extract style from references, generate edit spec from raw clips."""
    session = _get_sync_session()
    try:
        _update_job_status(session, job_id, "running")

        from app.models.db import (
            Asset, AssetType, EditSpec, EditSpecSource,
            Project, ProjectStatus, StyleProfile,
        )

        project = session.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"Project {project_id} not found")

        project.status = ProjectStatus.analyzing
        session.commit()

        # Gather reference transcripts + visual style from each reference
        refs = session.execute(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type == AssetType.reference_video,
            )
        ).scalars().all()

        ref_transcripts = []
        visual_analyses = []
        for r in refs:
            if r.transcript:
                ref_transcripts.append(r.transcript)
            elif r.metadata_extra and "description" in r.metadata_extra:
                ref_transcripts.append(r.metadata_extra["description"])

            # Extract visual style if we have a local path for this reference
            try:
                from app.services.storage import get_storage
                from app.services.media_analyzer import extract_visual_style
                storage = get_storage()
                local_path = storage.get_local_path(r.storage_url)
                visual = extract_visual_style(local_path)
                visual_analyses.append(visual)
                logger.info("Visual style extracted for ref %s: cuts=%d, avg_cut=%.2fs",
                            r.id, visual["num_cuts"], visual["avg_cut_duration_sec"])
            except Exception as e:
                logger.warning("Visual style extraction failed for ref %s: %s", r.id, e)

        if not ref_transcripts:
            raise ValueError("No reference transcripts available. Transcribe references first.")

        # Aggregate visual data across all references
        aggregated_visual: dict | None = None
        if visual_analyses:
            aggregated_visual = _aggregate_visual_analyses(visual_analyses)

        # Gather raw clip info
        raw_clips = session.execute(
            select(Asset).where(
                Asset.project_id == project.id,
                Asset.type == AssetType.raw_video,
            )
        ).scalars().all()

        clips_data = []
        for clip in raw_clips:
            clip_info = {
                "asset_id": str(clip.id),
                "duration_sec": clip.duration_sec or 0,
                "transcript": clip.transcript or "",
                "sentences": (clip.metadata_extra or {}).get("sentences", []),
                "silences": (clip.silence_map or {}).get("silences", []),
            }
            clips_data.append(clip_info)

        if not clips_data:
            raise ValueError("No raw clips available. Upload raw footage first.")

        # Run AI
        from app.services.ai_orchestrator import AIOrchestrator
        ai = AIOrchestrator()

        project.status = ProjectStatus.generating
        session.commit()

        style_profile, edit_spec = ai.run_full_pipeline_sync(
            reference_transcripts=ref_transcripts,
            clips=clips_data,
            project_id=str(project.id),
            goal=project.goal or "",
            max_duration=settings.max_output_duration_sec,
            visual_data=aggregated_visual,
        )

        # Ensure color_grade from visual analysis is preserved in the profile
        if aggregated_visual and "color_grade" in aggregated_visual:
            style_profile.setdefault("color_grade", aggregated_visual["color_grade"])

        # Save style profile
        sp = StyleProfile(
            project_id=project.id,
            name=f"Auto-extracted ({style_profile.get('tone', 'default')})",
            profile_json=style_profile,
            model_name=settings.anthropic_model,
        )
        session.add(sp)

        # Save edit spec
        es = EditSpec(
            project_id=project.id,
            version=1,
            spec_json=edit_spec,
            source=EditSpecSource.ai,
        )
        session.add(es)

        project.status = ProjectStatus.ready
        session.commit()

        _update_job_status(session, job_id, "completed", result={
            "style_profile_id": str(sp.id),
            "edit_spec_id": str(es.id),
        })
        logger.info("AI pipeline complete for project %s", project_id)

    except Exception as exc:
        logger.exception("AI pipeline failed for project %s", project_id)
        _update_job_status(session, job_id, "failed", error=str(exc))
        try:
            from app.models.db import Project, ProjectStatus
            project = session.get(Project, uuid.UUID(project_id))
            if project:
                project.status = ProjectStatus.failed
                session.commit()
        except Exception:
            pass
        raise
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 3: RENDER
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.workers.tasks.render_project")
def render_project(self, render_id: str, job_id: str):
    """Render a draft MP4 from an edit spec."""
    session = _get_sync_session()
    try:
        _update_job_status(session, job_id, "running")

        from app.models.db import (
            Asset, EditSpec, Render, RenderStatus, Project, ProjectStatus,
        )
        from app.services.render_engine import RenderEngine
        from app.services.storage import get_storage

        render = session.get(Render, uuid.UUID(render_id))
        if not render:
            raise ValueError(f"Render {render_id} not found")

        render.status = RenderStatus.preprocessing
        session.commit()

        edit_spec = session.get(EditSpec, render.edit_spec_id)
        if not edit_spec:
            raise ValueError(f"EditSpec {render.edit_spec_id} not found")

        project = session.get(Project, render.project_id)

        # Build asset resolver: asset_id → local file path
        storage = get_storage()

        def resolve_asset(asset_id: str) -> str:
            asset = session.get(Asset, uuid.UUID(asset_id))
            if asset:
                return storage.get_local_path(asset.storage_url)
            # Fallback: try as direct path
            return asset_id

        # Render
        render.status = RenderStatus.rendering
        session.commit()

        # Look up the style profile to get color grade for this project
        from app.models.db import StyleProfile
        style_result = session.execute(
            select(StyleProfile).where(
                StyleProfile.project_id == render.project_id
            ).order_by(StyleProfile.created_at.desc()).limit(1)
        )
        style = style_result.scalars().first()
        color_grade = (style.profile_json or {}).get("color_grade") if style else None

        engine = RenderEngine(asset_resolver=resolve_asset)
        result = engine.render(edit_spec.spec_json, color_grade=color_grade)

        # Upload outputs to storage
        render.status = RenderStatus.postprocessing
        session.commit()

        output_key = f"renders/{render.project_id}/{render.id}/final.mp4"
        with open(result["output_path"], "rb") as f:
            storage.save(f, output_key, "video/mp4")
        render.output_url = output_key

        if "thumbnail_path" in result:
            thumb_key = f"renders/{render.project_id}/{render.id}/thumb.jpg"
            with open(result["thumbnail_path"], "rb") as f:
                storage.save(f, thumb_key, "image/jpeg")
            render.thumbnail_url = thumb_key

        if "subtitle_path" in result:
            sub_key = f"renders/{render.project_id}/{render.id}/captions.ass"
            with open(result["subtitle_path"], "rb") as f:
                storage.save(f, sub_key, "text/plain")
            render.preview_url = sub_key

        render.status = RenderStatus.completed
        render.finished_at = datetime.now(timezone.utc)

        # Get duration from the rendered file
        from app.services.media_analyzer import get_media_info
        final_info = get_media_info(result["output_path"])
        render.duration_sec = final_info["duration_sec"]
        render.file_size_bytes = final_info["file_size_bytes"]

        if project:
            project.status = ProjectStatus.ready
        session.commit()

        _update_job_status(session, job_id, "completed", result={
            "render_id": render_id,
            "output_url": output_key,
        })
        logger.info("Render complete: %s", render_id)

    except Exception as exc:
        logger.exception("Render failed: %s", render_id)
        _update_job_status(session, job_id, "failed", error=str(exc))
        try:
            from app.models.db import Render, RenderStatus
            render = session.get(Render, uuid.UUID(render_id))
            if render:
                render.status = RenderStatus.failed
                render.error_message = str(exc)
                render.finished_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            pass
        raise
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 4: FULL PIPELINE (convenience — chains all steps)
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.workers.tasks.full_pipeline")
def full_pipeline(self, project_id: str, job_id: str):
    """Run the entire pipeline: transcribe all assets → AI → render.

    Audio settings are read from the job payload written by the API route.
    """
    session = _get_sync_session()
    try:
        _update_job_status(session, job_id, "running")

        from app.models.db import (
            Asset, AssetType, Job, JobType, JobStatus,
            EditSpec, Render, RenderStatus,
        )

        # ── Read audio settings from job payload ─────────────────────────
        job_obj = session.get(Job, uuid.UUID(job_id))
        payload = (job_obj.payload or {}) if job_obj else {}
        audio_mode            = payload.get("audio_mode", "reference_audio")
        music_asset_id        = payload.get("music_asset_id")
        audio_volume          = float(payload.get("audio_volume", -18.0))
        original_audio_volume = float(payload.get("original_audio_volume", 0.0))
        rhythm_preset         = payload.get("rhythm_preset", "loose_sync")
        content_hint          = payload.get("content_hint", "")

        # 1. Find all assets needing transcription
        assets = session.execute(
            select(Asset).where(Asset.project_id == uuid.UUID(project_id))
        ).scalars().all()

        needs_transcription = [
            a for a in assets
            if a.transcript_status.value == "pending"
            and a.type in (AssetType.reference_video, AssetType.raw_video)
        ]

        # 2. Transcribe sequentially (in-process for simplicity in full_pipeline)
        for asset in needs_transcription:
            logger.info("Transcribing asset %s (%s)", asset.id, asset.filename)
            from app.services.storage import get_storage
            from app.services.media_analyzer import analyze_asset
            from app.models.db import TranscriptStatus

            storage = get_storage()
            local_path = storage.get_local_path(asset.storage_url)
            analysis = analyze_asset(local_path)

            info = analysis["media_info"]
            asset.duration_sec = info["duration_sec"]
            asset.width = info["width"]
            asset.height = info["height"]
            asset.transcript = analysis["transcript"]["text"]
            asset.transcript_segments = analysis["transcript"]
            asset.silence_map = {"silences": analysis["silences"]}
            asset.transcript_status = TranscriptStatus.completed
            asset.metadata_extra = {
                "sentences": analysis["sentences"],
                "media_info": info,
            }
            session.commit()

        # 3. Run AI pipeline
        logger.info("Running AI pipeline for project %s", project_id)
        from app.services.ai_orchestrator import AIOrchestrator

        refs = [a for a in assets if a.type == AssetType.reference_video]
        raw  = [a for a in assets if a.type == AssetType.raw_video]

        ref_transcripts = [a.transcript for a in refs if a.transcript]
        clips_data = [
            {
                "asset_id":    str(a.id),
                "duration_sec": a.duration_sec or 0,
                "transcript":  a.transcript or "",
                "sentences":   (a.metadata_extra or {}).get("sentences", []),
                "silences":    (a.silence_map or {}).get("silences", []),
            }
            for a in raw
        ]

        # Extract visual style from each reference video
        visual_analyses = []
        fingerprint_beat_data: dict = {}

        for ref in refs:
            try:
                from app.services.storage import get_storage
                from app.services.media_analyzer import extract_visual_style
                storage = get_storage()
                ref_path = storage.get_local_path(ref.storage_url)
                visual = extract_visual_style(ref_path)
                visual_analyses.append(visual)
                logger.info("Visual style extracted for ref %s: cuts=%d, avg_cut=%.2fs",
                            ref.id, visual["num_cuts"], visual["avg_cut_duration_sec"])
                # Keep beat data from the first reference that has it
                if not fingerprint_beat_data and visual.get("beat_grid"):
                    fingerprint_beat_data = {k: visual[k] for k in (
                        "beat_grid", "beat_points", "downbeats", "phrase_boundaries",
                        "energy_curve", "intensity_curve", "transient_peaks", "tempo_bpm",
                    ) if k in visual}
            except Exception as e:
                logger.warning("Visual style extraction failed for ref %s: %s", ref.id, e)

        aggregated_visual = _aggregate_visual_analyses(visual_analyses) if visual_analyses else None

        # ── Music analysis for uploaded_audio mode ────────────────────────
        music_file_path: str | None = None
        music_analysis_result: dict = {}
        fallback_used = False

        if audio_mode == "uploaded_audio" and music_asset_id:
            try:
                from app.services.storage import get_storage
                from app.services.music_analysis import MusicAnalyzer
                storage = get_storage()
                music_asset = session.get(Asset, uuid.UUID(music_asset_id))
                if music_asset:
                    music_file_path = storage.get_local_path(music_asset.storage_url)
                    music_analysis_result = MusicAnalyzer().analyze_safe(music_file_path)
                    if music_analysis_result:
                        # Uploaded music beats take precedence over reference beats
                        fingerprint_beat_data = music_analysis_result
                        logger.info(
                            "Music analysis: bpm=%.1f beats=%d",
                            music_analysis_result.get("tempo_bpm", 0),
                            music_analysis_result.get("beat_count", 0),
                        )
                    else:
                        fallback_used = True
                        logger.warning("Music analysis returned empty; using reference beats")
            except Exception as exc:
                fallback_used = True
                logger.warning("Music analysis failed: %s", exc)
        elif audio_mode == "reference_audio" and fingerprint_beat_data:
            music_analysis_result = fingerprint_beat_data
        # original_audio / silent: no beat analysis

        ai = AIOrchestrator()
        from app.models.db import Project as ProjectModel
        project_obj = session.get(ProjectModel, uuid.UUID(project_id))
        project_goal = project_obj.goal if project_obj else ""

        style_profile, edit_spec = ai.run_full_pipeline_sync(
            reference_transcripts=ref_transcripts,
            clips=clips_data,
            project_id=project_id,
            goal=project_goal or "",
            visual_data=aggregated_visual,
        )

        # Ensure measured color grade is preserved
        if aggregated_visual and "color_grade" in aggregated_visual:
            style_profile.setdefault("color_grade", aggregated_visual["color_grade"])

        # Merge beat data into fingerprint / style profile
        if fingerprint_beat_data:
            for k, v in fingerprint_beat_data.items():
                style_profile.setdefault(k, v)

        from app.models.db import StyleProfile, EditSpecSource
        sp = StyleProfile(
            project_id=uuid.UUID(project_id),
            name=f"Auto ({style_profile.get('tone', 'default')})",
            profile_json=style_profile,
            model_name=settings.anthropic_model,
        )
        session.add(sp)

        # ── Re-run EditPlanner with beat sync + rhythm preset ────────────
        try:
            from app.services.edit_planner import EditPlanner
            from app.services.reference_analyzer import ReferenceAnalyzer

            # Build footage_index from raw clips
            footage_index_data = clips_data  # already has asset_id, duration_sec, etc.

            planner = EditPlanner(llm=None)  # LLM not needed for re-plan (AI spec already done)
            # Only re-plan if beat data is available; otherwise use AI edit_spec as-is
            if fingerprint_beat_data and fingerprint_beat_data.get("beat_grid"):
                beat_fingerprint = {**style_profile, **fingerprint_beat_data}
                timeline = planner.plan(
                    fingerprint=beat_fingerprint,
                    footage_index=footage_index_data,
                    content_hint=content_hint,
                    project_id=project_id,
                    rhythm_preset=rhythm_preset,
                )
                # Apply audio settings
                timeline.audio_mode = audio_mode
                if music_file_path:
                    timeline.music_path = music_file_path
                elif audio_mode == "reference_audio" and refs:
                    from app.services.storage import get_storage as _gs
                    timeline.music_path = _gs().get_local_path(refs[0].storage_url)
                timeline.audio_mix_settings = {
                    "music_volume":          audio_volume,
                    "original_audio_volume": original_audio_volume,
                    "duck_under_speech":     True,
                }
                beat_sync_edit_spec = timeline.to_render_spec()
                es = EditSpec(
                    project_id=uuid.UUID(project_id),
                    version=1,
                    spec_json=beat_sync_edit_spec,
                    source=EditSpecSource.ai,
                )
            else:
                # No beat data — use the original AI spec but inject audio settings
                if isinstance(edit_spec, dict):
                    edit_spec["audio_mode"]         = audio_mode
                    edit_spec["audio_mix_settings"] = {
                        "music_volume":          audio_volume,
                        "original_audio_volume": original_audio_volume,
                        "duck_under_speech":     True,
                    }
                    if music_file_path:
                        edit_spec["music_path"] = music_file_path
                es = EditSpec(
                    project_id=uuid.UUID(project_id),
                    version=1,
                    spec_json=edit_spec,
                    source=EditSpecSource.ai,
                )
        except Exception as plan_exc:
            logger.warning("Beat-sync re-plan failed (%s), using original AI spec", plan_exc)
            fallback_used = True
            es = EditSpec(
                project_id=uuid.UUID(project_id),
                version=1,
                spec_json=edit_spec,
                source=EditSpecSource.ai,
            )

        session.add(es)
        session.flush()

        # 4. Render
        logger.info("Starting render for project %s", project_id)
        render = Render(
            project_id=uuid.UUID(project_id),
            edit_spec_id=es.id,
            status=RenderStatus.rendering,
        )
        session.add(render)
        session.flush()

        from app.services.render_engine import RenderEngine
        from app.services.storage import get_storage
        storage = get_storage()

        def resolve_asset(asset_id: str) -> str:
            a = session.get(Asset, uuid.UUID(asset_id))
            if a:
                return storage.get_local_path(a.storage_url)
            return asset_id

        engine = RenderEngine(asset_resolver=resolve_asset)
        color_grade = style_profile.get("color_grade")
        result = engine.render(es.spec_json, color_grade=color_grade)

        # Save outputs
        output_key = f"renders/{project_id}/{render.id}/final.mp4"
        with open(result["output_path"], "rb") as f:
            storage.save(f, output_key, "video/mp4")
        render.output_url = output_key
        render.status = RenderStatus.completed
        render.finished_at = datetime.now(timezone.utc)

        from app.services.media_analyzer import get_media_info
        final_info = get_media_info(result["output_path"])
        render.duration_sec = final_info["duration_sec"]

        session.commit()

        # ── Write logs.json ───────────────────────────────────────────────
        try:
            import os as _os
            logs_data = {
                "project_id":       project_id,
                "job_id":           job_id,
                "audio_mode":       audio_mode,
                "music_file_used":  music_file_path,
                "bpm":              music_analysis_result.get("tempo_bpm"),
                "beat_count":       music_analysis_result.get("beat_count"),
                "rhythm_preset":    rhythm_preset,
                "fallback_used":    fallback_used,
                "render_id":        str(render.id),
                "output_url":       output_key,
                "finished_at":      datetime.now(timezone.utc).isoformat(),
            }
            log_dir = _os.path.join(settings.storage_path, "projects", project_id)
            _os.makedirs(log_dir, exist_ok=True)
            log_path = _os.path.join(log_dir, "logs.json")
            with open(log_path, "w") as lf:
                json.dump(logs_data, lf, indent=2)
        except Exception as log_exc:
            logger.warning("Could not write logs.json: %s", log_exc)

        _update_job_status(session, job_id, "completed", result={
            "render_id":       str(render.id),
            "edit_spec_id":    str(es.id),
            "style_profile_id": str(sp.id),
        })
        logger.info("Full pipeline complete for project %s", project_id)

    except Exception as exc:
        logger.exception("Full pipeline failed for project %s", project_id)
        _update_job_status(session, job_id, "failed", error=str(exc))
        raise
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════
#  TASK 5: IMPORT VIDEO FROM URL
# ═══════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.workers.tasks.import_video_from_url")
def import_video_from_url(self, project_id: str, url: str, job_id: str):
    """Download a video from a URL (TikTok, YouTube, direct link) using yt-dlp
    and save it as a reference_video asset."""
    import subprocess
    import tempfile
    import os

    session = _get_sync_session()
    try:
        _update_job_status(session, job_id, "running")

        from app.models.db import Asset, AssetType, Project

        project = session.get(Project, uuid.UUID(project_id))
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Download with yt-dlp to a temp directory
        with tempfile.TemporaryDirectory(prefix="tiktok_import_") as tmpdir:
            output_template = os.path.join(tmpdir, "video.%(ext)s")
            cmd = [
                "yt-dlp",
                "--no-playlist",
                "--max-filesize", "200M",
                "-f", "mp4/best[ext=mp4]/best",
                "--merge-output-format", "mp4",
                "-o", output_template,
                url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                raise RuntimeError(f"yt-dlp failed: {proc.stderr[:500]}")

            # Find the downloaded file
            downloaded = None
            for f in os.listdir(tmpdir):
                if f.endswith((".mp4", ".mkv", ".webm")):
                    downloaded = os.path.join(tmpdir, f)
                    break

            if not downloaded or not os.path.exists(downloaded):
                raise RuntimeError("No video file found after download")

            # Get file info
            file_size = os.path.getsize(downloaded)
            filename = f"import_{uuid.uuid4().hex[:8]}.mp4"

            # Save to storage
            from app.services.storage import get_storage, make_asset_key
            storage = get_storage()
            key = make_asset_key(project_id, "reference_video", filename)

            with open(downloaded, "rb") as fh:
                storage.save(fh, key, "video/mp4")

            # Probe media info
            from app.services.media_analyzer import get_media_info
            info = get_media_info(downloaded)

            # Create asset record
            asset = Asset(
                project_id=project.id,
                type=AssetType.reference_video,
                filename=filename,
                storage_url=key,
                mime_type="video/mp4",
                file_size_bytes=file_size,
                duration_sec=info.get("duration_sec"),
                width=info.get("width"),
                height=info.get("height"),
            )
            session.add(asset)
            session.commit()

        _update_job_status(session, job_id, "completed", result={
            "asset_id": str(asset.id),
            "url": url,
        })
        logger.info("Imported video from %s for project %s", url, project_id)

    except Exception as exc:
        logger.exception("URL import failed: %s", url)
        _update_job_status(session, job_id, "failed", error=str(exc))
        raise
    finally:
        session.close()
