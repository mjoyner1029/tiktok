import { useState, useRef, useCallback } from "react";
import {
  Upload,
  Film,
  Image,
  Music,
  Trash2,
  ArrowRight,
} from "lucide-react";
import * as api from "../api";
import type { Asset } from "../api";
import { useDragDrop } from "../hooks/usePolling";

interface Props {
  projectId: string;
  assets: Asset[];
  onRefresh: () => void;
  onNext: () => void;
}

const TYPE_ICONS: Record<string, typeof Film> = {
  raw_video: Film,
  image: Image,
  audio: Music,
};

export default function StepUpload({ projectId, assets, onRefresh, onNext }: Props) {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [assetType, setAssetType] = useState<string>("raw_video");
  const fileRef = useRef<HTMLInputElement>(null);

  const handleFiles = useCallback(async (files: File[]) => {
    setError("");
    setUploading(true);
    try {
      for (const f of files) {
        // Auto-detect asset type from MIME
        let type = assetType;
        if (f.type.startsWith("image/")) type = "image";
        else if (f.type.startsWith("audio/")) type = "audio";
        else type = "raw_video";
        await api.uploadAsset(projectId, f, type);
      }
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [projectId, assetType, onRefresh]);

  const { dragOver, handlers } = useDragDrop(handleFiles);

  const handleDelete = async (id: string) => {
    try {
      await api.deleteAsset(id);
      onRefresh();
    } catch { /* ignore */ }
  };

  const handleTranscribeAll = async () => {
    try {
      await api.transcribeAll(projectId);
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Transcription failed");
    }
  };

  const videoAssets = assets.filter((a) => a.type === "raw_video");
  const imageAssets = assets.filter((a) => a.type === "image");
  const audioAssets = assets.filter((a) => a.type === "audio");

  return (
    <div>
      <div className="section">
        <div className="section-header">
          <h3>Upload Your Content</h3>
        </div>
        <p className="text-sm text-muted mb-6">
          Upload your own videos, images, and audio. These are the raw materials
          the AI will use to create your TikTok edit.
        </p>

        {/* Upload type selector */}
        <div className="flex gap-3 mb-4">
          {[
            { val: "raw_video", label: "Video", icon: Film },
            { val: "image", label: "Image", icon: Image },
            { val: "audio", label: "Audio / Music", icon: Music },
          ].map(({ val, label, icon: Icon }) => (
            <button
              key={val}
              className={`btn ${assetType === val ? "btn-primary" : "btn-secondary"} btn-sm`}
              onClick={() => setAssetType(val)}
            >
              <Icon size={14} /> {label}
            </button>
          ))}
        </div>

        <div
          className={`upload-zone ${dragOver ? "drag-over" : ""}`}
          {...handlers}
          onClick={() => fileRef.current?.click()}
        >
          <div className="upload-zone-icon"><Upload size={40} /></div>
          <h3>Drop your {assetType === "raw_video" ? "videos" : assetType === "image" ? "images" : "audio files"} here</h3>
          <p>or click to browse — supports MP4, MOV, PNG, JPG, MP3, WAV</p>
          {uploading && <div className="spinner mt-4" />}
          <input
            ref={fileRef}
            type="file"
            accept={assetType === "raw_video" ? "video/*" : assetType === "image" ? "image/*" : "audio/*"}
            multiple
            hidden
            onChange={(e) => {
              if (e.target.files) handleFiles(Array.from(e.target.files));
            }}
          />
        </div>

        {error && <p className="text-error text-sm mt-4">{error}</p>}
      </div>

      {/* Asset lists */}
      {assets.length > 0 && (
        <>
          {/* Videos */}
          {videoAssets.length > 0 && (
            <div className="section">
              <div className="section-header">
                <h3>Videos ({videoAssets.length})</h3>
                <button className="btn btn-secondary btn-sm" onClick={handleTranscribeAll}>
                  Transcribe All
                </button>
              </div>
              <div className="asset-grid">
                {videoAssets.map((a) => {
                  const Icon = TYPE_ICONS[a.type] || Film;
                  return (
                    <div key={a.id} className="asset-card">
                      <div className="asset-card-thumb"><Icon size={32} /></div>
                      <div className="asset-card-info">
                        <h5>{a.filename}</h5>
                        <p>
                          {a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : "—"}
                          {a.width ? ` · ${a.width}×${a.height}` : ""}
                        </p>
                        <p>
                          <span className={`badge badge-${a.transcript_status}`}>
                            {a.transcript_status}
                          </span>
                        </p>
                      </div>
                      <div className="asset-card-actions">
                        <button className="btn btn-ghost btn-sm btn-icon" onClick={() => handleDelete(a.id)}>
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Images */}
          {imageAssets.length > 0 && (
            <div className="section">
              <div className="section-header"><h3>Images ({imageAssets.length})</h3></div>
              <div className="asset-grid">
                {imageAssets.map((a) => (
                  <div key={a.id} className="asset-card">
                    <div className="asset-card-thumb"><Image size={32} /></div>
                    <div className="asset-card-info">
                      <h5>{a.filename}</h5>
                      <p>{a.width ? `${a.width}×${a.height}` : "—"}</p>
                    </div>
                    <div className="asset-card-actions">
                      <button className="btn btn-ghost btn-sm btn-icon" onClick={() => handleDelete(a.id)}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Audio */}
          {audioAssets.length > 0 && (
            <div className="section">
              <div className="section-header"><h3>Audio ({audioAssets.length})</h3></div>
              <div className="asset-grid">
                {audioAssets.map((a) => (
                  <div key={a.id} className="asset-card">
                    <div className="asset-card-thumb"><Music size={32} /></div>
                    <div className="asset-card-info">
                      <h5>{a.filename}</h5>
                      <p>{a.duration_sec ? `${a.duration_sec.toFixed(1)}s` : "—"}</p>
                    </div>
                    <div className="asset-card-actions">
                      <button className="btn btn-ghost btn-sm btn-icon" onClick={() => handleDelete(a.id)}>
                        <Trash2 size={14} />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      {/* Navigation */}
      <div className="flex justify-between mt-6">
        <div />
        <button className="btn btn-primary" onClick={onNext} disabled={assets.length === 0}>
          Next: Edit & Captions <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
