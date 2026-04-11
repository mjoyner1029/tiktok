/**
 * API client for the TikTok Style Engine backend — React Native version.
 */
import axios from 'axios';

// ── Configure your backend URL ──────────────────────────────────────────
// For local dev w/ Expo Go:
//   iOS Simulator → http://localhost:8000
//   Android Emulator → http://10.0.2.2:8000
//   Physical device → http://<YOUR_LAN_IP>:8000
const API_BASE = __DEV__
  ? 'http://localhost:8000'
  : 'https://api.yourdomain.com'; // production URL

const api = axios.create({
  baseURL: `${API_BASE}/api/v1`,
  headers: { 'Content-Type': 'application/json' },
  timeout: 30000,
});

// ── Types ────────────────────────────────────────────────────────────────

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

export const createProject = (data: { title: string; goal?: string }) =>
  api.post<Project>('/projects/', data).then((r) => r.data);

export const listProjects = () =>
  api.get<Project[]>('/projects/').then((r) => r.data);

export const getProject = (id: string) =>
  api.get<Project>(`/projects/${id}`).then((r) => r.data);

export const updateProject = (id: string, data: { title?: string; goal?: string }) =>
  api.patch<Project>(`/projects/${id}`, data).then((r) => r.data);

export const deleteProject = (id: string) => api.delete(`/projects/${id}`);

// ── Assets ───────────────────────────────────────────────────────────────

export const uploadAsset = async (
  projectId: string,
  fileUri: string,
  fileName: string,
  mimeType: string,
  assetType: string = 'raw_video'
) => {
  const form = new FormData();
  form.append('file', {
    uri: fileUri,
    name: fileName,
    type: mimeType,
  } as any);
  form.append('asset_type', assetType);
  return api
    .post<Asset>(`/assets/upload/${projectId}`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000,
    })
    .then((r) => r.data);
};

export const listAssets = (projectId: string) =>
  api.get<Asset[]>(`/assets/${projectId}`).then((r) => r.data);

export const deleteAsset = (id: string) => api.delete(`/assets/detail/${id}`);

export const transcribeAsset = (id: string) =>
  api.post<Job>(`/assets/transcribe/${id}`).then((r) => r.data);

export const transcribeAll = (projectId: string) =>
  api.post<Job[]>(`/assets/transcribe-all/${projectId}`).then((r) => r.data);

export const importFromUrl = (projectId: string, url: string) =>
  api.post<Job>(`/assets/import-url/${projectId}`, { url }).then((r) => r.data);

// ── Analysis & Pipeline ─────────────────────────────────────────────────

export const startAnalysis = (projectId: string) =>
  api.post<Job>(`/projects/${projectId}/analyze`).then((r) => r.data);

export const startRender = (projectId: string) =>
  api.post<Render>(`/projects/${projectId}/render`).then((r) => r.data);

export const startFullPipeline = (projectId: string) =>
  api.post<Job>(`/projects/${projectId}/pipeline`).then((r) => r.data);

export const reviseEditSpec = (projectId: string, feedback: string) =>
  api.post<EditSpec>(`/projects/${projectId}/revise`, { feedback }).then((r) => r.data);

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
