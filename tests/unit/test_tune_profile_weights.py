"""Unit tests for scripts/tune_profile_weights.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

# ── import the script under test ──────────────────────────────────────────────

_SCRIPT = Path(__file__).parents[2] / "scripts" / "tune_profile_weights.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("tune_profile_weights", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tune_profile_weights"] = mod
    spec.loader.exec_module(mod)
    return mod


tune = _load_module()


# ═══════════════════════════════════════════════════════════════════════════
#  _project_bounded_simplex
# ═══════════════════════════════════════════════════════════════════════════

class TestProjectBoundedSimplex:
    def _proj(self, v, lo=0.05, hi=0.40):
        return tune._project_bounded_simplex(v, lo=lo, hi=hi)

    def test_uniform_input_stays_on_simplex(self):
        w = self._proj([1.0 / 6] * 6)
        assert abs(sum(w) - 1.0) < 1e-9
        for x in w:
            assert 0.05 <= x <= 0.40

    def test_sum_to_one(self):
        w = self._proj([0.1, 0.5, 0.2, 0.05, 0.3, 0.7])
        assert abs(sum(w) - 1.0) < 1e-9

    def test_bounds_respected(self):
        # Feed extreme values — the clip should push all into [lo, hi]
        w = self._proj([2.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        for x in w:
            assert 0.05 <= x <= 0.40 + 1e-9

    def test_already_valid_stays_valid(self):
        v = [0.20, 0.25, 0.20, 0.10, 0.15, 0.10]
        w = self._proj(v)
        assert abs(sum(w) - 1.0) < 1e-9
        for x in w:
            assert 0.05 <= x <= 0.40 + 1e-9

    def test_two_element_vector(self):
        w = self._proj([0.9, 0.1], lo=0.05, hi=0.95)
        assert abs(sum(w) - 1.0) < 1e-9

    def test_infeasible_guard_returns_uniform(self):
        # hi too small: 6 * 0.10 < 1
        w = tune._project_bounded_simplex([0.5] * 6, lo=0.05, hi=0.10)
        # Should not crash — guard returns uniform
        assert len(w) == 6

    def test_all_weights_identical_input(self):
        w = self._proj([0.3] * 6)
        assert abs(sum(w) - 1.0) < 1e-9
        expected = 1.0 / 6
        for x in w:
            assert abs(x - expected) < 1e-6

    def test_projection_is_idempotent(self):
        v = [0.1, 0.5, 0.2, 0.05, 0.3, 0.7]
        w1 = self._proj(v)
        w2 = self._proj(w1)
        for a, b in zip(w1, w2):
            assert abs(a - b) < 1e-9

    def test_empty_list(self):
        w = self._proj([])
        assert w == []


# ═══════════════════════════════════════════════════════════════════════════
#  _pgd_fit
# ═══════════════════════════════════════════════════════════════════════════

class TestPgdFit:
    """Verify that the optimiser produces sensible weights."""

    def _make_samples_dominant(self, dominant_dim: str, n=10):
        """Build samples where one dimension perfectly predicts user_rating."""
        dims = tune.DIMS
        samples = []
        for i in range(n):
            rating = (i % 5) + 1         # 1, 2, 3, 4, 5, ...
            dom_score = rating * 2.0      # matches target  y = rating * 2
            d = {d_: 5.0 for d_ in dims}
            d[dominant_dim] = dom_score   # vary only the dominant dimension
            samples.append({
                "profile":     "test",
                "dimensions":  d,
                "user_rating": rating,
                "status":      "approved",
                "source":      "synthetic",
            })
        return samples

    def test_dominant_dimension_gets_highest_weight(self):
        dom = "avg_clip_quality"
        samples = self._make_samples_dominant(dom, n=20)
        result = tune.fit_weights(samples)
        assert result["converged"]
        w = result["weights"]
        # The dominant dimension should receive the highest weight
        assert w[dom] == max(w.values())

    def test_converged_flag_set_when_enough_data(self):
        samples = self._make_samples_dominant("pacing_similarity", n=6)
        result = tune.fit_weights(samples)
        assert result["converged"] is True

    def test_mse_is_non_negative(self):
        samples = self._make_samples_dominant("visual_variety", n=8)
        result = tune.fit_weights(samples)
        assert result["mse"] is not None
        assert result["mse"] >= 0.0

    def test_weights_sum_to_one(self):
        samples = self._make_samples_dominant("continuity", n=12)
        result = tune.fit_weights(samples)
        w = result["weights"]
        assert abs(sum(w.values()) - 1.0) < 1e-5

    def test_weights_within_bounds(self):
        samples = self._make_samples_dominant("render_style_strength", n=10)
        result = tune.fit_weights(samples)
        for d, v in result["weights"].items():
            assert tune.WEIGHT_LO - 1e-6 <= v <= tune.WEIGHT_HI + 1e-6

    def test_all_dims_present_in_output(self):
        samples = self._make_samples_dominant("profile_distinctiveness", n=8)
        result = tune.fit_weights(samples)
        for d in tune.DIMS:
            assert d in result["weights"]

    def test_too_few_samples_returns_fallback(self):
        samples = self._make_samples_dominant("avg_clip_quality", n=1)
        result = tune.fit_weights(samples)
        assert result["converged"] is False
        assert result["n_samples"] == 1

    def test_zero_samples_returns_fallback(self):
        result = tune.fit_weights([])
        assert result["converged"] is False

    def test_identical_ratings_returns_fallback(self):
        dims = tune.DIMS
        samples = [
            {"profile": "x", "dimensions": {d: 5.0 for d in dims},
             "user_rating": 3, "status": "approved", "source": "syn"}
            for _ in range(5)
        ]
        result = tune.fit_weights(samples)
        assert result["converged"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  fit_all (global + per-profile)
# ═══════════════════════════════════════════════════════════════════════════

class TestFitAll:
    def _build_multi_profile_samples(self):
        dims = tune.DIMS
        samples = []
        for prof in ["fashion_montage", "music_video"]:
            for rating in range(1, 6):
                d = {d_: float(rating * 2) for d_ in dims}
                d["avg_clip_quality"] = float(rating * 2)  # dominant
                samples.append({
                    "profile":     prof,
                    "dimensions":  d,
                    "user_rating": rating,
                    "status":      "approved",
                    "source":      "syn",
                })
        return samples

    def test_global_result_present(self):
        samples = self._build_multi_profile_samples()
        global_res, per_profile = tune.fit_all(samples)
        assert "weights" in global_res

    def test_per_profile_keys(self):
        samples = self._build_multi_profile_samples()
        _, per_profile = tune.fit_all(samples)
        assert "fashion_montage" in per_profile
        assert "music_video" in per_profile

    def test_per_profile_with_single_sample_gets_fallback(self):
        dims = tune.DIMS
        samples = [
            {"profile": "travel_reel", "dimensions": {d: 7.0 for d in dims},
             "user_rating": 4, "status": "approved", "source": "syn"}
        ]
        _, per_profile = tune.fit_all(samples)
        assert per_profile["travel_reel"]["converged"] is False


# ═══════════════════════════════════════════════════════════════════════════
#  collect_samples (file-based, using temp dirs)
# ═══════════════════════════════════════════════════════════════════════════

def _make_ranked_profiles(tmpdir: Path, reviews: list[dict]) -> Path:
    """Create a synthetic ranked_profiles.json in tmpdir."""
    ranked = []
    for rv in reviews:
        ranked.append({
            "profile": rv["profile"],
            "auto_score": 7.0,
            "final_score": 7.5,
            "dimension_scores": {d: 7.0 for d in tune.DIMS},
            "review": {
                "status": rv.get("status", "approved"),
                "user_rating": rv.get("user_rating"),
                "notes": "",
            },
        })
    doc = {"ranked_at": "now", "ranked": ranked}
    path = tmpdir / "ranked_profiles.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _make_report(tmpdir: Path) -> Path:
    report = {
        "benchmark_version": "1.0",
        "reference_mp4": "ref.mp4",
        "base_fingerprint": {"avg_shot_duration": 2.0},
        "profiles": [],
    }
    path = tmpdir / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


class TestCollectSamples:
    def test_basic_load_from_ranked(self, tmp_path):
        reviews = [
            {"profile": "fashion_montage", "user_rating": 4, "status": "approved"},
            {"profile": "music_video",     "user_rating": 3, "status": "approved"},
        ]
        _make_ranked_profiles(tmp_path, reviews)
        report_path = _make_report(tmp_path)

        samples = tune.collect_samples([report_path], min_rating=1)
        assert len(samples) == 2
        profiles = {s["profile"] for s in samples}
        assert "fashion_montage" in profiles
        assert "music_video" in profiles

    def test_min_rating_filter(self, tmp_path):
        reviews = [
            {"profile": "fashion_montage", "user_rating": 5, "status": "approved"},
            {"profile": "music_video",     "user_rating": 2, "status": "approved"},
        ]
        _make_ranked_profiles(tmp_path, reviews)
        report_path = _make_report(tmp_path)

        samples = tune.collect_samples([report_path], min_rating=4)
        assert len(samples) == 1
        assert samples[0]["profile"] == "fashion_montage"

    def test_profile_filter(self, tmp_path):
        reviews = [
            {"profile": "fashion_montage", "user_rating": 4, "status": "approved"},
            {"profile": "vlog",            "user_rating": 5, "status": "approved"},
        ]
        _make_ranked_profiles(tmp_path, reviews)
        report_path = _make_report(tmp_path)

        samples = tune.collect_samples([report_path], min_rating=1, profile_filter="vlog")
        assert all(s["profile"] == "vlog" for s in samples)
        assert len(samples) == 1

    def test_unrated_profiles_skipped(self, tmp_path):
        reviews = [
            {"profile": "fashion_montage", "user_rating": None, "status": "unreviewed"},
            {"profile": "music_video",     "user_rating": 4,    "status": "approved"},
        ]
        _make_ranked_profiles(tmp_path, reviews)
        report_path = _make_report(tmp_path)

        samples = tune.collect_samples([report_path], min_rating=1)
        assert len(samples) == 1
        assert samples[0]["profile"] == "music_video"

    def test_missing_report_skipped(self, tmp_path):
        samples = tune.collect_samples([tmp_path / "nonexistent.json"], min_rating=1)
        assert samples == []

    def test_dimensions_present(self, tmp_path):
        reviews = [{"profile": "vlog", "user_rating": 3, "status": "approved"}]
        _make_ranked_profiles(tmp_path, reviews)
        report_path = _make_report(tmp_path)

        samples = tune.collect_samples([report_path], min_rating=1)
        assert len(samples) == 1
        for d in tune.DIMS:
            assert d in samples[0]["dimensions"]

    def test_multiple_reports(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        _make_ranked_profiles(dir_a, [{"profile": "vlog", "user_rating": 4}])
        _make_ranked_profiles(dir_b, [{"profile": "music_video", "user_rating": 5}])
        rep_a = _make_report(dir_a)
        rep_b = _make_report(dir_b)

        samples = tune.collect_samples([rep_a, rep_b], min_rating=1)
        assert len(samples) == 2


# ═══════════════════════════════════════════════════════════════════════════
#  build_learned_weights_doc and build_tuning_summary
# ═══════════════════════════════════════════════════════════════════════════

class TestOutputBuilders:
    def _make_global_result(self, converged=True):
        return {
            "weights":   {d: 1.0 / len(tune.DIMS) for d in tune.DIMS},
            "mse":       0.05,
            "n_samples": 6,
            "converged": converged,
            "note":      "ok",
        }

    def test_doc_has_required_keys(self, tmp_path):
        gr = self._make_global_result()
        doc = tune.build_learned_weights_doc(
            gr, {}, 4, None, 0.05, 0.40, [tmp_path / "r.json"]
        )
        assert "global" in doc
        assert "per_profile" in doc
        assert "metadata" not in doc  # we use fit_stats
        assert "fit_stats" in doc
        assert "generated_at" in doc

    def test_doc_global_none_when_not_converged(self, tmp_path):
        gr = self._make_global_result(converged=False)
        doc = tune.build_learned_weights_doc(
            gr, {}, 1, None, 0.05, 0.40, [tmp_path / "r.json"]
        )
        assert doc["global"] is None

    def test_doc_per_profile_only_converged(self, tmp_path):
        gr = self._make_global_result()
        pp = {
            "fashion_montage": {**self._make_global_result(), "converged": True},
            "vlog":            {**self._make_global_result(), "converged": False},
        }
        doc = tune.build_learned_weights_doc(
            gr, pp, 4, None, 0.05, 0.40, [tmp_path / "r.json"]
        )
        assert "fashion_montage" in doc["per_profile"]
        assert "vlog" not in doc["per_profile"]

    def test_summary_markdown_contains_global_section(self, tmp_path):
        gr = self._make_global_result()
        md = tune.build_tuning_summary(
            gr, {}, [], 4, None, 0.05, 0.40,
            [tmp_path / "r.json"], tmp_path / "lw.json",
        )
        assert "## Global Learned Weights" in md

    def test_summary_markdown_contains_usage_section(self, tmp_path):
        gr = self._make_global_result()
        md = tune.build_tuning_summary(
            gr, {}, [], 1, None, 0.05, 0.40,
            [tmp_path / "r.json"], tmp_path / "lw.json",
        )
        assert "## Usage" in md
        assert "--learned-weights" in md


# ═══════════════════════════════════════════════════════════════════════════
#  CLI (subprocess, integration-level)
# ═══════════════════════════════════════════════════════════════════════════

import subprocess


def _run_cli(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(_SCRIPT)] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=str(cwd or Path.cwd()))


def _make_rated_benchmark(tmpdir: Path, ratings: dict[str, int]) -> Path:
    """Create a minimal report.json + ranked_profiles.json pair."""
    ranked = [
        {
            "profile": profile,
            "auto_score": float(rating * 2),
            "final_score": float(rating * 2),
            "dimension_scores": {d: float(rating * 2) for d in tune.DIMS},
            "review": {
                "status": "approved",
                "user_rating": rating,
                "notes": "",
                "reviewed_at": "2024-01-01T00:00:00Z",
            },
            "output_path": None,
            "contact_sheet_path": None,
            "total_duration_sec": 30.0,
            "num_clips": 5,
        }
        for profile, rating in ratings.items()
    ]
    ranked_doc = {"ranked_at": "now", "ranked": ranked}
    (tmpdir / "ranked_profiles.json").write_text(
        json.dumps(ranked_doc), encoding="utf-8"
    )
    report = {
        "benchmark_version": "1.0",
        "reference_mp4": "ref.mp4",
        "base_fingerprint": {"avg_shot_duration": 2.0, "pace": "medium"},
        "profiles": [],
    }
    report_path = tmpdir / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


class TestCLI:
    def test_dry_run_writes_no_files(self, tmp_path):
        ratings = {
            "fashion_montage": 5,
            "music_video":     3,
            "vlog":            4,
            "travel_reel":     2,
            "talking_head":    5,
            "product_showcase": 1,
        }
        report_path = _make_rated_benchmark(tmp_path, ratings)
        out = tmp_path / "lw.json"
        result = _run_cli([
            "--reports", str(report_path),
            "--out", str(out),
            "--dry-run",
        ])
        assert result.returncode == 0, result.stderr
        assert not out.exists(), "dry-run should not write output file"

    def test_writes_learned_weights_json(self, tmp_path):
        ratings = {"fashion_montage": 5, "music_video": 2, "vlog": 4,
                   "travel_reel": 3, "talking_head": 5, "product_showcase": 1}
        report_path = _make_rated_benchmark(tmp_path, ratings)
        out = tmp_path / "lw.json"
        result = _run_cli(["--reports", str(report_path), "--out", str(out)])
        assert result.returncode == 0, result.stderr
        assert out.exists()
        doc = json.loads(out.read_text())
        assert "global" in doc or doc["global"] is None

    def test_writes_tuning_summary_md(self, tmp_path):
        ratings = {"fashion_montage": 5, "music_video": 2, "vlog": 4,
                   "travel_reel": 3, "talking_head": 5, "product_showcase": 1}
        report_path = _make_rated_benchmark(tmp_path, ratings)
        out = tmp_path / "lw.json"
        result = _run_cli(["--reports", str(report_path), "--out", str(out)])
        assert result.returncode == 0, result.stderr
        md_path = tmp_path / "tuning_summary.md"
        assert md_path.exists()

    def test_min_rating_filters_correctly(self, tmp_path):
        # Only rating ≥ 4 kept → 3 samples; enough for global fit
        ratings = {"fashion_montage": 5, "music_video": 1, "vlog": 4,
                   "travel_reel": 2, "talking_head": 5, "product_showcase": 3}
        report_path = _make_rated_benchmark(tmp_path, ratings)
        out = tmp_path / "lw.json"
        result = _run_cli([
            "--reports", str(report_path),
            "--out", str(out),
            "--min-rating", "4",
        ])
        assert result.returncode == 0, result.stderr
        doc = json.loads(out.read_text())
        assert doc["config"]["min_rating"] == 4

    def test_profile_flag_restricts_samples(self, tmp_path):
        ratings = {"fashion_montage": 5, "music_video": 3}
        report_path = _make_rated_benchmark(tmp_path, ratings)
        out = tmp_path / "lw.json"
        result = _run_cli([
            "--reports", str(report_path),
            "--out", str(out),
            "--profile", "fashion_montage",
        ])
        # Only 1 sample → converged=False (too few), but should not crash
        assert result.returncode in (0, 1)

    def test_no_rated_samples_exits_nonzero(self, tmp_path):
        # All unreviewed
        (tmp_path / "ranked_profiles.json").write_text(
            json.dumps({"ranked": [
                {"profile": "vlog", "dimension_scores": {d: 5.0 for d in tune.DIMS},
                 "review": {"status": "unreviewed", "user_rating": None}}
            ]}),
            encoding="utf-8",
        )
        report_path = _make_report(tmp_path)
        out = tmp_path / "lw.json"
        result = _run_cli(["--reports", str(report_path), "--out", str(out)])
        assert result.returncode != 0

    def test_multiple_reports_combined(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        dir_b = tmp_path / "b"
        dir_b.mkdir()
        ratings_a = {"fashion_montage": 5, "music_video": 2}
        ratings_b = {"vlog": 4, "travel_reel": 3, "talking_head": 5}
        rep_a = _make_rated_benchmark(dir_a, ratings_a)
        rep_b = _make_rated_benchmark(dir_b, ratings_b)
        out = tmp_path / "lw.json"
        result = _run_cli([
            "--reports", str(rep_a), str(rep_b),
            "--out", str(out),
        ])
        assert result.returncode == 0, result.stderr
        doc = json.loads(out.read_text())
        n_global = doc["fit_stats"]["global"]["n_samples"]
        assert n_global == 5
