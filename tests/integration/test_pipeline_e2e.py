"""End-to-end integration tests for /pipeline/start and /pipeline/export.

Uses synthetic data — no real ffmpeg, no LLM calls, no network requests.
All heavy services are mocked at their public API boundaries:

  - ReferenceAnalyzer.analyze_urls  → returns FAKE_FINGERPRINT
  - FootageAnalyzer.analyze_all     → returns FAKE_FOOTAGE_INDEX
  - EditPlanner.plan                → returns a valid EditTimeline
  - RenderEngine._render_impl       → writes a stub MP4 byte sequence

Tests verify:
  /pipeline/start  → creates reference_fingerprint.json, footage_index.json,
                      timeline_v1.json, preview.mp4, logs.json
  /pipeline/export → creates final_v1.mp4 WITHOUT re-running analysis or planning
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.edit_planner import EditPlanner
from app.services.footage_analyzer import FootageAnalyzer
from app.services.reference_analyzer import ReferenceAnalyzer
from app.services.render_engine import RenderEngine
from app.services.timeline_schema import (
    CaptionEvent,
    ClipEvent,
    ColorGrade,
    EditTimeline,
    TransitionEvent,
)

# ── Synthetic data ─────────────────────────────────────────────────────────

# Minimal but complete fingerprint that every pipeline service can consume
FAKE_FINGERPRINT: dict = {
    "duration_sec": 10.0,
    "aspect_ratio": "9:16",
    "cut_points": [0.7, 1.4, 2.1, 2.8, 3.5],
    "shot_durations": [0.7, 0.7, 0.7, 0.7, 0.7],
    "avg_shot_duration": 0.7,
    "num_cuts": 5,
    "pace": "fast",
    "beat_points": [],
    "tempo_bpm": None,
    "downbeats": [],
    "cut_to_beat_alignment": 0.0,
    "transitions": ["hard_cut"],
    "dominant_transition": "hard_cut",
    "motion_pattern": "slow_push",
    "motion_style": {"primary": "slow_push"},
    "caption_style": {
        "uses_text": True,
        "position": "center",
        "case": "uppercase",
        "words_per_caption": 2,
        "animation": "pop",
        "font_size_class": "large",
        "has_stroke": True,
        "all_caps": True,
        "max_words": 3,
    },
    "color_profile": {
        "brightness": 0.0, "contrast": 1.0, "saturation": 1.0,
        "gamma": 1.0, "luma_avg": 128.0,
        "temperature": "neutral", "black_level": "normal",
    },
    "color_grade": {
        "brightness": 0.0, "contrast": 1.0, "saturation": 1.0,
        "gamma": 1.0, "luma_avg": 128.0,
    },
    "hook_style": "bold opener",
    "energy_level": "high",
    "tone": "aspirational",
}

# Minimal footage entry that produces a non-empty asset_map in the pipeline
_SEGS = [
    {"start": 0.0, "end": 2.0, "score": 9.5, "tags": ["sharp_detail"], "type": "sharp_detail"},
    {"start": 2.0, "end": 4.0, "score": 8.0, "tags": ["usable"], "type": "usable"},
    {"start": 4.0, "end": 6.0, "score": 7.5, "tags": ["usable"], "type": "usable"},
]
FAKE_FOOTAGE_INDEX: list = [
    {
        "asset_id": "footage_00",
        "file_path": "/tmp/fake_footage_00.mp4",
        "path": "/tmp/fake_footage_00.mp4",
        "duration_sec": 10.0,
        "duration": 10.0,
        "resolution": [1080, 1920],
        "has_speech": False,
        "usable_segments": _SEGS,
        "moments": _SEGS,  # legacy alias
        "quality": {"blur_score": 0.1, "brightness": "normal", "stability": "stable"},
    }
]

# Minimal valid MP4 stub (ftyp box header only) — nonzero size so existence check passes
_STUB_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42iso2" + b"\x00" * 32

# Stub bytes uploaded as footage (content doesn't matter — FootageAnalyzer is mocked)
_STUB_FOOTAGE_BYTES = _STUB_MP4


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_timeline(project_id: str) -> EditTimeline:
    """Build a minimal valid EditTimeline for use as a plan mock return value."""
    return EditTimeline(
        project_id=project_id,
        version=1,
        duration_sec=1.4,
        width=1080,
        height=1920,
        fps=30,
        clips=[
            ClipEvent(
                asset_id="footage_00",
                source_in=0.0, source_out=0.7,
                timeline_in=0.0, timeline_out=0.7,
                transition_out=TransitionEvent(type="hard_cut", duration=0.0),
            ),
            ClipEvent(
                asset_id="footage_00",
                source_in=2.0, source_out=2.7,
                timeline_in=0.7, timeline_out=1.4,
                transition_out=TransitionEvent(type="hard_cut", duration=0.0),
            ),
        ],
        captions=[
            CaptionEvent(text="HOOK SHOT", start=0.1, end=0.6),
            CaptionEvent(text="KEEP GOING", start=0.8, end=1.3),
        ],
        color_grade=ColorGrade(),
    )


def _fake_render(spec, color_grade=None, render_style=None) -> dict:
    """
    Mock side_effect for RenderEngine._render_impl.

    When patch.object replaces an instance method with a MagicMock, Python's
    descriptor protocol is bypassed (MagicMock is not a descriptor), so the mock
    is called WITHOUT `self`.  The signature here therefore has no `self`.

    Writes a stub MP4 to a temp file and returns its path so the pipeline's
    shutil.copy2() and FileResponse succeed.
    """
    fd, path = tempfile.mkstemp(suffix="_e2e_fake.mp4", prefix="tiktok_test_")
    os.write(fd, _STUB_MP4)
    os.close(fd)
    return {"output_path": path}


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture()
def project_store(tmp_path: Path) -> Path:
    """Isolated directory that receives all project artifacts during this test."""
    d = tmp_path / "projects"
    d.mkdir()
    return d


@pytest.fixture()
def mock_services(project_store: Path):
    """
    Patch every heavy service at its class-method boundary.

    Returns a dict of the MagicMock instances so tests can assert on call counts
    after resetting them in the `started_project` fixture.
    """
    mock_ref = MagicMock(return_value=FAKE_FINGERPRINT)
    mock_footage = MagicMock(return_value=FAKE_FOOTAGE_INDEX)
    mock_plan = MagicMock(
        side_effect=lambda fp, fi, **kw: _make_timeline(kw.get("project_id") or "testproj")
    )
    mock_render = MagicMock(side_effect=_fake_render)

    with (
        # Redirect _projects_dir() so artifacts land in our isolated tmp dir
        patch("app.api.pipeline._projects_dir", new=lambda: project_store),
        # Mock LLM factory — avoids real API key requirements
        patch("app.api.pipeline._build_llm", return_value=MagicMock()),
        # Mock all heavy service methods — no ffmpeg, no network, no Claude
        patch.object(ReferenceAnalyzer, "analyze_urls", mock_ref),
        patch.object(FootageAnalyzer, "analyze_all", mock_footage),
        patch.object(EditPlanner, "plan", mock_plan),
        patch.object(RenderEngine, "_render_impl", mock_render),
    ):
        yield {
            "mock_ref": mock_ref,
            "mock_footage": mock_footage,
            "mock_plan": mock_plan,
            "mock_render": mock_render,
        }


@pytest.fixture()
def client(mock_services) -> TestClient:
    """FastAPI TestClient operating inside the mocked service context."""
    return TestClient(app, raise_server_exceptions=True)


def _post_start(client: TestClient, *, preview: bool = True) -> "requests.Response":
    """Helper: POST /api/v1/pipeline/start with minimal synthetic inputs.

    httpx 0.28 requires all parts of a multipart/form-data request to go through
    the ``files`` parameter.  Plain form fields are encoded as (None, value, None)
    tuples, which httpx sends as part-fields without a Content-Disposition filename.
    """
    return client.post(
        "/api/v1/pipeline/start",
        files=[
            ("reference_url", (None, "http://fake.tiktok.example.com/video/001", None)),
            ("content_hint", (None, "fitness motivation", None)),
            ("preview", (None, str(preview).lower(), None)),
            ("footage", ("clip.mp4", io.BytesIO(_STUB_FOOTAGE_BYTES), "video/mp4")),
        ],
    )


# ── /pipeline/start ────────────────────────────────────────────────────────

class TestPipelineStart:

    # ── HTTP contract ───────────────────────────────────────────────────

    def test_returns_200(self, client):
        resp = _post_start(client)
        assert resp.status_code == 200, resp.text

    def test_content_type_is_mp4(self, client):
        resp = _post_start(client)
        assert resp.headers.get("content-type", "").startswith("video/mp4")

    def test_x_project_id_header_present(self, client):
        resp = _post_start(client)
        assert "X-Project-Id" in resp.headers

    def test_x_project_id_is_8_chars(self, client):
        resp = _post_start(client)
        assert len(resp.headers["X-Project-Id"]) == 8

    def test_response_body_nonempty(self, client):
        resp = _post_start(client)
        assert len(resp.content) > 0

    # ── Artifact: reference_fingerprint.json ───────────────────────────

    def test_creates_reference_fingerprint_json(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "reference_fingerprint.json"
        assert artifact.exists(), f"reference_fingerprint.json missing from {project_store / project_id}"

    def test_fingerprint_json_contains_num_cuts(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        fp = json.loads((project_store / project_id / "reference_fingerprint.json").read_text())
        assert fp["num_cuts"] == FAKE_FINGERPRINT["num_cuts"]

    def test_fingerprint_json_contains_pace(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        fp = json.loads((project_store / project_id / "reference_fingerprint.json").read_text())
        assert fp["pace"] == "fast"

    # ── Artifact: footage_index.json ───────────────────────────────────

    def test_creates_footage_index_json(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "footage_index.json"
        assert artifact.exists(), "footage_index.json missing"

    def test_footage_index_json_is_list(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        fi = json.loads((project_store / project_id / "footage_index.json").read_text())
        assert isinstance(fi, list)
        assert len(fi) >= 1

    def test_footage_index_json_has_asset_id(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        fi = json.loads((project_store / project_id / "footage_index.json").read_text())
        assert fi[0]["asset_id"] == "footage_00"

    # ── Artifact: timeline_v1.json ─────────────────────────────────────

    def test_creates_timeline_v1_json(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "timeline_v1.json"
        assert artifact.exists(), "timeline_v1.json missing"

    def test_timeline_v1_json_version_is_1(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        tl = json.loads((project_store / project_id / "timeline_v1.json").read_text())
        assert tl["version"] == 1

    def test_timeline_v1_json_has_clips(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        tl = json.loads((project_store / project_id / "timeline_v1.json").read_text())
        assert len(tl["clips"]) >= 1

    def test_timeline_v1_json_is_valid_edit_timeline(self, client, project_store):
        """Timeline JSON must deserialize back into a valid EditTimeline object."""
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        raw = (project_store / project_id / "timeline_v1.json").read_text()
        tl = EditTimeline.model_validate_json(raw)
        assert isinstance(tl, EditTimeline)
        errors = tl.validate_timeline()
        assert errors == [], f"Persisted timeline has validation errors: {errors}"

    # ── Artifact: preview.mp4 ──────────────────────────────────────────

    def test_creates_preview_mp4(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "preview.mp4"
        assert artifact.exists(), "preview.mp4 missing"

    def test_preview_mp4_nonempty(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "preview.mp4"
        assert artifact.stat().st_size > 0, "preview.mp4 is empty"

    # ── Artifact: logs.json ────────────────────────────────────────────

    def test_creates_logs_json(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        artifact = project_store / project_id / "logs.json"
        assert artifact.exists(), "logs.json missing"

    def test_logs_json_has_fingerprint_summary(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        logs = json.loads((project_store / project_id / "logs.json").read_text())
        assert "fingerprint_summary" in logs

    def test_logs_json_has_footage_summary(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        logs = json.loads((project_store / project_id / "logs.json").read_text())
        assert "footage_summary" in logs

    def test_logs_json_has_timeline_summary(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        logs = json.loads((project_store / project_id / "logs.json").read_text())
        assert "timeline_summary" in logs

    def test_logs_json_no_validation_errors(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        logs = json.loads((project_store / project_id / "logs.json").read_text())
        assert logs.get("validation_errors") == [], (
            f"logs.json reports validation errors: {logs.get('validation_errors')}"
        )

    def test_logs_json_timeline_version_is_1(self, client, project_store):
        resp = _post_start(client)
        project_id = resp.headers["X-Project-Id"]
        logs = json.loads((project_store / project_id / "logs.json").read_text())
        assert logs["timeline_version"] == 1

    # ── All 5 artifacts in one assertion ──────────────────────────────

    def test_all_five_artifacts_present(self, client, project_store):
        """Single comprehensive check — all 5 required files must exist."""
        resp = _post_start(client)
        assert resp.status_code == 200, resp.text
        project_id = resp.headers["X-Project-Id"]
        project_dir = project_store / project_id

        required = {
            "reference_fingerprint.json",
            "footage_index.json",
            "timeline_v1.json",
            "preview.mp4",
            "logs.json",
        }
        actual = {f.name for f in project_dir.iterdir()}
        missing = required - actual
        assert not missing, (
            f"Missing artifacts in {project_dir}: {missing}\n"
            f"Found: {actual}"
        )

    # ── Validation errors ──────────────────────────────────────────────

    def test_missing_footage_returns_422(self, client):
        """Request without any footage file must be rejected."""
        resp = client.post(
            "/api/v1/pipeline/start",
            files=[("reference_url", (None, "http://fake.example.com/v", None))],
        )
        assert resp.status_code == 422

    def test_missing_reference_url_returns_422(self, client):
        """Request without reference_url must be rejected."""
        resp = client.post(
            "/api/v1/pipeline/start",
            files=[("footage", ("c.mp4", io.BytesIO(_STUB_FOOTAGE_BYTES), "video/mp4"))],
        )
        assert resp.status_code == 422

    # ── Service call counts ────────────────────────────────────────────

    def test_reference_analyzer_called_once(self, client, mock_services):
        _post_start(client)
        mock_services["mock_ref"].assert_called_once()

    def test_footage_analyzer_called_once(self, client, mock_services):
        _post_start(client)
        mock_services["mock_footage"].assert_called_once()

    def test_edit_planner_called_once(self, client, mock_services):
        _post_start(client)
        mock_services["mock_plan"].assert_called_once()

    def test_render_engine_called_once(self, client, mock_services):
        _post_start(client)
        mock_services["mock_render"].assert_called_once()

    def test_render_engine_receives_valid_spec(self, client, mock_services):
        """The spec passed to _render_impl must have the required keys."""
        _post_start(client)
        call_spec = mock_services["mock_render"].call_args[0][0]  # first positional arg
        assert "output" in call_spec
        assert "tracks" in call_spec
        assert "video" in call_spec["tracks"]

    def test_preview_mode_sets_480x854_in_spec(self, client, mock_services):
        """preview=true must produce a 480×854 spec in the render call."""
        _post_start(client, preview=True)
        call_spec = mock_services["mock_render"].call_args[0][0]
        assert call_spec["output"]["width"] == 480
        assert call_spec["output"]["height"] == 854


# ── /pipeline/export ───────────────────────────────────────────────────────

class TestPipelineExport:
    """
    Verify that POST /pipeline/export/{project_id}:
      1. Creates final_v1.mp4 in the project directory
      2. Does NOT re-run reference analysis, footage analysis, or planning
    """

    @pytest.fixture()
    def started(self, client, project_store, mock_services):
        """
        Run /pipeline/start to create a project, then reset mock call counts
        so assertions in export tests measure only the export call.
        """
        resp = _post_start(client)
        assert resp.status_code == 200, f"/pipeline/start failed: {resp.text}"
        project_id = resp.headers["X-Project-Id"]

        # Clear call history from the start phase
        mock_services["mock_ref"].reset_mock()
        mock_services["mock_footage"].reset_mock()
        mock_services["mock_plan"].reset_mock()
        mock_services["mock_render"].reset_mock()

        return project_id, project_store / project_id

    # ── HTTP contract ───────────────────────────────────────────────────

    def test_export_returns_200(self, client, started):
        project_id, _ = started
        resp = client.post(f"/api/v1/pipeline/export/{project_id}")
        assert resp.status_code == 200, resp.text

    def test_export_returns_mp4(self, client, started):
        project_id, _ = started
        resp = client.post(f"/api/v1/pipeline/export/{project_id}")
        assert resp.headers.get("content-type", "").startswith("video/mp4")

    def test_export_returns_x_project_id(self, client, started):
        project_id, _ = started
        resp = client.post(f"/api/v1/pipeline/export/{project_id}")
        assert resp.headers.get("X-Project-Id") == project_id

    def test_unknown_project_returns_404(self, client):
        resp = client.post("/api/v1/pipeline/export/doesnotexist000")
        assert resp.status_code == 404

    # ── Artifact: final_v1.mp4 ─────────────────────────────────────────

    def test_creates_final_v1_mp4(self, client, started):
        project_id, project_dir = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        final = project_dir / "final_v1.mp4"
        assert final.exists(), f"final_v1.mp4 not found in {project_dir}"

    def test_final_v1_mp4_nonempty(self, client, started):
        project_id, project_dir = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        assert (project_dir / "final_v1.mp4").stat().st_size > 0

    # ── No re-analysis / re-planning ───────────────────────────────────

    def test_does_not_call_reference_analyzer(self, client, started, mock_services):
        """Export must load the saved fingerprint, not re-analyze the reference."""
        project_id, _ = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        mock_services["mock_ref"].assert_not_called()

    def test_does_not_call_footage_analyzer(self, client, started, mock_services):
        """Export must load footage_index from disk, not re-analyze footage."""
        project_id, _ = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        mock_services["mock_footage"].assert_not_called()

    def test_does_not_call_edit_planner(self, client, started, mock_services):
        """Export must use the persisted timeline, not re-plan."""
        project_id, _ = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        mock_services["mock_plan"].assert_not_called()

    def test_calls_render_engine_exactly_once(self, client, started, mock_services):
        """Export must invoke the render engine exactly once (for the final pass)."""
        project_id, _ = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        mock_services["mock_render"].assert_called_once()

    def test_export_uses_full_resolution(self, client, started, mock_services):
        """Export (non-preview) must use 1080×1920, not the 480×854 preview size."""
        project_id, _ = started
        client.post(f"/api/v1/pipeline/export/{project_id}")
        call_spec = mock_services["mock_render"].call_args[0][0]
        assert call_spec["output"]["width"] == 1080
        assert call_spec["output"]["height"] == 1920

    def test_export_uses_persisted_timeline(self, client, started, project_store):
        """
        The spec passed to _render_impl must reflect the saved timeline.
        Specifically, the video track must have the asset IDs from timeline_v1.json.
        """
        project_id, project_dir = started

        # Read the saved timeline to know what asset IDs were persisted
        tl = EditTimeline.model_validate_json(
            (project_dir / "timeline_v1.json").read_text()
        )
        expected_assets = {c.asset_id for c in tl.clips}

        # Capture the spec the render engine was called with during export
        captured: dict = {}

        def capture_render(spec, color_grade=None, render_style=None):
            captured.update(spec)
            fd, path = tempfile.mkstemp(suffix="_capture.mp4")
            os.write(fd, _STUB_MP4)
            os.close(fd)
            return {"output_path": path}

        with patch.object(RenderEngine, "_render_impl", MagicMock(side_effect=capture_render)):
            resp = client.post(f"/api/v1/pipeline/export/{project_id}")

        assert resp.status_code == 200, resp.text
        actual_assets = {v["asset_id"] for v in captured.get("tracks", {}).get("video", [])}
        assert actual_assets == expected_assets, (
            f"Export spec asset IDs {actual_assets} don't match timeline {expected_assets}"
        )
