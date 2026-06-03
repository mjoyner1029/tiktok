"""Optional CLIP-based visual embedding support.

When ``open_clip`` or the original ``clip`` library is installed this module
computes real ViT-B/32 visual embeddings for reference frames and footage
segment key-frames, enabling true visual-similarity scoring in ClipRanker.

When neither backend is available every public helper returns *None* / 0.5,
so callers can fall back to the proxy-score path without any branching.

Supported backends (tried in order)
------------------------------------
1. ``open_clip``   (pip install open-clip-torch)
2. ``clip``        (pip install git+https://github.com/openai/CLIP.git)

Cache layout
------------
``{storage_root}/projects/{project_id}/embeddings/{hex_key}.json``

Each file is a JSON-encoded ``list[float]`` (512 dimensions, ViT-B/32).
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Optional backend detection ────────────────────────────────────────────────

try:
    import torch as _torch
    import open_clip as _open_clip_mod  # type: ignore[import]
    _CLIP_BACKEND: str | None = "open_clip"
except ImportError:
    try:
        import torch as _torch  # type: ignore[assignment]
        import clip as _clip_mod  # type: ignore[import,assignment]
        _CLIP_BACKEND = "clip"
    except ImportError:
        _torch = None            # type: ignore[assignment]
        _open_clip_mod = None    # type: ignore[assignment]
        _clip_mod = None         # type: ignore[assignment,name-defined]
        _CLIP_BACKEND = None

#: True when a CLIP backend is importable at import time.
CLIP_AVAILABLE: bool = _CLIP_BACKEND is not None

#: Output dimension for ViT-B/32.
EMBEDDING_DIM: int = 512

# ── Lazy model state ──────────────────────────────────────────────────────────

_model: Any = None
_preprocess: Any = None
_device: str = "cpu"


# ── Model loading (idempotent) ────────────────────────────────────────────────

def _load_model() -> None:
    """Load CLIP model into module-level globals; safe to call repeatedly."""
    global _model, _preprocess, _device
    if _model is not None:
        return
    if not CLIP_AVAILABLE:
        raise RuntimeError("No CLIP backend available (install open-clip-torch or clip)")

    if _CLIP_BACKEND == "open_clip":
        # Try the richer laion pretrained weights first; fall back to openai
        try:
            _model, _, _preprocess = _open_clip_mod.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
        except Exception:
            _model, _, _preprocess = _open_clip_mod.create_model_and_transforms(
                "ViT-B-32", pretrained="openai"
            )
        _model.eval()
    else:  # "clip" (OpenAI original)
        _model, _preprocess = _clip_mod.load("ViT-B/32", device=_device)

    logger.info("CLIP model loaded (backend=%s)", _CLIP_BACKEND)


# ── Core embedding ────────────────────────────────────────────────────────────

def embed_image_path(image_path: str) -> list[float]:
    """Return a normalized 512-dim ViT-B/32 embedding for one image file.

    The returned vector is L2-normalized (unit norm) so that cosine similarity
    reduces to a plain dot product.

    Raises:
        RuntimeError: When no CLIP backend is available.
        FileNotFoundError: When the image file does not exist.
    """
    _load_model()
    from PIL import Image as _PIL  # Pillow is always in requirements.txt

    img = _PIL.open(image_path).convert("RGB")
    tensor = _preprocess(img).unsqueeze(0)
    if _device != "cpu":
        tensor = tensor.to(_device)
    with _torch.no_grad():
        features = _model.encode_image(tensor)
        features = features / features.norm(dim=-1, keepdim=True)
    return features.squeeze(0).cpu().tolist()


# ── Pure-Python math (always available, no optional deps) ────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two pre-normalised CLIP embedding vectors.

    CLIP vectors are unit-norm, so cosine similarity equals the dot product.
    The result is shifted from ``[-1, 1]`` to ``[0, 1]`` for convenient use as a
    scoring dimension.  Returns 0.5 (neutral) for empty or mismatched inputs.
    """
    if not a or not b or len(a) != len(b):
        return 0.5
    dot = sum(x * y for x, y in zip(a, b))
    return (max(-1.0, min(1.0, dot)) + 1.0) / 2.0


def mean_embedding(embeddings: list[list[float]]) -> list[float] | None:
    """Return the L2-normalised mean of a list of unit-norm embeddings.

    Returns ``None`` when the list is empty.
    """
    if not embeddings:
        return None
    dim = len(embeddings[0])
    n = len(embeddings)
    mean = [sum(e[i] for e in embeddings) / n for i in range(dim)]
    norm = sum(x * x for x in mean) ** 0.5
    return [x / norm for x in mean] if norm > 1e-9 else mean


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _cache_dir(storage_root: str | Path, project_id: str) -> Path:
    return Path(storage_root) / "projects" / project_id / "embeddings"


def _segment_key(asset_id: str, start: float, end: float) -> str:
    """16-hex-char stable cache key for one footage segment."""
    raw = f"{asset_id}\x00{start:.4f}\x00{end:.4f}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _reference_key(video_path: str, n_frames: int) -> str:
    """Cache key for a reference video + frame count."""
    raw = f"{video_path}\x00{n_frames}"
    return "ref_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _load_cached(path: Path) -> list[float] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_cache(path: Path, embedding: list[float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(embedding), encoding="utf-8")


# ── FFmpeg frame extractor ────────────────────────────────────────────────────

def _ffmpeg_bin() -> str:
    try:
        from app.config import get_settings
        return get_settings().ffmpeg_binary
    except Exception:
        return "ffmpeg"


def _extract_frame(video_path: str, timestamp: float) -> str | None:
    """Write a single JPEG frame at *timestamp* seconds to a temp file.

    Returns the file path on success, or *None* if ffmpeg fails.
    The caller is responsible for deleting the file.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    out = tmp.name
    tmp.close()
    cmd = [
        _ffmpeg_bin(), "-y",
        "-ss", f"{timestamp:.3f}", "-i", video_path,
        "-vframes", "1", "-vf", "scale=224:-2", "-q:v", "3", out,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=30)
    p = Path(out)
    if r.returncode == 0 and p.exists() and p.stat().st_size > 0:
        return out
    p.unlink(missing_ok=True)
    return None


# ── EmbeddingService ──────────────────────────────────────────────────────────

class EmbeddingService:
    """Compute and cache CLIP embeddings for reference frames and footage segments.

    All public methods silently return *None* when CLIP is unavailable, so
    callers require no conditional logic around ``CLIP_AVAILABLE``.

    Example
    -------
    ::

        svc = EmbeddingService(project_id="abc123", storage_root="./storage")

        # Compute reference embedding and inject into fingerprint
        ref_emb = svc.embed_reference_video("reference.mp4")
        fingerprint["_ref_embedding"] = ref_emb   # None-safe

        # Enrich footage index with per-segment embeddings
        svc.enrich_footage_index(footage_index)

        # EditPlanner / ClipRanker picks up _ref_embedding and segment _embedding
        timeline = planner.plan(fingerprint, footage_index)
    """

    def __init__(
        self,
        project_id: str,
        storage_root: str | Path = "./storage",
    ) -> None:
        self.project_id = project_id
        self._dir = _cache_dir(storage_root, project_id)

    @property
    def available(self) -> bool:
        """True when a CLIP backend is importable."""
        return CLIP_AVAILABLE

    # ── Reference video ───────────────────────────────────────────────────

    def embed_frames(
        self,
        frame_paths: list[str],
        cache_key: str = "reference",
    ) -> list[float] | None:
        """Return mean CLIP embedding for a list of already-extracted image files.

        Results are cached.  Useful when frame files are already on disk (e.g.
        produced by :func:`~app.services.reference_analyzer._extract_frames`).
        Returns *None* when CLIP is unavailable or no frames can be embedded.
        """
        if not CLIP_AVAILABLE:
            return None
        cached_path = self._dir / f"{cache_key}.json"
        cached = _load_cached(cached_path)
        if cached is not None:
            return cached

        embeddings: list[list[float]] = []
        for fp in frame_paths:
            if not Path(fp).exists():
                continue
            try:
                embeddings.append(embed_image_path(fp))
            except Exception as exc:
                logger.debug("Frame embed failed %s: %s", fp, exc)

        result = mean_embedding(embeddings)
        if result is not None:
            _write_cache(cached_path, result)
        return result

    def embed_reference_video(
        self,
        video_path: str,
        n_frames: int = 8,
    ) -> list[float] | None:
        """Extract *n_frames* evenly-spaced frames from *video_path* and embed them.

        The mean embedding is cached keyed on video path + frame count.
        Returns *None* when CLIP is unavailable or the video cannot be read.
        """
        if not CLIP_AVAILABLE:
            return None

        cache_key = _reference_key(video_path, n_frames)
        cached_path = self._dir / f"{cache_key}.json"
        cached = _load_cached(cached_path)
        if cached is not None:
            return cached

        try:
            from app.services.media_analyzer import get_media_info
            dur = get_media_info(video_path).get("duration_sec", 0.0)
        except Exception:
            dur = 0.0

        if dur <= 0:
            return None

        timestamps = [dur * (i + 0.5) / n_frames for i in range(n_frames)]
        frame_paths: list[str] = []
        try:
            for ts in timestamps:
                fp = _extract_frame(video_path, ts)
                if fp:
                    frame_paths.append(fp)
            return self.embed_frames(frame_paths, cache_key=cache_key)
        finally:
            for fp in frame_paths:
                Path(fp).unlink(missing_ok=True)

    # ── Footage segments ──────────────────────────────────────────────────

    def embed_segment(
        self,
        asset_id: str,
        video_path: str,
        start: float,
        end: float,
    ) -> list[float] | None:
        """Embed the midpoint keyframe of a footage segment (cached).

        Returns *None* when CLIP is unavailable, the video file is missing,
        or the frame extraction fails.
        """
        if not CLIP_AVAILABLE:
            return None
        if not Path(video_path).exists():
            return None

        key = _segment_key(asset_id, start, end)
        cached_path = self._dir / f"{key}.json"
        cached = _load_cached(cached_path)
        if cached is not None:
            return cached

        mid = (start + end) / 2.0
        frame_path = _extract_frame(video_path, mid)
        if frame_path is None:
            return None
        try:
            emb = embed_image_path(frame_path)
            _write_cache(cached_path, emb)
            return emb
        except Exception as exc:
            logger.debug("Segment embed failed %s@%.3fs: %s", asset_id, mid, exc)
            return None
        finally:
            Path(frame_path).unlink(missing_ok=True)

    # ── Bulk enrichment ───────────────────────────────────────────────────

    def enrich_footage_index(
        self,
        footage_index: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Inject ``_embedding`` into every usable segment dict (in-place).

        Segments whose embedding cannot be computed are left unchanged.
        This is a no-op when CLIP is unavailable.
        """
        if not CLIP_AVAILABLE:
            return footage_index

        for clip in footage_index:
            video_path = clip.get("file_path") or clip.get("path", "")
            asset_id = clip.get("asset_id", "")
            for seg in clip.get("moments") or clip.get("usable_segments") or []:
                emb = self.embed_segment(
                    asset_id,
                    video_path,
                    float(seg.get("start", 0.0)),
                    float(seg.get("end", 0.0)),
                )
                if emb is not None:
                    seg["_embedding"] = emb
        return footage_index
