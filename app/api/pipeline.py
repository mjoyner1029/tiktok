"""
Pipeline API — timeline-driven AI video editor endpoints.

New endpoints:
  POST /pipeline/start          — full pipeline: analyze → plan → preview render
  POST /pipeline/revise/{id}    — apply feedback to saved timeline → new version
  POST /pipeline/export/{id}    — final-quality render from saved timeline
  GET  /pipeline/project/{id}   — inspect saved project artifacts

Legacy endpoints (kept for backward compatibility):
  POST /pipeline/analyze-reference
  POST /pipeline/run
  POST /pipeline/analyze-footage
  POST /pipeline/edit
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/pipeline", tags=["pipeline"])

# ── Project storage root ──────────────────────────────────────────────────

def _projects_dir() -> Path:
    from app.config import get_settings
    d = Path(get_settings().render_output_dir).parent / "projects"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── LLM factory ──────────────────────────────────────────────────────────

def _build_llm():
    from tiktok_engine.llm_client import LLMClient
    from app.config import get_settings
    s = get_settings()
    api_key = s.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY") or None
    return LLMClient(api_key=api_key, model=s.anthropic_model)


# ── Render helper ─────────────────────────────────────────────────────────

def _render_timeline(timeline, asset_map: dict, work_dir: Path, preview: bool = False):
    """Render an EditTimeline → result dict with output_path.

    Uses RenderEngine.render_timeline() which validates the EditTimeline first,
    ensuring raw Claude output never reaches the render engine.
    """
    from app.services.render_engine import RenderEngine

    engine = RenderEngine(
        asset_resolver=lambda aid: asset_map.get(aid, aid),
        work_dir=work_dir,
    )
    # render_timeline() validates the schema, converts via to_render_spec(), then renders
    return engine.render_timeline(timeline, preview=preview)


# ─────────────────────────────────────────────────────────────────────────
# NEW: /pipeline/start
# ─────────────────────────────────────────────────────────────────────────

@router.post("/start")
async def pipeline_start(
    reference_url: List[str] = Form(default=[]),
    reference_file: Optional[List[UploadFile]] = File(default=None),
    footage: List[UploadFile] = File(...),
    music_file: Optional[UploadFile] = File(default=None),
    content_hint: str = Form(""),
    audio_mode: str = Form("reference_audio"),
    audio_volume: float = Form(-18.0),
    original_audio_volume: float = Form(0.0),
    rhythm_preset: str = Form("loose_sync"),
    preview: bool = Form(True),
    # ── Batch params ──────────────────────────────────────────────────────
    target_duration_sec: Optional[float] = Form(default=None),
    max_selected_segments: Optional[int] = Form(default=None),
    min_clip_variety: Optional[int] = Form(default=None),
):
    """
    Full beat-aware pipeline in one call:
      1. Analyze reference (URL or uploaded file) → fingerprint + beat data
      2. Analyze uploaded footage (50+ clip batch support) → scored footage_index
      3. Plan timeline with rhythm_preset + target_duration_sec → EditTimeline
      4. Render with audio_mode routing
      5. Save all artifacts to projects/{project_id}/

    Returns JSON with video_url, project_id, beat/timing metadata, and
    a batch_report describing deduplication / rejection statistics.
    """
    if not footage:
        raise HTTPException(status_code=422, detail="At least one footage file is required")
    if not reference_url and not reference_file:
        raise HTTPException(status_code=422, detail="Provide at least one reference_url or reference_file")

    _VALID_AUDIO_MODES = {"reference_audio", "uploaded_audio", "original_audio", "silent"}
    _VALID_RHYTHM_PRESETS = {"tight_sync", "loose_sync", "cinematic", "chaotic"}
    if audio_mode not in _VALID_AUDIO_MODES:
        raise HTTPException(status_code=422, detail=f"audio_mode must be one of {sorted(_VALID_AUDIO_MODES)}")
    if rhythm_preset not in _VALID_RHYTHM_PRESETS:
        raise HTTPException(status_code=422, detail=f"rhythm_preset must be one of {sorted(_VALID_RHYTHM_PRESETS)}")

    from app.config import get_settings as _gs
    _cfg = _gs()
    if len(footage) > _cfg.max_input_clips:
        raise HTTPException(
            status_code=422,
            detail=f"Too many footage clips: {len(footage)} > max_input_clips={_cfg.max_input_clips}",
        )

    # Clamp optional overrides to sensible ranges
    if target_duration_sec is not None and target_duration_sec <= 0:
        raise HTTPException(status_code=422, detail="target_duration_sec must be > 0")
    if max_selected_segments is not None and max_selected_segments < 1:
        raise HTTPException(status_code=422, detail="max_selected_segments must be ≥ 1")
    if min_clip_variety is not None and min_clip_variety < 1:
        raise HTTPException(status_code=422, detail="min_clip_variety must be ≥ 1")

    tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok_start_"))
    try:
        # Save footage files
        footage_paths: list[Path] = []
        for f in footage:
            suffix = Path(f.filename or "clip.mp4").suffix or ".mp4"
            dest = tmp_dir / f"footage_{len(footage_paths):02d}{suffix}"
            dest.write_bytes(await f.read())
            footage_paths.append(dest)

        # Save optional reference file(s)
        reference_file_paths: list[Path] = []
        if reference_file:
            for rf in reference_file:
                if rf.size and rf.size > 0:
                    suffix = Path(rf.filename or "ref.mp4").suffix or ".mp4"
                    dest = tmp_dir / f"reference_{len(reference_file_paths):02d}{suffix}"
                    dest.write_bytes(await rf.read())
                    reference_file_paths.append(dest)

        # Save optional music file
        music_path: Optional[Path] = None
        if music_file and music_file.size and music_file.size > 0:
            suffix = Path(music_file.filename or "music.mp3").suffix or ".mp3"
            music_path = tmp_dir / f"music{suffix}"
            music_path.write_bytes(await music_file.read())

        def _run():
            import uuid, json
            from app.services.reference_analyzer import ReferenceAnalyzer
            from app.services.footage_analyzer import FootageAnalyzer
            from app.services.edit_planner import EditPlanner
            from app.services.revision_engine import save_project_artifacts
            from app.services.embeddings import EmbeddingService, CLIP_AVAILABLE
            from app.services.music_analysis import MusicAnalyzer
            from app.config import get_settings
            s = get_settings()
            llm = _build_llm()

            project_id = uuid.uuid4().hex[:8]
            project_dir = _projects_dir() / project_id
            project_dir.mkdir(parents=True, exist_ok=True)
            work_dir = tmp_dir / "render"
            work_dir.mkdir(exist_ok=True)

            emb_svc = EmbeddingService(project_id, storage_root=s.storage_local_root)

            ref_urls = [u for u in reference_url if u.strip().startswith("http")]

            if ref_urls:
                logger.info("Analyzing %d reference URL(s)…", len(ref_urls))
                fingerprint = ReferenceAnalyzer(llm).analyze_urls(ref_urls, embedding_service=emb_svc)
            elif reference_file_paths:
                logger.info("Analyzing %d reference file(s)…", len(reference_file_paths))
                fingerprint = ReferenceAnalyzer(llm).analyze_files(
                    [str(p) for p in reference_file_paths], embedding_service=emb_svc
                )
            else:
                fingerprint = {}

            logger.info("Fingerprint: %d cuts, avg=%.2fs, pace=%s",
                        fingerprint.get("num_cuts", 0),
                        fingerprint.get("avg_shot_duration", 0),
                        fingerprint.get("pace", "unknown"))

            logger.info("Analyzing %d footage clip(s) (batch mode)…", len(footage_paths))
            analyzer = FootageAnalyzer(
                max_selected_segments=max_selected_segments,
            )
            footage_index = analyzer.analyze_all(footage_paths)
            emb_svc.enrich_footage_index(footage_index)

            # Extract batch report sentinel before embedding count
            batch_report = next(
                (c for c in footage_index if c.get("asset_id") == "__batch_report__"), {}
            )
            real_footage = [c for c in footage_index if c.get("asset_id") != "__batch_report__"]

            reference_embedded = "_ref_embedding" in fingerprint
            footage_segments_embedded = sum(
                1
                for clip in real_footage
                for seg in (clip.get("usable_segments") or clip.get("moments") or [])
                if "_embedding" in seg
            )
            embedding_status = {
                "clip_available": CLIP_AVAILABLE,
                "reference_embedded": reference_embedded,
                "footage_segments_embedded": footage_segments_embedded,
                "fallback_used": not (CLIP_AVAILABLE and reference_embedded),
            }

            # Beat analysis for uploaded music
            beat_data: dict = {}
            resolved_music_path: Optional[str] = None
            if audio_mode == "uploaded_audio" and music_path and music_path.exists():
                logger.info("Running beat analysis on uploaded music: %s", music_path)
                beat_data = MusicAnalyzer().analyze_safe(str(music_path))
                # Copy music to project dir so it persists
                import shutil as _shutil
                resolved_music_path = str(project_dir / music_path.name)
                _shutil.copy2(music_path, resolved_music_path)
            elif audio_mode == "reference_audio":
                # Use beat data already embedded in fingerprint from reference analysis
                beat_data = {k: fingerprint.get(k) for k in
                    ("beat_grid", "tempo_bpm", "beat_count", "downbeats",
                     "phrase_boundaries", "energy_curve", "intensity_curve") if fingerprint.get(k)}

            # Merge beat data into fingerprint for the planner
            if beat_data:
                fingerprint = {**fingerprint, **beat_data}

            ref_shot_count = fingerprint.get("num_cuts", 0) + 1 or 15
            max_shots = min(40, max(15, ref_shot_count))

            timeline = EditPlanner(llm).plan(
                fingerprint, footage_index,
                content_hint=content_hint.strip(),
                max_shots=max_shots,
                project_id=project_id,
                width=s.export_width, height=s.export_height, fps=s.export_fps,
                rhythm_preset=rhythm_preset,
                target_duration_sec=target_duration_sec,
                min_clip_variety=min_clip_variety,
            )

            # Apply audio settings to timeline
            timeline.audio_mode = audio_mode
            timeline.audio_mix_settings = {
                "music_volume": audio_volume,
                "original_audio_volume": original_audio_volume,
                "duck_under_speech": True,
                "fade_in_sec": 1.0,
                "fade_out_sec": 2.0,
            }
            if resolved_music_path:
                timeline.music_path = resolved_music_path

            logger.info("Timeline: %d clips, %d captions, %.1fs, audio_mode=%s",
                        len(timeline.clips), len(timeline.captions),
                        timeline.duration_sec, audio_mode)

            asset_map = {e["asset_id"]: e.get("file_path", e.get("path", "")) for e in footage_index}
            result = _render_timeline(timeline, asset_map, work_dir, preview=preview)
            output_path = Path(result["output_path"])

            # Save preview video into project dir
            video_dest = project_dir / ("preview.mp4" if preview else "final.mp4")
            import shutil as _shutil
            _shutil.copy2(output_path, video_dest)

            save_project_artifacts(
                project_dir, fingerprint, footage_index, timeline,
                embedding_status=embedding_status,
            )

            # Collect top clips for result display
            top_clips = []
            for clip in timeline.clips[:10]:
                top_clips.append({
                    "asset_id": clip.asset_id,
                    "start": round(clip.timeline_in, 2),
                    "end": round(clip.timeline_out, 2),
                    "duration": round(clip.timeline_out - clip.timeline_in, 2),
                    "score": round(getattr(clip, "score", 0) or 0, 3),
                    "score_breakdown": getattr(clip, "score_breakdown", {}),
                    "description": getattr(clip, "description", ""),
                })

            pacing_meta = getattr(timeline, "pacing_metadata", None) or {}

            meta = {
                "project_id": project_id,
                "audio_mode": audio_mode,
                "rhythm_preset": rhythm_preset,
                "bpm": fingerprint.get("tempo_bpm"),
                "beat_count": fingerprint.get("beat_count") or len(fingerprint.get("beat_grid", [])),
                "timeline_duration": round(timeline.duration_sec, 2),
                "clip_count": len(timeline.clips),
                "render_style": fingerprint.get("render_style") or fingerprint.get("pace"),
                "ranking_profile": fingerprint.get("ranking_profile") or "default",
                "escalation_score": pacing_meta.get("escalation_score"),
                "pacing_curve": pacing_meta.get("pacing_curve", []),
                "top_clips": top_clips,
                "embedding_status": embedding_status,
                "batch_report": {
                    k: v for k, v in batch_report.items()
                    if k != "asset_id"
                },
            }
            meta_file = project_dir / "pipeline_meta.json"
            meta_file.write_text(json.dumps(meta, indent=2))

            return project_id, video_dest, meta

        project_id, video_dest, meta = await asyncio.to_thread(_run)

        if not video_dest.exists() or video_dest.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="Render produced no output")

        # Return JSON so the frontend can separately fetch the video
        meta["video_url"] = f"/api/v1/pipeline/video/{project_id}/{'preview' if preview else 'final'}.mp4"
        return JSONResponse(content=meta)

    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("pipeline/start failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────
# GET /pipeline/video/{project_id}/{filename}  — serve saved video file
# ─────────────────────────────────────────────────────────────────────────

@router.get("/video/{project_id}/{filename}")
async def pipeline_video(project_id: str, filename: str):
    """Serve a saved preview/final video for a project."""
    # Sanitize to prevent path traversal
    safe_name = Path(filename).name
    if not safe_name.endswith(".mp4"):
        raise HTTPException(status_code=400, detail="Only .mp4 files are served")
    video_path = _projects_dir() / project_id / safe_name
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video not found")
    return FileResponse(path=str(video_path), media_type="video/mp4", filename=safe_name)


# ─────────────────────────────────────────────────────────────────────────
# NEW: /pipeline/revise/{project_id}
# ─────────────────────────────────────────────────────────────────────────

@router.post("/revise/{project_id}")
async def pipeline_revise(
    project_id: str,
    feedback: str = Form(...),
    preview: bool = Form(True),
):
    """
    Apply natural-language feedback to the latest saved timeline.
    Saves timeline_vN+1.json and returns a new render.

    Example feedback: "make it faster", "use more closeups", "less text",
    "make it more cinematic", "replace the first clip", "match the reference closer"
    """
    project_dir = _projects_dir() / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok_revise_"))
    try:
        def _run():
            from app.services.revision_engine import RevisionEngine, load_project, save_project_artifacts
            llm = _build_llm()
            project = load_project(project_dir)
            if "timeline" not in project:
                raise RuntimeError("No timeline found in project")

            timeline = project["timeline"]
            fingerprint = project.get("fingerprint", {})
            footage_index = project.get("footage_index", [])

            new_timeline = RevisionEngine(llm).revise(
                timeline, feedback, footage_index=footage_index, fingerprint=fingerprint,
            )

            work_dir = tmp_dir / "render"
            work_dir.mkdir(exist_ok=True)
            asset_map = {e["asset_id"]: e.get("file_path", e.get("path", "")) for e in footage_index}
            result = _render_timeline(new_timeline, asset_map, work_dir, preview=preview)
            output_path = Path(result["output_path"])

            save_project_artifacts(project_dir, fingerprint, footage_index, new_timeline)
            shutil.copy2(output_path, project_dir / f"preview_v{new_timeline.version}.mp4")

            return new_timeline.version, output_path

        new_version, output_path = await asyncio.to_thread(_run)
        if not output_path.exists():
            raise HTTPException(status_code=500, detail="Revision render produced no output")

        from starlette.background import BackgroundTask
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename=f"tiktok_{project_id}_v{new_version}.mp4",
            headers={"X-Project-Id": project_id, "X-Timeline-Version": str(new_version)},
            background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
        )

    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("pipeline/revise failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────
# NEW: /pipeline/export/{project_id}
# ─────────────────────────────────────────────────────────────────────────

@router.post("/export/{project_id}")
async def pipeline_export(project_id: str):
    """Render the latest saved timeline at full quality (1080×1920)."""
    project_dir = _projects_dir() / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok_export_"))
    try:
        def _run():
            from app.services.revision_engine import load_project
            project = load_project(project_dir)
            if "timeline" not in project:
                raise RuntimeError("No timeline found in project")

            timeline = project["timeline"]
            footage_index = project.get("footage_index", [])
            asset_map = {e["asset_id"]: e.get("file_path", e.get("path", "")) for e in footage_index}

            work_dir = tmp_dir / "render"
            work_dir.mkdir(exist_ok=True)
            result = _render_timeline(timeline, asset_map, work_dir, preview=False)
            output_path = Path(result["output_path"])
            shutil.copy2(output_path, project_dir / f"final_v{timeline.version}.mp4")
            return output_path

        output_path = await asyncio.to_thread(_run)
        if not output_path.exists():
            raise HTTPException(status_code=500, detail="Export produced no output")

        from starlette.background import BackgroundTask
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename=f"tiktok_{project_id}_final.mp4",
            headers={"X-Project-Id": project_id},
            background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
        )

    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("pipeline/export failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ─────────────────────────────────────────────────────────────────────────
# NEW: GET /pipeline/project/{project_id}
# ─────────────────────────────────────────────────────────────────────────

@router.get("/project/{project_id}")
async def get_project(project_id: str):
    """Return saved project metadata: fingerprint summary, timeline versions, file list."""
    project_dir = _projects_dir() / project_id
    if not project_dir.exists():
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found")

    files = {f.name: f.stat().st_size for f in sorted(project_dir.iterdir())}
    versions = sorted(project_dir.glob("timeline_v*.json"))
    summary: dict = {"project_id": project_id, "files": files, "timeline_versions": len(versions)}

    fp_file = project_dir / "reference_fingerprint.json"
    if fp_file.exists():
        import json
        fp = json.loads(fp_file.read_text())
        summary["fingerprint_summary"] = {
            k: fp.get(k) for k in
            ("num_cuts", "avg_shot_duration", "pace", "dominant_transition", "energy_level", "tempo_bpm")
        }

    if versions:
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline.model_validate_json(versions[-1].read_text())
        summary["latest_timeline"] = {
            "version": tl.version,
            "duration_sec": tl.duration_sec,
            "num_clips": len(tl.clips),
            "num_captions": len(tl.captions),
        }

    return summary


# ─────────────────────────────────────────────────────────────────────────
# LEGACY endpoints
# ─────────────────────────────────────────────────────────────────────────

class AnalyzeReferenceRequest(BaseModel):
    url: str


class RunPipelineRequest(BaseModel):
    reference_urls: List[str]
    content: str


@router.post("/analyze-reference")
async def analyze_reference(req: AnalyzeReferenceRequest):
    """Download a TikTok URL and extract its editing style fingerprint."""
    def _run():
        from app.services.reference_analyzer import ReferenceAnalyzer
        return ReferenceAnalyzer(_build_llm()).analyze_urls([req.url])

    try:
        fingerprint = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("analyze-reference failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return fingerprint


@router.post("/run")
async def run_pipeline(req: RunPipelineRequest):
    """Legacy: run 5-step pipeline → return edit plan JSON (no render)."""
    if not req.reference_urls:
        raise HTTPException(status_code=422, detail="At least one reference_url is required")

    def _run():
        from tiktok_engine.video_ingest import VideoIngestor
        from tiktok_engine.pipeline import EditPlanPipeline
        import json as _json
        llm = _build_llm()
        ingestor = VideoIngestor(llm)
        references = [ingestor.analyze_reference_url(u) for u in req.reference_urls]
        plan = EditPlanPipeline(llm).run(references, req.content)
        return _json.loads(plan.to_json())

    try:
        result = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("pipeline run failed")
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@router.post("/analyze-footage")
async def analyze_footage(file: UploadFile = File(...)):
    """Upload a footage file and return its scored usable-segment index."""
    suffix = Path(file.filename or "clip.mp4").suffix or ".mp4"
    tmp_path = ""

    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(await file.read())
            tmp_path = tmp.name

        def _run():
            from app.services.footage_analyzer import FootageAnalyzer
            return FootageAnalyzer().analyze_all([Path(tmp_path)])

        result = await asyncio.to_thread(_run)
    except Exception as exc:
        logger.exception("analyze-footage failed")
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return result[0] if result else {}


@router.post("/edit")
async def edit_footage(
    reference_url: List[str] = Form(...),
    footage: List[UploadFile] = File(...),
    content_hint: str = Form(""),
):
    """
    Legacy full-pipeline endpoint → returns rendered MP4.
    For new integrations, prefer POST /pipeline/start (adds project tracking).
    """
    if not footage:
        raise HTTPException(status_code=422, detail="At least one footage file is required")

    tmp_dir = Path(tempfile.mkdtemp(prefix="tiktok_edit_"))
    try:
        footage_paths: list[Path] = []
        for f in footage:
            suffix = Path(f.filename or "clip.mp4").suffix or ".mp4"
            dest = tmp_dir / f"footage_{len(footage_paths):02d}{suffix}"
            dest.write_bytes(await f.read())
            footage_paths.append(dest)

        def _run():
            from app.services.reference_analyzer import ReferenceAnalyzer
            from app.services.footage_analyzer import FootageAnalyzer
            from app.services.edit_planner import EditPlanner
            from app.config import get_settings
            s = get_settings()
            llm = _build_llm()

            ref_urls = [u for u in reference_url if u.strip().startswith("http")]
            fingerprint = ReferenceAnalyzer(llm).analyze_urls(ref_urls)
            footage_index = FootageAnalyzer().analyze_all(footage_paths)

            ref_shot_count = fingerprint.get("num_cuts", 0) + 1 or 15
            max_shots = min(40, max(15, ref_shot_count))
            timeline = EditPlanner(llm).plan(
                fingerprint, footage_index,
                content_hint=content_hint.strip(),
                max_shots=max_shots,
                width=s.export_width, height=s.export_height, fps=s.export_fps,
            )

            asset_map = {e["asset_id"]: e.get("file_path", e.get("path", "")) for e in footage_index}
            work_dir = tmp_dir / "render"
            work_dir.mkdir(exist_ok=True)
            result = _render_timeline(timeline, asset_map, work_dir, preview=False)
            return Path(result["output_path"])

        output_path = await asyncio.to_thread(_run)

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise HTTPException(status_code=500, detail="Render produced no output")

        from starlette.background import BackgroundTask
        return FileResponse(
            path=str(output_path),
            media_type="video/mp4",
            filename="edited_clip.mp4",
            background=BackgroundTask(lambda: shutil.rmtree(tmp_dir, ignore_errors=True)),
        )

    except HTTPException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.exception("edit-footage failed")
        raise HTTPException(status_code=500, detail=str(exc))
