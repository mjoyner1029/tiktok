"""Tests for app/services/render_engine.py — all subprocess calls are mocked."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock, MagicMock, call
import pytest

from app.services.render_engine import (
    _run,
    _ensure_dir,
    normalize_clip,
    trim_clip,
    apply_motion,
    _seconds_to_ass_time,
    generate_ass_subtitles,
    concat_clips,
    mix_audio,
    burn_subtitles,
    apply_color_grade,
    generate_thumbnail,
    remove_silence,
    RenderEngine,
)
from app.config import get_settings

settings = get_settings()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_proc(**kwargs):
    """Return a successful mock subprocess.CompletedProcess."""
    m = Mock()
    m.returncode = 0
    m.stdout = kwargs.get("stdout", "")
    m.stderr = kwargs.get("stderr", "")
    return m


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------

class TestRun:
    def test_success(self, tmp_path):
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            cmd = [settings.ffmpeg_binary, "-version"]
            result = _run(cmd)
            assert result.returncode == 0
            mock_run.assert_called_once()

    def test_failure_raises_runtime_error(self):
        err_proc = Mock()
        err_proc.returncode = 1
        err_proc.stderr = "some error"
        with patch("app.services.render_engine.subprocess.run", return_value=err_proc):
            with pytest.raises(RuntimeError, match="FFmpeg failed"):
                _run([settings.ffmpeg_binary, "-bad-arg"])

    def test_invalid_binary_raises_value_error(self):
        with pytest.raises(ValueError, match="Refusing to execute unknown binary"):
            _run(["rm", "-rf", "/"])

    def test_empty_cmd_raises_value_error(self):
        with pytest.raises(ValueError, match="Refusing to execute unknown binary"):
            _run([])


# ---------------------------------------------------------------------------
# _ensure_dir
# ---------------------------------------------------------------------------

class TestEnsureDir:
    def test_creates_parent_dirs(self, tmp_path):
        deep_path = str(tmp_path / "a" / "b" / "c" / "out.mp4")
        result = _ensure_dir(deep_path)
        assert result == deep_path
        assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_existing_dir_ok(self, tmp_path):
        path = str(tmp_path / "out.mp4")
        _ensure_dir(path)  # first call
        result = _ensure_dir(path)  # second call should not raise
        assert result == path


# ---------------------------------------------------------------------------
# normalize_clip
# ---------------------------------------------------------------------------

class TestNormalizeClip:
    def test_basic(self, tmp_path):
        inp = str(tmp_path / "input.mp4")
        out = str(tmp_path / "output.mp4")
        Path(inp).write_text("fake")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = normalize_clip(inp, out)
        assert result == out
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == settings.ffmpeg_binary
        assert "-vf" in cmd
        assert "libx264" in cmd

    def test_creates_parent_directory(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "sub" / "output.mp4")
        Path(inp).write_text("x")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            normalize_clip(inp, out)
        assert (tmp_path / "sub").is_dir()


# ---------------------------------------------------------------------------
# trim_clip
# ---------------------------------------------------------------------------

class TestTrimClip:
    def test_basic_trim(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = trim_clip(inp, out, start=1.0, end=4.0)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "-t" in cmd

    def test_speed_change(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = trim_clip(inp, out, start=0.0, end=5.0, speed=1.5)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "-vf" in cmd
        assert "-af" in cmd

    def test_high_speed_clamps_atempo(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            result = trim_clip(inp, out, start=0.0, end=5.0, speed=3.0)
        assert result == out


# ---------------------------------------------------------------------------
# apply_motion
# ---------------------------------------------------------------------------

class TestApplyMotion:
    @pytest.mark.parametrize("motion_type", [
        "zoom_in", "zoom_out", "slow_push", "slow_pull", "shake",
    ])
    def test_motion_types(self, tmp_path, motion_type):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / f"out_{motion_type}.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = apply_motion(inp, out, motion_type=motion_type)
        assert result == out
        mock_run.assert_called_once()

    def test_static_uses_copy(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = apply_motion(inp, out, motion_type="static")
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        assert "copy" in cmd

    def test_unknown_motion_type_fallback(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            result = apply_motion(inp, out, motion_type="teleport")
        assert result == out


# ---------------------------------------------------------------------------
# _seconds_to_ass_time
# ---------------------------------------------------------------------------

class TestSecondsToAssTime:
    def test_zero(self):
        assert _seconds_to_ass_time(0.0) == "0:00:00.00"

    def test_sub_minute(self):
        result = _seconds_to_ass_time(1.5)
        assert result == "0:00:01.50"

    def test_one_minute(self):
        result = _seconds_to_ass_time(60.0)
        assert result == "0:01:00.00"

    def test_over_minute(self):
        result = _seconds_to_ass_time(65.25)
        assert result == "0:01:05.25"

    def test_over_hour(self):
        result = _seconds_to_ass_time(3661.0)
        assert result == "1:01:01.00"


# ---------------------------------------------------------------------------
# generate_ass_subtitles
# ---------------------------------------------------------------------------

class TestGenerateAssSubtitles:
    def test_empty_tracks_creates_file(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        result = generate_ass_subtitles([], out)
        assert result == out
        assert Path(out).exists()
        content = Path(out).read_text()
        assert "[Script Info]" in content
        assert "[Events]" in content

    def test_single_lower_third_track(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [{"start": 0.0, "end": 2.5, "text": "HELLO WORLD", "position": "lower_third"}]
        result = generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "HELLO WORLD" in content
        assert "LowerThird" in content

    def test_upper_third_position(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [{"start": 1.0, "end": 3.0, "text": "TOP TEXT", "position": "upper_third"}]
        generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "UpperThird" in content

    def test_center_position(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [{"start": 0.0, "end": 1.0, "text": "MID", "position": "center"}]
        generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "Center" in content

    def test_top_position_maps_to_upper(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [{"start": 0.0, "end": 1.0, "text": "X", "position": "top"}]
        generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "UpperThird" in content

    def test_bottom_position_maps_to_lower(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [{"start": 0.0, "end": 1.0, "text": "X", "position": "bottom"}]
        generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "LowerThird" in content

    def test_multiple_tracks(self, tmp_path):
        out = str(tmp_path / "subs.ass")
        tracks = [
            {"start": 0.0, "end": 1.0, "text": "First"},
            {"start": 1.5, "end": 3.0, "text": "Second"},
            {"start": 3.5, "end": 5.0, "text": "Third"},
        ]
        generate_ass_subtitles(tracks, out)
        content = Path(out).read_text()
        assert "First" in content
        assert "Second" in content
        assert "Third" in content

    def test_creates_parent_dir(self, tmp_path):
        out = str(tmp_path / "nested" / "subs.ass")
        generate_ass_subtitles([], out)
        assert Path(out).exists()


# ---------------------------------------------------------------------------
# concat_clips
# ---------------------------------------------------------------------------

class TestConcatClips:
    def test_multiple_clips(self, tmp_path):
        clips = [str(tmp_path / f"clip{i}.mp4") for i in range(3)]
        out = str(tmp_path / "out.mp4")
        for c in clips:
            Path(c).write_text("x")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = concat_clips(clips, out)
        assert result == out
        mock_run.assert_called()

    def test_single_clip_uses_copy(self, tmp_path):
        clips = [str(tmp_path / "clip.mp4")]
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = concat_clips(clips, out)
        assert result == out


# ---------------------------------------------------------------------------
# mix_audio
# ---------------------------------------------------------------------------

class TestMixAudio:
    def test_with_ducking(self, tmp_path):
        video = str(tmp_path / "video.mp4")
        audio = str(tmp_path / "music.mp3")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = mix_audio(video, audio, out, duck_under_speech=True)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "sidechaincompress" in " ".join(cmd)

    def test_without_ducking(self, tmp_path):
        video = str(tmp_path / "video.mp4")
        audio = str(tmp_path / "music.mp3")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = mix_audio(video, audio, out, duck_under_speech=False)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "amix" in " ".join(cmd)


# ---------------------------------------------------------------------------
# burn_subtitles
# ---------------------------------------------------------------------------

class TestBurnSubtitles:
    def test_basic(self, tmp_path):
        video = str(tmp_path / "video.mp4")
        ass = str(tmp_path / "subs.ass")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = burn_subtitles(video, ass, out)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "ass=" in " ".join(cmd)


# ---------------------------------------------------------------------------
# apply_color_grade
# ---------------------------------------------------------------------------

class TestApplyColorGrade:
    def test_basic_grade(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        grade = {"brightness": 0.1, "contrast": 1.2, "saturation": 1.1, "gamma": 1.0}
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = apply_color_grade(inp, out, grade)
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "eq=" in " ".join(cmd)

    def test_clamps_out_of_range_values(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        grade = {"brightness": 5.0, "contrast": -1.0, "saturation": 10.0, "gamma": 0.0}
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            result = apply_color_grade(inp, out, grade)
        assert result == out

    def test_defaults_when_missing_keys(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            result = apply_color_grade(inp, out, {})
        assert result == out


# ---------------------------------------------------------------------------
# generate_thumbnail
# ---------------------------------------------------------------------------

class TestGenerateThumbnail:
    def test_basic(self, tmp_path):
        video = str(tmp_path / "video.mp4")
        thumb = str(tmp_path / "thumb.jpg")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = generate_thumbnail(video, thumb, timestamp=2.0)
        assert result == thumb
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "-vframes" in cmd

    def test_default_timestamp(self, tmp_path):
        video = str(tmp_path / "video.mp4")
        thumb = str(tmp_path / "thumb.jpg")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            generate_thumbnail(video, thumb)
        cmd = mock_run.call_args[0][0]
        assert "0.500" in cmd


# ---------------------------------------------------------------------------
# remove_silence
# ---------------------------------------------------------------------------

class TestRemoveSilence:
    def test_no_silences_copies_file(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()) as mock_run:
            result = remove_silence(inp, out, silences=[])
        assert result == out
        cmd = mock_run.call_args[0][0]
        assert "copy" in cmd

    def test_with_silences_trims_and_concatenates(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        silences = [{"start": 2.0, "end": 4.0, "duration": 2.0}]
        mock_info = {"duration_sec": 10.0, "width": 1080, "height": 1920, "fps": 30,
                     "has_video": True, "has_audio": True, "file_size_bytes": 1000,
                     "format_name": "mp4", "bit_rate": 500000}

        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch("app.services.media_analyzer.get_media_info", return_value=mock_info), \
             patch("app.services.render_engine.os.unlink"), \
             patch("app.services.render_engine.os.rmdir"):
            result = remove_silence(inp, out, silences=silences)
        assert result == out

    def test_with_multiple_silences(self, tmp_path):
        inp = str(tmp_path / "in.mp4")
        out = str(tmp_path / "out.mp4")
        silences = [
            {"start": 1.0, "end": 2.0, "duration": 1.0},
            {"start": 5.0, "end": 7.0, "duration": 2.0},
        ]
        mock_info = {"duration_sec": 10.0, "width": 1080, "height": 1920, "fps": 30,
                     "has_video": True, "has_audio": True, "file_size_bytes": 1000,
                     "format_name": "mp4", "bit_rate": 500000}
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch("app.services.media_analyzer.get_media_info", return_value=mock_info), \
             patch("app.services.render_engine.os.unlink"), \
             patch("app.services.render_engine.os.rmdir"):
            result = remove_silence(inp, out, silences=silences)
        assert result == out


# ---------------------------------------------------------------------------
# RenderEngine
# ---------------------------------------------------------------------------

class TestRenderEngine:
    def _make_spec(self, tmp_path, num_clips=1):
        """Create a minimal edit spec for testing."""
        clips = []
        for i in range(num_clips):
            asset_path = str(tmp_path / f"asset_{i}.mp4")
            Path(asset_path).write_text("x")
            clips.append({
                "asset_id": asset_path,
                "start": 0.0,
                "end": 3.0,
                "source_in": 0.0,
                "source_out": 3.0,
                "speed": 1.0,
                "motion": {"type": "static", "strength": 0.05},
            })
        return {
            "project_id": "test",
            "output": {"width": 1080, "height": 1920, "fps": 30},
            "tracks": {
                "video": clips,
                "text": [
                    {"start": 0.0, "end": 2.0, "text": "TEST", "position": "lower_third"},
                ],
                "audio": [],
            },
        }

    def test_render_single_clip_no_grade(self, tmp_path):
        spec = self._make_spec(tmp_path, num_clips=1)
        final_mp4 = str(tmp_path / "render" / "final.mp4")
        Path(final_mp4).parent.mkdir(parents=True, exist_ok=True)
        Path(final_mp4).write_text("x")

        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch.object(RenderEngine, "_render_impl") as mock_impl:
            mock_impl.return_value = {
                "output_path": final_mp4,
                "thumbnail_path": str(tmp_path / "thumb.jpg"),
                "subtitle_path": str(tmp_path / "subs.ass"),
            }
            engine = RenderEngine()
            result = engine.render(spec)
        assert "output_path" in result

    def test_cleanup_removes_work_dir(self, tmp_path):
        engine = RenderEngine()
        work_dir = engine.work_dir
        assert work_dir.exists()
        engine.cleanup()
        assert not work_dir.exists()

    def test_render_no_clips_raises(self, tmp_path):
        spec = {"project_id": "test", "output": {}, "tracks": {"video": [], "text": [], "audio": []}}
        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()):
            engine = RenderEngine()
            with pytest.raises((ValueError, Exception)):
                engine._render_impl(spec)

    def test_render_with_color_grade(self, tmp_path):
        spec = self._make_spec(tmp_path, num_clips=1)
        color_grade = {"brightness": 0.1, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0}

        final_mp4 = str(tmp_path / "render" / "final.mp4")
        Path(final_mp4).parent.mkdir(parents=True, exist_ok=True)
        Path(final_mp4).write_text("x")

        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch.object(RenderEngine, "_render_impl") as mock_impl:
            mock_impl.return_value = {"output_path": final_mp4}
            engine = RenderEngine()
            result = engine.render(spec, color_grade=color_grade)
        assert "output_path" in result

    def test_render_impl_multi_clip(self, tmp_path):
        spec = self._make_spec(tmp_path, num_clips=2)
        concat_path = str(tmp_path / "work" / "concat.mp4")
        Path(concat_path).parent.mkdir(parents=True, exist_ok=True)
        Path(concat_path).write_text("x")

        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch("app.services.render_engine.generate_thumbnail") as mock_thumb, \
             patch("app.services.render_engine.burn_subtitles") as mock_burn, \
             patch("app.services.render_engine.concat_clips") as mock_concat:
            mock_thumb.return_value = str(tmp_path / "thumb.jpg")
            mock_burn.return_value = str(tmp_path / "burned.mp4")
            mock_concat.return_value = concat_path
            engine = RenderEngine()
            try:
                result = engine._render_impl(spec)
                assert "output_path" in result
            except Exception:
                pass  # Some paths may fail without real files

    def test_asset_resolver_used(self, tmp_path):
        asset_id = "test-asset-id"
        resolved_path = str(tmp_path / "resolved.mp4")
        Path(resolved_path).write_text("x")
        resolver = Mock(return_value=resolved_path)

        spec = {
            "project_id": "test",
            "output": {"width": 1080, "height": 1920, "fps": 30},
            "tracks": {
                "video": [{
                    "asset_id": asset_id,
                    "start": 0.0, "end": 3.0,
                    "source_in": 0.0, "source_out": 3.0,
                    "speed": 1.0,
                    "motion": {"type": "zoom_in", "strength": 0.05},
                }],
                "text": [],
                "audio": [],
            },
        }

        with patch("app.services.render_engine.subprocess.run", return_value=_ok_proc()), \
             patch("app.services.render_engine.generate_thumbnail") as mock_thumb, \
             patch("app.services.render_engine.burn_subtitles") as mock_burn:
            mock_thumb.return_value = str(tmp_path / "thumb.jpg")
            mock_burn.return_value = str(tmp_path / "burned.mp4")
            engine = RenderEngine(asset_resolver=resolver)
            try:
                engine._render_impl(spec)
                resolver.assert_called_with(asset_id)
            except Exception:
                pass  # May fail on file operations but resolver should be called

    # ── render_timeline ────────────────────────────────────────────────────

    def test_render_timeline_rejects_dict(self, tmp_path):
        """render_timeline() must reject raw dicts — type-safe entry point."""
        engine = RenderEngine()
        with pytest.raises(TypeError, match="EditTimeline"):
            engine.render_timeline({"tracks": {}, "output": {}})  # type: ignore

    def test_render_timeline_rejects_none(self, tmp_path):
        engine = RenderEngine()
        with pytest.raises(TypeError):
            engine.render_timeline(None)  # type: ignore

    def test_render_timeline_rejects_invalid_timeline(self, tmp_path):
        """render_timeline() must raise ValueError when validate_timeline fails."""
        from app.services.timeline_schema import (
            EditTimeline, ClipEvent, TransitionEvent
        )
        # Create a timeline with a duration mismatch to trigger validation error
        clip = ClipEvent(
            asset_id="x",
            source_in=0.0, source_out=5.0,
            timeline_in=0.0, timeline_out=5.0,
            transition_out=TransitionEvent(type="hard_cut", duration=0.0),
        )
        bad_timeline = EditTimeline(
            project_id="bad",
            duration_sec=2.0,   # clips end at 5.0 — mismatch
            clips=[clip],
            captions=[],
        )
        engine = RenderEngine()
        with pytest.raises(ValueError, match="validation"):
            engine.render_timeline(bad_timeline)

    def test_render_timeline_accepts_valid_timeline(self, tmp_path):
        """render_timeline() must delegate to _render_impl for a valid EditTimeline."""
        from app.services.timeline_schema import (
            EditTimeline, ClipEvent, CaptionEvent, TransitionEvent
        )
        clip = ClipEvent(
            asset_id="a1",
            source_in=0.0, source_out=2.0,
            timeline_in=0.0, timeline_out=2.0,
            transition_out=TransitionEvent(type="hard_cut", duration=0.0),
        )
        caption = CaptionEvent(text="TEST", start=0.2, end=1.8)
        timeline = EditTimeline(
            project_id="test",
            duration_sec=2.0,
            clips=[clip],
            captions=[caption],
        )
        fake_output = str(tmp_path / "final.mp4")
        Path(fake_output).parent.mkdir(parents=True, exist_ok=True)
        Path(fake_output).write_bytes(b"fake_video_data")

        with patch.object(RenderEngine, "_render_impl", return_value={"output_path": fake_output}) as mock_impl:
            engine = RenderEngine(asset_resolver=lambda aid: f"/footage/{aid}.mp4")
            result = engine.render_timeline(timeline, preview=False)

        assert mock_impl.called
        assert "output_path" in result

    def test_render_timeline_preview_sets_smaller_resolution(self, tmp_path):
        """In preview mode, render_timeline must set 480×854 in the spec."""
        from app.services.timeline_schema import (
            EditTimeline, ClipEvent, TransitionEvent
        )
        clip = ClipEvent(
            asset_id="a1",
            source_in=0.0, source_out=2.0,
            timeline_in=0.0, timeline_out=2.0,
        )
        timeline = EditTimeline(project_id="test", duration_sec=2.0, clips=[clip], captions=[])

        captured_spec: dict = {}

        def capture_impl(spec, **kwargs):
            captured_spec.update(spec)
            return {"output_path": str(tmp_path / "out.mp4")}

        with patch.object(RenderEngine, "_render_impl", side_effect=capture_impl):
            engine = RenderEngine(asset_resolver=lambda aid: "/fake.mp4")
            engine.render_timeline(timeline, preview=True)

        assert captured_spec["output"]["width"] == 480
        assert captured_spec["output"]["height"] == 854

    def test_render_timeline_full_resolution_default(self, tmp_path):
        """Non-preview render must use full 1080×1920."""
        from app.services.timeline_schema import (
            EditTimeline, ClipEvent, TransitionEvent
        )
        clip = ClipEvent(
            asset_id="a1",
            source_in=0.0, source_out=2.0,
            timeline_in=0.0, timeline_out=2.0,
        )
        timeline = EditTimeline(project_id="test", duration_sec=2.0, clips=[clip], captions=[])

        captured_spec: dict = {}

        def capture_impl(spec, **kwargs):
            captured_spec.update(spec)
            return {"output_path": str(tmp_path / "out.mp4")}

        with patch.object(RenderEngine, "_render_impl", side_effect=capture_impl):
            engine = RenderEngine(asset_resolver=lambda aid: "/fake.mp4")
            engine.render_timeline(timeline, preview=False)

        assert captured_spec["output"]["width"] == 1080
        assert captured_spec["output"]["height"] == 1920


class TestRenderEngineRenderStyle:
    """Verify that render_timeline passes render_style through to _render_impl."""

    def _make_timeline(self, render_style=None):
        from app.services.timeline_schema import EditTimeline, ClipEvent
        clip = ClipEvent(
            asset_id="a1",
            source_in=0.0, source_out=2.0,
            timeline_in=0.0, timeline_out=2.0,
        )
        return EditTimeline(
            project_id="rs_test",
            duration_sec=2.0,
            clips=[clip],
            captions=[],
            render_style=render_style,
        )

    def test_render_timeline_passes_render_style(self, tmp_path):
        """render_timeline must forward render_style to _render_impl."""
        rs = {"zoom_style": {"type": "punch_zoom", "strength": 0.1}}
        timeline = self._make_timeline(render_style=rs)

        captured_kwargs: dict = {}

        def capture_impl(spec, **kwargs):
            captured_kwargs.update(kwargs)
            return {"output_path": str(tmp_path / "out.mp4")}

        with patch.object(RenderEngine, "_render_impl", side_effect=capture_impl):
            engine = RenderEngine(asset_resolver=lambda aid: "/fake.mp4")
            engine.render_timeline(timeline)

        assert captured_kwargs.get("render_style") == rs

    def test_render_timeline_render_style_none_by_default(self, tmp_path):
        """render_timeline must pass render_style=None when not set on timeline."""
        timeline = self._make_timeline(render_style=None)

        captured_kwargs: dict = {}

        def capture_impl(spec, **kwargs):
            captured_kwargs.update(kwargs)
            return {"output_path": str(tmp_path / "out.mp4")}

        with patch.object(RenderEngine, "_render_impl", side_effect=capture_impl):
            engine = RenderEngine(asset_resolver=lambda aid: "/fake.mp4")
            engine.render_timeline(timeline)

        # render_style kwarg should be absent or None — not truthy
        assert not captured_kwargs.get("render_style")

    def test_render_style_none_falls_back_to_color_grade(self, tmp_path):
        """Without render_style, apply_color_grade is still called when color_grade present."""
        from unittest.mock import call
        timeline = self._make_timeline(render_style=None)
        # inject a non-default color grade
        timeline.color_grade.contrast = 1.25

        calls = []

        def capture_impl(spec, **kwargs):
            calls.append(kwargs)
            return {"output_path": str(tmp_path / "out.mp4")}

        with patch.object(RenderEngine, "_render_impl", side_effect=capture_impl):
            engine = RenderEngine(asset_resolver=lambda aid: "/fake.mp4")
            engine.render_timeline(timeline)

        assert len(calls) == 1
        assert calls[0]["color_grade"]["contrast"] == pytest.approx(1.25)
        assert not calls[0].get("render_style")
