import { useState, useRef, useCallback } from "react";
import {
  Link2,
  Upload,
  Sparkles,
  Film,
  Trash2,
  ArrowRight,
  Eye,
} from "lucide-react";
import * as api from "../api";
import type { Asset, StyleProfile, Job } from "../api";
import { useDragDrop } from "../hooks/usePolling";

interface Props {
  projectId: string;
  assets: Asset[];
  styles: StyleProfile[];
  jobs: Job[];
  onRefresh: () => void;
  onNext: () => void;
}

export default function StepReference({ projectId, assets, styles, jobs, onRefresh, onNext }: Props) {
  const [url, setUrl] = useState("");
  const [importing, setImporting] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"upload" | "url">("upload");
  const [showStyle, setShowStyle] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const analysisJobs = jobs.filter((j) => j.type === "analyze_style");
  const isAnalyzing = analysisJobs.some((j) => j.status === "pending" || j.status === "running");

  // Upload reference MP4
  const handleFiles = useCallback(async (files: File[]) => {
    setError("");
    setUploading(true);
    try {
      for (const f of files) {
        await api.uploadAsset(projectId, f, "reference_video");
      }
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [projectId, onRefresh]);

  const { dragOver, handlers } = useDragDrop(handleFiles);

  // Import from URL
  const handleImportUrl = async () => {
    if (!url.trim()) return;
    setError("");
    setImporting(true);
    try {
      await api.importFromUrl(projectId, url.trim());
      setUrl("");
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Import failed — make sure the backend supports URL imports");
    } finally {
      setImporting(false);
    }
  };

  // Start AI analysis
  const handleAnalyze = async () => {
    setError("");
    setAnalyzing(true);
    try {
      await api.startAnalysis(projectId);
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleDeleteAsset = async (id: string) => {
    try {
      await api.deleteAsset(id);
      onRefresh();
    } catch {
      // ignore
    }
  };

  const latestStyle = styles[0] || null;

  return (
    <div>
      <div className="section">
        <div className="section-header">
          <h3>Reference TikToks</h3>
        </div>
        <p className="text-sm text-muted mb-6">
          Drop in TikTok links or upload saved MP4s. The AI will analyze the editing style —
          cuts, coloring, zoom patterns, caption style, pacing, and energy.
        </p>

        {/* Tabs: Upload / URL */}
        <div className="tabs">
          <button className={`tab ${tab === "upload" ? "active" : ""}`} onClick={() => setTab("upload")}>
            <Upload size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
            Upload MP4
          </button>
          <button className={`tab ${tab === "url" ? "active" : ""}`} onClick={() => setTab("url")}>
            <Link2 size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
            Paste Link
          </button>
        </div>

        {tab === "upload" && (
          <div
            className={`upload-zone ${dragOver ? "drag-over" : ""}`}
            {...handlers}
            onClick={() => fileRef.current?.click()}
          >
            <div className="upload-zone-icon"><Film size={40} /></div>
            <h3>Drop reference TikTok MP4s here</h3>
            <p>or click to browse — MP4, MOV, WebM accepted</p>
            {uploading && <div className="spinner mt-4" />}
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) handleFiles(Array.from(e.target.files));
              }}
            />
          </div>
        )}

        {tab === "url" && (
          <div className="url-input-bar">
            <input
              placeholder="https://www.tiktok.com/@user/video/1234567890..."
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleImportUrl()}
            />
            <button
              className="btn btn-primary"
              onClick={handleImportUrl}
              disabled={!url.trim() || importing}
            >
              {importing ? <span className="spinner" /> : <Link2 size={16} />}
              Import
            </button>
          </div>
        )}

        {error && <p className="text-error text-sm mt-4">{error}</p>}
      </div>

      {/* Reference assets list */}
      {assets.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h3>Uploaded References ({assets.length})</h3>
          </div>
          <div className="asset-grid">
            {assets.map((a) => (
              <div key={a.id} className="asset-card">
                <div className="asset-card-thumb">
                  <Film size={32} />
                </div>
                <div className="asset-card-info">
                  <h5>{a.filename}</h5>
                  <p>
                    {a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : "Processing..."}
                    {a.width && a.height ? ` · ${a.width}×${a.height}` : ""}
                  </p>
                  <p>
                    Transcript: <span className={`badge badge-${a.transcript_status}`}>
                      {a.transcript_status}
                    </span>
                  </p>
                </div>
                <div className="asset-card-actions">
                  <button
                    className="btn btn-ghost btn-sm btn-icon"
                    onClick={() => handleDeleteAsset(a.id)}
                    title="Remove"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Analyze button */}
      {assets.length > 0 && (
        <div className="section">
          <div className="flex gap-3 items-center">
            <button
              className="btn btn-primary btn-lg"
              onClick={handleAnalyze}
              disabled={analyzing || isAnalyzing}
            >
              {isAnalyzing ? (
                <><span className="spinner" /> Analyzing Style...</>
              ) : (
                <><Sparkles size={18} /> Analyze Editing Style</>
              )}
            </button>
            {latestStyle && (
              <button className="btn btn-secondary" onClick={() => setShowStyle(!showStyle)}>
                <Eye size={16} /> {showStyle ? "Hide" : "View"} Style Profile
              </button>
            )}
          </div>

          {latestStyle && showStyle && (
            <div className="card mt-4">
              <h4 style={{ fontSize: 14, marginBottom: 12, color: "var(--accent-2)" }}>
                Detected Style Profile
              </h4>
              <div className="json-viewer">
                {JSON.stringify(latestStyle.profile_json, null, 2)}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Next */}
      <div className="flex justify-between mt-6">
        <div />
        <button className="btn btn-primary" onClick={onNext}>
          Next: Upload Your Content <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
