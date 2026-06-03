/**
 * API client for the TikTok Style Engine backend.
 */
import axios from "axios";

const API_BASE = import.meta.env.VITE_API_URL || "";

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  headers: { "Content-Type": "application/json" },
});

// ── Types ────────────────────────────────────────────────────────────────

export type AudioMode = "uploaded_audio" | "reference_audio" | "original_audio" | "silent";
export type RhythmPreset = "tight_sync" | "loose_sync" | "cinematic" | "chaotic";

export interface PipelineStartOptions {
  /** TikTok / video URLs to use as style reference */
  referenceUrls: string[];
  /** Optional uploaded reference video files */
  referenceFiles: File[];
  /** Raw footage clips to edit */
  footageFiles: File[];
  /** Optional music/audio file for uploaded_audio mode */
  musicFile: File | null;
  audioMode: AudioMode;
  /** Music track level in dBFS, e.g. -18.0 */
  audioVolume: number;
  /** Original clip audio level 0–1 */
  originalAudioVolume: number;
  rhythmPreset: RhythmPreset;
  contentHint: string;
  preview?: boolean;
  // ── Batch / large-footage options ──────────────────────────────────────
  /** Target output duration in seconds (15 | 30 | 45 | 60) */
  targetDurationSec?: number;
  /** Max number of segments fed to the planner (advanced) */
  maxSelectedSegments?: number;
  /** Minimum distinct source clips in the final edit */
  minClipVariety?: number;
}

export interface BatchRejection {
  asset_id: string;
  start: number | null;
  score: number | null;
  reason: string;
}

export interface BatchReport {
  total_clips: number;
  total_segments_before_dedup: number;
  total_segments_after_cap: number;
  total_segments_after_dedup: number;
  total_selected: number;
  rejection_log: BatchRejection[];
}

export interface PipelineClipResult {
  asset_id: string;
  start: number;
  end: number;
  duration: number;
  score: number;
  score_breakdown: Record<string, number>;
  description?: string;
}

export interface PipelineResult {
  project_id: string;
  /** Relative URL to stream the video — fetch from /api/v1/pipeline/video/... */
  video_url: string;
  audio_mode: AudioMode;
  rhythm_preset: RhythmPreset;
  bpm?: number | null;
  beat_count?: number | null;
  timeline_duration: number;
  clip_count: number;
  render_style?: string | null;
  ranking_profile?: string | null;
  escalation_score?: number | null;
  pacing_curve?: number[];
  top_clips?: PipelineClipResult[];
  embedding_status?: {
    clip_available: boolean;
    reference_embedded: boolean;
    footage_segments_embedded: number;
    fallback_used: boolean;
  };
  batch_report?: BatchReport;
}

export interface Project {
  id: string;
  workspace_id: string;
  title: string;
  status: string;
  target_platform: string;
  goal: string | null;
  created_at: string;
  updated_at: string;
}

export interface Asset {
  id: string;
  project_id: string;
  type: string;
  filename: string;
  storage_url: string;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
  transcript_status: string;
  created_at: string;
}

export interface StyleProfile {
  id: string;
  project_id: string;
  name: string | null;
  profile_json: Record<string, unknown>;
  model_name: string;
  created_at: string;
}

export interface EditSpec {
  id: string;
  project_id: string;
  version: number;
  spec_json: Record<string, unknown>;
  source: string;
  revision_note: string | null;
  created_at: string;
}

export interface Render {
  id: string;
  project_id: string;
  edit_spec_id: string;
  status: string;
  output_url: string | null;
  preview_url: string | null;
  thumbnail_url: string | null;
  duration_sec: number | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  type: string;
  status: string;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

// ── Projects ─────────────────────────────────────────────────────────────

export const createProject = (data: {
  title: string;
  goal?: string;
}) => api.post<Project>("/projects/", data).then((r) => r.data);

export const listProjects = () =>
  api.get<Project[]>("/projects/").then((r) => r.data);

export const getProject = (id: string) =>
  api.get<Project>(`/projects/${id}`).then((r) => r.data);

export const updateProject = (
  id: string,
  data: { title?: string; goal?: string }
) => api.patch<Project>(`/projects/${id}`, data).then((r) => r.data);

export const deleteProject = (id: string) =>
  api.delete(`/projects/${id}`);

// ── Assets ───────────────────────────────────────────────────────────────

export const uploadAsset = (
  projectId: string,
  file: File,
  assetType: string = "raw_video"
) => {
  const form = new FormData();
  form.append("file", file);
  form.append("asset_type", assetType);
  return api
    .post<Asset>(`/assets/upload/${projectId}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    })
    .then((r) => r.data);
};

export const listAssets = (projectId: string) =>
  api.get<Asset[]>(`/assets/${projectId}`).then((r) => r.data);

export const deleteAsset = (id: string) =>
  api.delete(`/assets/detail/${id}`);

export const transcribeAsset = (id: string) =>
  api.post<Job>(`/assets/transcribe/${id}`).then((r) => r.data);

export const transcribeAll = (projectId: string) =>
  api.post<Job[]>(`/assets/transcribe-all/${projectId}`).then((r) => r.data);

// ── Import from URL (backend endpoint we'll add) ────────────────────────

export const importFromUrl = (projectId: string, url: string) =>
  api
    .post<Job>(`/assets/import-url/${projectId}`, { url })
    .then((r) => r.data);

// ── Analysis & Pipeline ─────────────────────────────────────────────────

export const startAnalysis = (projectId: string) =>
  api
    .post<Job>(`/projects/${projectId}/analyze`)
    .then((r) => r.data);

export const startRender = (projectId: string) =>
  api
    .post<Render>(`/projects/${projectId}/render`)
    .then((r) => r.data);

export const startFullPipeline = (
  projectId: string,
  options?: {
    audio_mode?: AudioMode;
    music_asset_id?: string;
    audio_volume?: number;
    original_audio_volume?: number;
    rhythm_preset?: RhythmPreset;
    content_hint?: string;
  }
) =>
  api
    .post<Job>(`/projects/${projectId}/pipeline`, options ?? {})
    .then((r) => r.data);

/**
 * Beat-aware standalone pipeline — multipart/form-data.
 * Returns PipelineResult with metadata and a video_url to stream.
 */
export const startPipeline = async (
  opts: PipelineStartOptions,
  onStage?: (stage: string) => void,
): Promise<PipelineResult> => {
  const form = new FormData();

  opts.referenceUrls.filter(u => u.trim().startsWith("http"))
    .forEach(u => form.append("reference_url", u));

  opts.referenceFiles.forEach(f => form.append("reference_file", f));
  opts.footageFiles.forEach(f => form.append("footage", f));

  if (opts.musicFile) form.append("music_file", opts.musicFile);

  form.append("content_hint", opts.contentHint);
  form.append("audio_mode", opts.audioMode);
  form.append("audio_volume", String(opts.audioVolume));
  form.append("original_audio_volume", String(opts.originalAudioVolume));
  form.append("rhythm_preset", opts.rhythmPreset);
  form.append("preview", String(opts.preview ?? true));

  if (opts.targetDurationSec != null)
    form.append("target_duration_sec", String(opts.targetDurationSec));
  if (opts.maxSelectedSegments != null)
    form.append("max_selected_segments", String(opts.maxSelectedSegments));
  if (opts.minClipVariety != null)
    form.append("min_clip_variety", String(opts.minClipVariety));

  onStage?.("analyzing_style");

  const response = await axios.post<PipelineResult>(
    `${API_BASE}/api/v1/pipeline/start`,
    form,
    { headers: { "Content-Type": "multipart/form-data" }, timeout: 1200000 },
  );
  return response.data;
};

export const reviseEditSpec = (projectId: string, feedback: string) =>
  api
    .post<EditSpec>(`/projects/${projectId}/revise`, { feedback })
    .then((r) => r.data);

// ── Sub-resources ────────────────────────────────────────────────────────

export const listEditSpecs = (projectId: string) =>
  api.get<EditSpec[]>(`/projects/${projectId}/specs`).then((r) => r.data);

export const listRenders = (projectId: string) =>
  api.get<Render[]>(`/projects/${projectId}/renders`).then((r) => r.data);

export const listStyles = (projectId: string) =>
  api.get<StyleProfile[]>(`/projects/${projectId}/styles`).then((r) => r.data);

export const listJobs = (projectId: string) =>
  api.get<Job[]>(`/projects/${projectId}/jobs`).then((r) => r.data);

// ── Renders ──────────────────────────────────────────────────────────────

export const getRender = (renderId: string) =>
  api.get<Render>(`/renders/${renderId}`).then((r) => r.data);

export const getDownloadUrl = (renderId: string) =>
  `${API_BASE}/api/v1/renders/${renderId}/download`;

export const getThumbnailUrl = (renderId: string) =>
  `${API_BASE}/api/v1/renders/${renderId}/thumbnail`;

export default api;
