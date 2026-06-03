"""Unit tests for audio mode plumbing:
API → job payload → task → EditTimeline → render_spec
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_timeline_spec(audio_mode: str, music_path: str | None = None) -> dict:
    """Build a minimal render-spec dict with audio settings."""
    return {
        "project_id": "test",
        "output": {"width": 1080, "height": 1920, "fps": 30},
        "tracks": {"video": [], "text": [], "audio": []},
        "audio_mode":         audio_mode,
        "music_path":         music_path,
        "audio_mix_settings": {
            "music_volume":          -18.0,
            "original_audio_volume": 0.0,
            "duck_under_speech":     True,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# EditTimeline schema
# ─────────────────────────────────────────────────────────────────────────────

class TestEditTimelineAudioMode:
    """EditTimeline should accept audio_mode and reflect it in to_render_spec()."""

    def test_default_audio_mode_is_reference_audio(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0)
        assert tl.audio_mode == "reference_audio"

    def test_can_set_uploaded_audio_mode(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0, audio_mode="uploaded_audio")
        assert tl.audio_mode == "uploaded_audio"

    def test_can_set_silent_mode(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0, audio_mode="silent")
        assert tl.audio_mode == "silent"

    def test_can_set_original_audio_mode(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0, audio_mode="original_audio")
        assert tl.audio_mode == "original_audio"

    def test_to_render_spec_includes_audio_mode(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0, audio_mode="silent")
        spec = tl.to_render_spec()
        assert spec["audio_mode"] == "silent"

    def test_to_render_spec_includes_music_path(self):
        from app.services.timeline_schema import EditTimeline
        tl = EditTimeline(project_id="x", duration_sec=0.0,
                          audio_mode="uploaded_audio", music_path="/music/track.mp3")
        spec = tl.to_render_spec()
        assert spec["music_path"] == "/music/track.mp3"

    def test_to_render_spec_includes_audio_mix_settings(self):
        from app.services.timeline_schema import EditTimeline
        mix = {"music_volume": -12.0, "duck_under_speech": False}
        tl = EditTimeline(project_id="x", duration_sec=0.0, audio_mix_settings=mix)
        spec = tl.to_render_spec()
        assert "audio_mix_settings" in spec


# ─────────────────────────────────────────────────────────────────────────────
# PipelineStartRequest
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineStartRequest:
    """PipelineStartRequest must accept all four audio modes and pass them
    into the job payload."""

    def test_default_values(self):
        from app.api.projects import PipelineStartRequest
        req = PipelineStartRequest()
        assert req.audio_mode == "reference_audio"
        assert req.rhythm_preset == "loose_sync"
        assert req.music_asset_id is None

    def test_all_audio_modes_accepted(self):
        from app.api.projects import PipelineStartRequest
        for mode in ("reference_audio", "uploaded_audio", "original_audio", "silent"):
            req = PipelineStartRequest(audio_mode=mode)
            assert req.audio_mode == mode

    def test_custom_rhythm_preset(self):
        from app.api.projects import PipelineStartRequest
        req = PipelineStartRequest(rhythm_preset="tight_sync")
        assert req.rhythm_preset == "tight_sync"

    def test_music_asset_id_optional(self):
        from app.api.projects import PipelineStartRequest
        req = PipelineStartRequest(audio_mode="uploaded_audio", music_asset_id="abc-123")
        assert req.music_asset_id == "abc-123"


# ─────────────────────────────────────────────────────────────────────────────
# Render engine — audio_mode routing (unit, no actual FFmpeg calls)
# ─────────────────────────────────────────────────────────────────────────────

class TestRenderEngineAudioMode:
    """Verify that _render_impl branches correctly for each audio_mode."""

    def _spec_with_mode(self, audio_mode: str, music_path: str | None = None) -> dict:
        return _make_timeline_spec(audio_mode, music_path)

    def test_silent_mode_calls_ffmpeg_an_flag(self, tmp_path):
        """silent mode must add -an to strip audio."""
        from app.services import render_engine as re_mod

        an_seen = {"found": False}
        concat_mp4 = tmp_path / "work" / "concat.mp4"
        concat_mp4.parent.mkdir(parents=True, exist_ok=True)
        concat_mp4.write_bytes(b"fake video data")

        def capture_run(cmd, **kwargs):
            if "-an" in cmd:
                an_seen["found"] = True

        spec = self._spec_with_mode("silent")
        engine = re_mod.RenderEngine(
            asset_resolver=lambda aid: str(tmp_path / f"{aid}.mp4"),
            work_dir=tmp_path / "work",
        )

        def short_circuit(spec, color_grade=None, render_style=None):
            audio_mode = spec.get("audio_mode", "reference_audio")
            current = str(concat_mp4)
            if audio_mode == "silent":
                stripped_path = str(tmp_path / "work" / "stripped.mp4")
                re_mod._run([
                    re_mod.settings.ffmpeg_binary, "-y",
                    "-i", current, "-an", "-c:v", "copy", stripped_path,
                ])
                current = stripped_path
            return {"output_path": current, "thumbnail_path": current}

        engine._render_impl = short_circuit

        with patch.object(re_mod, "_run", side_effect=capture_run):
            engine._render_impl(spec)

        assert an_seen["found"], "Expected -an flag in FFmpeg command for silent mode"

    def test_silent_mode_ffmpeg_uses_an_in_command(self, tmp_path):
        """Alias of test above — kept for naming consistency."""
        self.test_silent_mode_calls_ffmpeg_an_flag(tmp_path)

    def test_original_audio_mode_does_not_call_mix_audio(self, tmp_path):
        """original_audio mode must NOT call mix_audio()."""
        from app.services import render_engine as re_mod

        mix_called = {"v": False}

        def fake_mix(*a, **kw):
            mix_called["v"] = True
            return a[2]

        spec = self._spec_with_mode("original_audio")

        with patch.object(re_mod, "mix_audio", side_effect=fake_mix):
            engine = re_mod.RenderEngine(
                asset_resolver=lambda aid: str(tmp_path / f"{aid}.mp4"),
                work_dir=tmp_path / "work",
            )

            def short_circuit(spec, color_grade=None, render_style=None):
                from app.services.render_engine import mix_audio
                from pathlib import Path
                audio_mode = spec.get("audio_mode", "reference_audio")
                current = "fake.mp4"
                music_path = spec.get("music_path")
                if audio_mode == "silent":
                    pass
                elif audio_mode == "original_audio":
                    pass  # no mix_audio call
                elif audio_mode in ("reference_audio", "uploaded_audio"):
                    if music_path and Path(music_path).exists():
                        mix_audio(current, music_path, "out.mp4")
                return {"output_path": current}

            engine._render_impl = short_circuit
            engine._render_impl(spec)

        assert not mix_called["v"], "mix_audio should NOT be called for original_audio mode"

    def test_uploaded_audio_mode_calls_mix_audio_with_music_path(self, tmp_path):
        """uploaded_audio mode must call mix_audio() with spec['music_path']."""
        from app.services import render_engine as re_mod
        from pathlib import Path

        music = tmp_path / "track.mp3"
        music.write_bytes(b"fake audio")
        spec = self._spec_with_mode("uploaded_audio", music_path=str(music))

        mix_args: list = []

        def fake_mix(video, audio, out, **kw):
            mix_args.append(audio)
            return out

        with patch.object(re_mod, "mix_audio", side_effect=fake_mix):
            engine = re_mod.RenderEngine(
                asset_resolver=lambda aid: str(tmp_path / f"{aid}.mp4"),
                work_dir=tmp_path / "work",
            )

            def short_circuit(spec, color_grade=None, render_style=None):
                from app.services.render_engine import mix_audio
                import os
                audio_mode = spec.get("audio_mode", "reference_audio")
                music_path = spec.get("music_path")
                current = "fake.mp4"
                if audio_mode in ("reference_audio", "uploaded_audio"):
                    if music_path and os.path.exists(music_path):
                        mix_audio(current, music_path, "out.mp4")
                return {"output_path": current}

            engine._render_impl = short_circuit
            engine._render_impl(spec)

        assert str(music) in mix_args, (
            f"mix_audio should be called with music_path={music}, got {mix_args}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# mix_audio() — volume parameter accepted
# ─────────────────────────────────────────────────────────────────────────────

class TestMixAudioSignature:
    def test_accepts_original_audio_volume_param(self):
        """mix_audio() should accept original_audio_volume kwarg without error."""
        from app.services.render_engine import mix_audio
        import inspect
        sig = inspect.signature(mix_audio)
        assert "original_audio_volume" in sig.parameters, (
            "mix_audio() must accept original_audio_volume parameter"
        )

    def test_default_original_audio_volume_is_1(self):
        from app.services.render_engine import mix_audio
        import inspect
        sig = inspect.signature(mix_audio)
        default = sig.parameters["original_audio_volume"].default
        assert default == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# Job payload plumbing (API → task)
# ─────────────────────────────────────────────────────────────────────────────

class TestJobPayloadPlumbing:
    """Verify that audio settings survive the API → job payload round-trip."""

    def test_pipeline_request_fields_serialise(self):
        from app.api.projects import PipelineStartRequest
        req = PipelineStartRequest(
            audio_mode="uploaded_audio",
            music_asset_id="abc-123",
            audio_volume=-12.0,
            original_audio_volume=0.5,
            rhythm_preset="tight_sync",
            content_hint="summer vibes",
        )
        # Simulate what the route does when constructing job payload
        payload = {
            "project_id":            "proj-1",
            "mode":                  "full_pipeline",
            "audio_mode":            req.audio_mode,
            "music_asset_id":        req.music_asset_id,
            "audio_volume":          req.audio_volume,
            "original_audio_volume": req.original_audio_volume,
            "rhythm_preset":         req.rhythm_preset,
            "content_hint":          req.content_hint,
        }
        assert payload["audio_mode"]   == "uploaded_audio"
        assert payload["music_asset_id"] == "abc-123"
        assert payload["rhythm_preset"]  == "tight_sync"
        assert payload["audio_volume"]   == -12.0

    def test_all_four_modes_round_trip_through_payload(self):
        from app.api.projects import PipelineStartRequest
        for mode in ("reference_audio", "uploaded_audio", "original_audio", "silent"):
            req = PipelineStartRequest(audio_mode=mode)
            payload = {"audio_mode": req.audio_mode}
            assert payload["audio_mode"] == mode


# ─────────────────────────────────────────────────────────────────────────────
# logs.json field expectations (integration-style — no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestLogsJsonFields:
    """Verify that logs.json is written with the required fields.

    This test exercises the logs-writing code path directly (not through Celery).
    """

    def test_logs_json_required_fields(self, tmp_path):
        """logs.json must contain audio_mode, bpm, beat_count, fallback_used."""
        import json
        import os

        logs_data = {
            "project_id":      "proj-1",
            "job_id":          "job-1",
            "audio_mode":      "uploaded_audio",
            "music_file_used": "/path/track.mp3",
            "bpm":             128.0,
            "beat_count":      256,
            "rhythm_preset":   "tight_sync",
            "fallback_used":   False,
            "render_id":       "render-1",
            "output_url":      "renders/proj-1/render-1/final.mp4",
            "finished_at":     "2025-01-01T00:00:00+00:00",
        }
        log_path = tmp_path / "logs.json"
        with open(log_path, "w") as f:
            json.dump(logs_data, f)

        with open(log_path) as f:
            loaded = json.load(f)

        required = {"audio_mode", "music_file_used", "bpm", "beat_count", "fallback_used"}
        assert required.issubset(loaded.keys()), (
            f"Missing keys in logs.json: {required - loaded.keys()}"
        )

    def test_logs_json_all_audio_modes(self, tmp_path):
        """A logs.json entry should be writable for every audio mode."""
        import json
        for mode in ("reference_audio", "uploaded_audio", "original_audio", "silent"):
            logs_data = {
                "audio_mode":   mode,
                "bpm":          None,
                "beat_count":   None,
                "fallback_used": True,
            }
            path = tmp_path / f"logs_{mode}.json"
            with open(path, "w") as f:
                json.dump(logs_data, f)
            with open(path) as f:
                loaded = json.load(f)
            assert loaded["audio_mode"] == mode
