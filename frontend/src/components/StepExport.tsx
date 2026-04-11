import { useState } from "react";
import {
  Download,
  RotateCcw,
  Film,
  Clock,
  AlertCircle,
  CheckCircle2,
} from "lucide-react";
import * as api from "../api";
import type { Render, Job } from "../api";

interface Props {
  projectId: string;
  render: Render | null;
  renders: Render[];
  jobs: Job[];
  onRefresh: () => void;
}

export default function StepExport({ projectId, renders, jobs, onRefresh }: Props) {
  const [rendering, setRendering] = useState(false);
  const [pipelining, setPipelining] = useState(false);
  const [error, setError] = useState("");

  const renderJobs = jobs.filter(
    (j) => j.type === "render" && (j.status === "pending" || j.status === "running")
  );
  const isRendering = renderJobs.length > 0;

  const handleRender = async () => {
    setError("");
    setRendering(true);
    try {
      await api.startRender(projectId);
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Render failed to start");
    } finally {
      setRendering(false);
    }
  };

  const handleFullPipeline = async () => {
    setError("");
    setPipelining(true);
    try {
      await api.startFullPipeline(projectId);
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Pipeline failed to start");
    } finally {
      setPipelining(false);
    }
  };

  const statusIcon = (status: string) => {
    switch (status) {
      case "completed": return <CheckCircle2 size={16} style={{ color: "var(--success)" }} />;
      case "failed": return <AlertCircle size={16} style={{ color: "var(--error)" }} />;
      case "rendering": case "processing":
        return <span className="spinner" />;
      default: return <Clock size={16} style={{ color: "var(--text-muted)" }} />;
    }
  };

  return (
    <div>
      <div className="section">
        <div className="section-header">
          <h3>Export Your TikTok</h3>
        </div>
        <p className="text-sm text-muted mb-6">
          Render the final video from your edit plan, or run the full pipeline
          (transcribe → analyze → render) in one shot.
        </p>

        <div className="flex gap-3 mb-6">
          <button
            className="btn btn-primary btn-lg"
            onClick={handleRender}
            disabled={rendering || isRendering}
          >
            {isRendering ? (
              <><span className="spinner" /> Rendering...</>
            ) : (
              <><Film size={18} /> Render Video</>
            )}
          </button>

          <button
            className="btn btn-secondary btn-lg"
            onClick={handleFullPipeline}
            disabled={pipelining || isRendering}
          >
            {pipelining ? (
              <><span className="spinner" /> Starting...</>
            ) : (
              <><RotateCcw size={18} /> Full Pipeline</>
            )}
          </button>
        </div>

        {error && <p className="text-error text-sm mb-4">{error}</p>}
      </div>

      {/* Active renders */}
      {renders.length > 0 && (
        <div className="section">
          <div className="section-header">
            <h3>Renders ({renders.length})</h3>
          </div>

          {renders.map((r) => (
            <div key={r.id} className="card mb-4">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  {statusIcon(r.status)}
                  <span className={`badge badge-${r.status}`}>{r.status}</span>
                  {r.duration_sec && (
                    <span className="text-sm text-muted">{r.duration_sec.toFixed(1)}s</span>
                  )}
                </div>
                <span className="text-sm text-muted font-mono">
                  {new Date(r.created_at).toLocaleTimeString()}
                </span>
              </div>

              {/* Progress indicator for active renders */}
              {(r.status === "queued" || r.status === "rendering") && (
                <div className="mb-4">
                  <div className="progress-bar">
                    <div
                      className="progress-bar-fill"
                      style={{
                        width: r.status === "rendering" ? "60%" : "15%",
                        transition: "width 2s ease",
                      }}
                    />
                  </div>
                  <p className="text-sm text-muted mt-2">
                    {r.status === "queued" ? "Waiting in queue..." : "FFmpeg is rendering your video..."}
                  </p>
                </div>
              )}

              {/* Error */}
              {r.status === "failed" && r.error_message && (
                <div style={{ background: "rgba(255,71,87,0.1)", padding: 12, borderRadius: 8, marginBottom: 12 }}>
                  <p className="text-error text-sm">{r.error_message}</p>
                </div>
              )}

              {/* Completed — download + preview */}
              {r.status === "completed" && (
                <div>
                  <div className="video-preview-container mb-4">
                    <video
                      src={api.getDownloadUrl(r.id)}
                      controls
                      poster={r.thumbnail_url ? api.getThumbnailUrl(r.id) : undefined}
                      style={{ width: "100%", height: "100%" }}
                    />
                  </div>
                  <div className="flex gap-3" style={{ justifyContent: "center" }}>
                    <a
                      href={api.getDownloadUrl(r.id)}
                      download={`tiktok_${projectId}.mp4`}
                      className="btn btn-success btn-lg"
                      style={{ textDecoration: "none" }}
                    >
                      <Download size={18} /> Download MP4
                    </a>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* No renders yet */}
      {renders.length === 0 && !isRendering && (
        <div className="empty-state">
          <div className="empty-state-icon"><Film size={48} /></div>
          <h3>No renders yet</h3>
          <p>Click "Render Video" to create your TikTok, or "Full Pipeline" to run everything end-to-end.</p>
        </div>
      )}
    </div>
  );
}
