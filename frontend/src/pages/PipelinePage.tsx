import { useState, useRef, useCallback } from "react";
import {
  Link2, FileVideo, Upload, Trash2, Loader2, Sparkles,
  ChevronRight, CheckCircle2, Download, RotateCcw, Film,
  Music, Sliders, Cpu, Clock, Zap, BarChart3, PlayCircle,
  List, AlertCircle,
} from "lucide-react";
import * as api from "../api";
import type { AudioMode, RhythmPreset, PipelineResult, PipelineClipResult, BatchReport } from "../api";

// ── Constants ──────────────────────────────────────────────────────────────

const AUDIO_MODES: { value: AudioMode; label: string; description: string }[] = [
  { value: "reference_audio",  label: "Reference Audio",   description: "Use music from the reference TikTok" },
  { value: "uploaded_audio",   label: "Uploaded Audio",    description: "Use your own uploaded music/audio file" },
  { value: "original_audio",   label: "Original Audio",    description: "Keep the original audio from footage clips" },
  { value: "silent",           label: "Silent",            description: "Strip all audio — add your own in post" },
];

const RHYTHM_PRESETS: { value: RhythmPreset; label: string; description: string }[] = [
  { value: "tight_sync",  label: "Tight Sync",  description: "Cuts snap precisely to every beat (±80ms)" },
  { value: "loose_sync",  label: "Loose Sync",  description: "Cuts align to downbeats (±200ms)" },
  { value: "cinematic",   label: "Cinematic",   description: "Cuts follow 4/8-bar phrase boundaries" },
  { value: "chaotic",     label: "Chaotic",     description: "Beat-aligned with intentional random drift" },
];

const TARGET_DURATIONS = [15, 30, 45, 60] as const;
type TargetDuration = typeof TARGET_DURATIONS[number];

type Stage = "idle" | "analyzing_style" | "generating_plan" | "rendering" | "done" | "error";

const STAGE_LABELS: Record<Stage, string> = {
  idle:            "",
  analyzing_style: "Analyzing reference style & beats…",
  generating_plan: "Planning shot timeline…",
  rendering:       "Rendering with FFmpeg…",
  done:            "Done",
  error:           "Error",
};

// ── Sub-components ─────────────────────────────────────────────────────────

function FileChip({ file, onRemove }: { file: File; onRemove: () => void }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)", padding: "8px 12px",
    }}>
      <FileVideo size={13} color="var(--accent-2)" style={{ flexShrink: 0 }} />
      <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {file.name}
      </span>
      <span style={{ fontSize: 11, color: "var(--text-dim)", flexShrink: 0 }}>
        {(file.size / 1024 / 1024).toFixed(1)} MB
      </span>
      <button
        onClick={onRemove}
        style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-dim)", padding: 0, display: "flex" }}
      >
        <Trash2 size={13} />
      </button>
    </div>
  );
}

function SliderRow({
  label, value, min, max, step, unit, onChange,
}: {
  label: string; value: number; min: number; max: number; step: number;
  unit?: string; onChange: (v: number) => void;
}) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
      <span style={{ fontSize: 13, color: "var(--text-muted)", width: 180, flexShrink: 0 }}>{label}</span>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        style={{ flex: 1, accentColor: "var(--accent)" }}
      />
      <span style={{ fontSize: 13, fontWeight: 600, width: 56, textAlign: "right", flexShrink: 0 }}>
        {value}{unit ?? ""}
      </span>
    </div>
  );
}

function MetaBadge({ icon: Icon, label, value }: {
  icon: typeof Cpu; label: string; value: string | number | null | undefined;
}) {
  if (value == null) return null;
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 4,
      background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)", padding: "12px 16px", minWidth: 110,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--text-muted)", fontSize: 11, textTransform: "uppercase", letterSpacing: "0.5px" }}>
        <Icon size={12} /> {label}
      </div>
      <span style={{ fontSize: 16, fontWeight: 700 }}>{value}</span>
    </div>
  );
}

function ClipCard({ clip, index }: { clip: PipelineClipResult; index: number }) {
  const [open, setOpen] = useState(false);
  const hasBreakdown = Object.keys(clip.score_breakdown ?? {}).length > 0;
  return (
    <div style={{
      background: "var(--bg-elevated)", borderRadius: "var(--radius-sm)",
      border: "1px solid var(--border)", overflow: "hidden",
    }}>
      <div
        style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", cursor: hasBreakdown ? "pointer" : "default" }}
        onClick={() => hasBreakdown && setOpen(o => !o)}
      >
        <span style={{ fontSize: 12, color: "var(--text-dim)", width: 20, flexShrink: 0 }}>#{index + 1}</span>
        <span style={{ fontSize: 13, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--accent-2)" }}>
          {clip.asset_id}
        </span>
        <span style={{ fontSize: 12, color: "var(--text-muted)", flexShrink: 0 }}>
          {clip.start.toFixed(1)}s → {clip.end.toFixed(1)}s ({clip.duration.toFixed(1)}s)
        </span>
        <span style={{
          fontSize: 12, fontWeight: 700, flexShrink: 0,
          color: clip.score > 0.7 ? "var(--success)" : clip.score > 0.4 ? "var(--warning)" : "var(--text-muted)",
        }}>
          {(clip.score * 100).toFixed(0)}%
        </span>
        {hasBreakdown && (
          <span style={{ fontSize: 11, color: "var(--text-dim)", flexShrink: 0 }}>{open ? "▲" : "▼"}</span>
        )}
      </div>
      {open && hasBreakdown && (
        <div style={{ padding: "8px 14px 12px", borderTop: "1px solid var(--border)", display: "flex", flexWrap: "wrap", gap: 8 }}>
          {clip.description && (
            <p style={{ fontSize: 12, color: "var(--text-muted)", width: "100%", marginBottom: 4 }}>{clip.description}</p>
          )}
          {Object.entries(clip.score_breakdown).map(([k, v]) => (
            <div key={k} style={{
              fontSize: 11, padding: "3px 8px", borderRadius: 4,
              background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-muted)",
            }}>
              <span style={{ color: "var(--text)" }}>{k}</span>: {typeof v === "number" ? v.toFixed(2) : String(v)}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function PacingChart({ curve }: { curve: number[] }) {
  if (!curve.length) return null;
  const max = Math.max(...curve, 0.01);
  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 40, marginTop: 8 }}>
      {curve.map((v, i) => (
        <div
          key={i}
          title={`clip ${i + 1}: ${(v * 100).toFixed(0)}%`}
          style={{
            flex: 1, height: `${(v / max) * 100}%`, minHeight: 2,
            background: `hsl(${260 - (v / max) * 100}, 80%, 65%)`,
            borderRadius: "2px 2px 0 0",
          }}
        />
      ))}
    </div>
  );
}

// ── BatchReportPanel ────────────────────────────────────────────────────────

function BatchReportPanel({ report }: { report: BatchReport }) {
  const [expanded, setExpanded] = useState(false);
  const rejectedCount = report.rejection_log?.length ?? 0;
  return (
    <div style={{
      marginTop: 20,
      background: "var(--bg-elevated)",
      borderRadius: "var(--radius)",
      padding: "16px 20px",
      border: "1px solid var(--border)",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 12 }}>
        <List size={14} color="var(--accent)" />
        <span style={{ fontWeight: 600, fontSize: 14 }}>Batch Analysis Report</span>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
        {[
          { label: "Clips",     value: report.total_clips },
          { label: "Segments raw", value: report.total_segments_before_dedup },
          { label: "After dedup",  value: report.total_segments_after_dedup },
          { label: "After cap",    value: report.total_segments_after_cap },
          { label: "Selected",     value: report.total_selected },
          { label: "Rejected",     value: rejectedCount },
        ].map(({ label, value }) => (
          <div key={label} style={{
            background: "var(--bg)",
            borderRadius: "var(--radius-sm)",
            padding: "10px 14px",
            textAlign: "center",
          }}>
            <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text)" }}>{value ?? "–"}</div>
            <div style={{ fontSize: 11, color: "var(--text-dim)", marginTop: 2 }}>{label}</div>
          </div>
        ))}
      </div>
      {rejectedCount > 0 && (
        <div style={{ marginTop: 12 }}>
          <button
            onClick={() => setExpanded(v => !v)}
            style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "transparent", border: "none", cursor: "pointer",
              color: "var(--text-dim)", fontSize: 12, padding: 0,
            }}
          >
            <AlertCircle size={12} />
            {expanded ? "Hide" : "Show"} rejection log ({rejectedCount})
          </button>
          {expanded && (
            <div style={{
              marginTop: 8, maxHeight: 200, overflowY: "auto",
              fontSize: 11, color: "var(--text-dim)", fontFamily: "monospace",
            }}>
              {report.rejection_log.map((r, i) => (
                <div key={i} style={{ padding: "2px 0", borderBottom: "1px solid var(--border)" }}>
                  {r.asset_id} @ {r.start ?? "?"}s — {r.reason}
                  {r.score != null ? ` (score: ${r.score.toFixed(3)})` : ""}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── ResultPanel ────────────────────────────────────────────────────────────

function ResultPanel({ result, onReset }: { result: PipelineResult; onReset: () => void }) {
  const API_BASE = import.meta.env.VITE_API_URL || "";
  const videoSrc = result.video_url.startsWith("/")
    ? `${API_BASE}${result.video_url}`
    : result.video_url;

  const download = () => {
    const a = document.createElement("a");
    a.href = videoSrc;
    a.download = `tiktok_${result.project_id}.mp4`;
    a.click();
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <video
        src={videoSrc}
        controls
        autoPlay
        data-testid="result-video"
        style={{ width: "100%", maxHeight: 640, borderRadius: "var(--radius)", background: "#000", border: "1px solid var(--border)" }}
      />

      <div style={{ display: "flex", gap: 10 }}>
        <button
          onClick={download}
          style={{ display: "flex", alignItems: "center", gap: 7, background: "var(--accent)", color: "#fff", border: "none", borderRadius: "var(--radius-sm)", padding: "11px 20px", fontSize: 14, fontWeight: 600, cursor: "pointer" }}
        >
          <Download size={15} /> Download MP4
        </button>
        <button
          onClick={onReset}
          style={{ display: "flex", alignItems: "center", gap: 7, background: "transparent", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "11px 20px", color: "var(--text-muted)", fontSize: 14, cursor: "pointer" }}
        >
          <RotateCcw size={14} /> Start over
        </button>
      </div>

      {/* Metadata badges */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }} data-testid="result-meta">
        <MetaBadge icon={Cpu}        label="Ranking"    value={result.ranking_profile} />
        <MetaBadge icon={Music}      label="Audio"      value={result.audio_mode?.replace(/_/g, " ")} />
        <MetaBadge icon={Zap}        label="Rhythm"     value={result.rhythm_preset?.replace(/_/g, " ")} />
        <MetaBadge icon={BarChart3}  label="BPM"        value={result.bpm != null ? `${Number(result.bpm).toFixed(0)} bpm` : null} />
        <MetaBadge icon={PlayCircle} label="Beats"      value={result.beat_count ?? null} />
        <MetaBadge icon={Clock}      label="Duration"   value={result.timeline_duration != null ? `${result.timeline_duration.toFixed(1)}s` : null} />
        <MetaBadge icon={Film}       label="Clips"      value={result.clip_count ?? null} />
        {result.render_style && (
          <MetaBadge icon={Sparkles} label="Style"      value={result.render_style} />
        )}
        {result.escalation_score != null && (
          <MetaBadge icon={Sliders}  label="Escalation" value={`${(result.escalation_score * 100).toFixed(0)}%`} />
        )}
      </div>

      {/* Pacing curve */}
      {result.pacing_curve && result.pacing_curve.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "16px" }}>
          <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 4 }}>Pacing Curve</p>
          <PacingChart curve={result.pacing_curve} />
        </div>
      )}

      {/* Top clips */}
      {result.top_clips && result.top_clips.length > 0 && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "16px" }}>
          <p style={{ fontSize: 13, fontWeight: 600, marginBottom: 12 }}>
            Top Selected Clips — click a row to see score breakdown
          </p>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }} data-testid="clip-list">
            {result.top_clips.map((c, i) => <ClipCard key={i} clip={c} index={i} />)}
          </div>
        </div>
      )}

      {result.batch_report && result.batch_report.total_clips > 0 && (
        <BatchReportPanel report={result.batch_report} />
      )}

      <p style={{ fontSize: 11, color: "var(--text-dim)", fontFamily: "var(--font-mono)" }}>
        project_id: {result.project_id}
      </p>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function PipelinePage() {
  // Reference
  const [refTab, setRefTab]               = useState<"url" | "file">("url");
  const [referenceUrls, setReferenceUrls] = useState<string[]>([""]);
  const [referenceFiles, setReferenceFiles] = useState<File[]>([]);
  // Footage
  const [footageFiles, setFootageFiles]   = useState<File[]>([]);
  const [dragOverFootage, setDragOverFootage] = useState(false);
  // Music
  const [musicFile, setMusicFile]         = useState<File | null>(null);
  const [dragOverMusic, setDragOverMusic] = useState(false);
  // Beat options
  const [audioMode, setAudioMode]         = useState<AudioMode>("reference_audio");
  const [audioVolume, setAudioVolume]     = useState(-18);
  const [originalAudioVolume, setOriginalAudioVolume] = useState(0);
  const [rhythmPreset, setRhythmPreset]   = useState<RhythmPreset>("loose_sync");
  const [contentHint, setContentHint]     = useState("");
  // Batch / large-footage options
  const [targetDuration, setTargetDuration]       = useState<TargetDuration>(30);
  const [maxSelectedSegments, setMaxSelectedSegments] = useState<string>("");
  const [showAdvanced, setShowAdvanced]           = useState(false);
  const [previewMode, setPreviewMode]             = useState(false);
  // Status
  const [stage, setStage]   = useState<Stage>("idle");
  const [error, setError]   = useState("");
  const [result, setResult] = useState<PipelineResult | null>(null);

  const footageRef = useRef<HTMLInputElement>(null);
  const refFileRef = useRef<HTMLInputElement>(null);
  const musicRef   = useRef<HTMLInputElement>(null);

  const validUrls = referenceUrls.filter(u => u.trim().startsWith("http"));
  const hasRef    = refTab === "url" ? validUrls.length > 0 : referenceFiles.length > 0;
  const canRun    = hasRef && footageFiles.length > 0;
  const running   = stage !== "idle" && stage !== "done" && stage !== "error";

  const addUrl    = () => setReferenceUrls(u => [...u, ""]);
  const removeUrl = (i: number) => setReferenceUrls(u => u.filter((_, idx) => idx !== i));
  const setUrl    = (i: number, v: string) => setReferenceUrls(u => u.map((x, idx) => idx === i ? v : x));

  const addFootageFiles = useCallback((incoming: FileList | File[]) => {
    const files = Array.from(incoming).filter(
      f => f.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm)$/i.test(f.name)
    );
    if (!files.length) return;
    setFootageFiles(prev => {
      const seen = new Set(prev.map(f => f.name + f.size));
      return [...prev, ...files.filter(f => !seen.has(f.name + f.size))];
    });
  }, []);

  const addRefFiles = useCallback((incoming: FileList | File[]) => {
    const files = Array.from(incoming).filter(
      f => f.type.startsWith("video/") || /\.(mp4|mov|avi|mkv|webm)$/i.test(f.name)
    );
    if (!files.length) return;
    setReferenceFiles(prev => {
      const seen = new Set(prev.map(f => f.name + f.size));
      return [...prev, ...files.filter(f => !seen.has(f.name + f.size))];
    });
  }, []);

  const handleMusicFile = (incoming: FileList | File[]) => {
    const file = Array.from(incoming).find(
      f => f.type.startsWith("audio/") || /\.(mp3|wav|aac|flac|m4a|ogg)$/i.test(f.name)
    );
    if (file) setMusicFile(file);
  };

  const run = async () => {
    if (!canRun || running) return;
    setError("");
    setResult(null);
    setStage("analyzing_style");

    const t1 = setTimeout(() => setStage("generating_plan"), 70_000);
    const t2 = setTimeout(() => setStage("rendering"),       150_000);

    try {
      const maxSegs = maxSelectedSegments.trim() ? parseInt(maxSelectedSegments, 10) : undefined;
      const res = await api.startPipeline({
        referenceUrls:       refTab === "url" ? validUrls : [],
        referenceFiles:      refTab === "file" ? referenceFiles : [],
        footageFiles,
        musicFile:           audioMode === "uploaded_audio" ? musicFile : null,
        audioMode,
        audioVolume,
        originalAudioVolume,
        rhythmPreset,
        contentHint,
        preview: previewMode,
        targetDurationSec: targetDuration,
        maxSelectedSegments: maxSegs && maxSegs > 0 ? maxSegs : undefined,
      });
      clearTimeout(t1);
      clearTimeout(t2);
      setResult(res);
      setStage("done");
    } catch (e: any) {
      clearTimeout(t1);
      clearTimeout(t2);
      const msg = e?.response?.data?.detail ?? e?.message ?? "Pipeline failed";
      setError(String(msg));
      setStage("error");
    }
  };

  const reset = () => { setStage("idle"); setResult(null); setError(""); };

  return (
    <div style={{ padding: "32px", maxWidth: 820, margin: "0 auto" }}>
      <div style={{ marginBottom: 32 }}>
        <h2 style={{ fontSize: 24, fontWeight: 700, marginBottom: 6, display: "flex", alignItems: "center", gap: 10 }}>
          <Film size={22} color="var(--accent)" />
          Beat-Aware Style Cloner
        </h2>
        <p style={{ color: "var(--text-muted)", fontSize: 14 }}>
          Upload footage + a reference TikTok. Set audio mode and rhythm. Get a beat-synced edit.
        </p>
      </div>

      {/* ── Done ───────────────────────────────────────────────────────── */}
      {stage === "done" && result && <ResultPanel result={result} onReset={reset} />}

      {/* ── Running ────────────────────────────────────────────────────── */}
      {running && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 40, textAlign: "center" }}>
          <Loader2 size={40} color="var(--accent)" className="spin" style={{ marginBottom: 20 }} />
          <p style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>{STAGE_LABELS[stage]}</p>
          <p style={{ fontSize: 13, color: "var(--text-muted)", maxWidth: 460, margin: "0 auto" }}>
            {stage === "analyzing_style" && footageFiles.length > 10
              ? `Analyzing ${footageFiles.length} footage clips with batch deduplication…`
              : stage === "analyzing_style" ? "Downloading reference, extracting frames, running beat analysis…"
              : stage === "generating_plan" ? "Claude is writing the shot timeline synced to your rhythm preset…"
              : stage === "rendering" ? "FFmpeg is cutting clips, mixing audio, and stamping captions…"
              : null}
          </p>
          <div style={{ marginTop: 28, display: "flex", justifyContent: "center", gap: 24 }}>
            {(["analyzing_style", "generating_plan", "rendering"] as Stage[]).map((s, i) => {
              const idx  = ["analyzing_style", "generating_plan", "rendering"].indexOf(stage);
              const done   = idx > i;
              const active = stage === s;
              return (
                <div key={s} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: active ? "var(--accent)" : done ? "var(--success)" : "var(--text-dim)" }}>
                  {done   ? <CheckCircle2 size={13} /> :
                   active ? <Loader2 size={13} className="spin" /> :
                            <div style={{ width: 13, height: 13, borderRadius: "50%", border: "1.5px solid currentColor" }} />}
                  {["Analyze style", "Plan timeline", "Render"][i]}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Form ───────────────────────────────────────────────────────── */}
      {(stage === "idle" || stage === "error") && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>

          {/* ── Reference ────────────────────────────────────────────── */}
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
              <Link2 size={16} color="var(--accent-2)" />
              <span style={{ fontWeight: 600 }}>Reference TikTok</span>
              <span style={{ fontSize: 11, color: "var(--text-dim)", background: "var(--bg-elevated)", padding: "2px 8px", borderRadius: 4, marginLeft: 4 }}>style template</span>
            </div>
            <p style={{ color: "var(--text-muted)", fontSize: 13, marginBottom: 14 }}>
              The AI extracts pacing, beats, captions, and motion from this video.
            </p>
            <div style={{ display: "flex", gap: 6, marginBottom: 14 }}>
              {(["url", "file"] as const).map(t => (
                <button key={t} onClick={() => setRefTab(t)} style={{
                  background: refTab === t ? "var(--accent)" : "var(--bg-elevated)",
                  color: refTab === t ? "#fff" : "var(--text-muted)",
                  border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                  padding: "5px 14px", fontSize: 12, fontWeight: 600, cursor: "pointer",
                }}>
                  {t === "url" ? "URL" : "Upload File"}
                </button>
              ))}
            </div>

            {refTab === "url" ? (
              <>
                {referenceUrls.map((u, i) => (
                  <div key={i} style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                    <input
                      value={u} onChange={e => setUrl(i, e.target.value)}
                      placeholder="https://www.tiktok.com/@creator/video/…"
                      data-testid={`ref-url-${i}`}
                      style={{ flex: 1, background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "10px 14px", color: "var(--text)", fontSize: 13 }}
                    />
                    {referenceUrls.length > 1 && (
                      <button onClick={() => removeUrl(i)} style={{ background: "transparent", border: "none", cursor: "pointer", color: "var(--text-muted)" }}>
                        <Trash2 size={15} />
                      </button>
                    )}
                  </div>
                ))}
                <button onClick={addUrl} style={{ display: "flex", alignItems: "center", gap: 6, background: "transparent", border: "1px dashed var(--border)", borderRadius: "var(--radius-sm)", padding: "7px 14px", color: "var(--text-muted)", fontSize: 12, cursor: "pointer" }}>
                  <ChevronRight size={12} /> Add another URL
                </button>
              </>
            ) : (
              <>
                <div
                  onClick={() => refFileRef.current?.click()}
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); addRefFiles(e.dataTransfer.files); }}
                  style={{
                    border: `2px dashed ${referenceFiles.length ? "var(--success)" : "var(--border)"}`,
                    borderRadius: "var(--radius-sm)", padding: "20px", textAlign: "center", cursor: "pointer",
                    background: referenceFiles.length ? "rgba(0,214,143,0.04)" : "var(--bg-elevated)", marginBottom: 10,
                  }}
                >
                  <Upload size={20} color={referenceFiles.length ? "var(--success)" : "var(--text-dim)"} style={{ marginBottom: 6 }} />
                  <p style={{ fontSize: 13, color: referenceFiles.length ? "var(--text)" : "var(--text-muted)" }}>
                    {referenceFiles.length ? `${referenceFiles.length} file(s) selected` : "Drop reference video(s) here or click to browse"}
                  </p>
                </div>
                <input ref={refFileRef} type="file" accept="video/*" multiple hidden
                  onChange={e => e.target.files && addRefFiles(e.target.files)} />
                {referenceFiles.map((f, i) => (
                  <FileChip key={i} file={f} onRemove={() => setReferenceFiles(p => p.filter((_, idx) => idx !== i))} />
                ))}
              </>
            )}
          </div>

          {/* ── Raw Footage ──────────────────────────────────────────── */}
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <FileVideo size={16} color="var(--accent-2)" />
              <span style={{ fontWeight: 600 }}>Raw Footage</span>
              {footageFiles.length > 0 && (
                <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-muted)" }}>
                  {footageFiles.length} file{footageFiles.length !== 1 ? "s" : ""}
                </span>
              )}
            </div>
            <div
              onClick={() => footageRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDragOverFootage(true); }}
              onDragLeave={() => setDragOverFootage(false)}
              onDrop={e => { e.preventDefault(); setDragOverFootage(false); addFootageFiles(e.dataTransfer.files); }}
              style={{
                border: `2px dashed ${dragOverFootage ? "var(--accent)" : footageFiles.length ? "var(--success)" : "var(--border)"}`,
                borderRadius: "var(--radius-sm)", padding: "28px 20px", textAlign: "center", cursor: "pointer",
                background: dragOverFootage ? "rgba(254,44,85,0.06)" : footageFiles.length ? "rgba(0,214,143,0.04)" : "var(--bg-elevated)",
                marginBottom: 10, transition: "border-color 0.15s, background 0.15s",
              }}
            >
              <FileVideo size={24} color={footageFiles.length ? "var(--success)" : "var(--text-dim)"} style={{ marginBottom: 8 }} />
              <p style={{ fontSize: 13, color: footageFiles.length ? "var(--text)" : "var(--text-muted)", fontWeight: footageFiles.length ? 600 : 400 }}>
                {footageFiles.length ? "Drop more clips to add" : "Drop footage here or click to browse"}
              </p>
              <p style={{ fontSize: 12, color: "var(--text-dim)", marginTop: 4 }}>MP4, MOV, AVI, MKV, WEBM — multiple files OK</p>
            </div>
            <input ref={footageRef} type="file" accept="video/*" multiple hidden
              onChange={e => e.target.files && addFootageFiles(e.target.files)} />
            {footageFiles.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 12 }}>
                {footageFiles.map((f, i) => (
                  <FileChip key={i} file={f} onRemove={() => setFootageFiles(p => p.filter((_, idx) => idx !== i))} />
                ))}
              </div>
            )}
            <p style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 6 }}>
              <Sparkles size={11} style={{ marginRight: 4, verticalAlign: "middle" }} />
              Optional — describe your footage to help Claude write captions:
            </p>
            <textarea
              value={contentHint}
              onChange={e => setContentHint(e.target.value)}
              placeholder="e.g. Morning routine — bedroom, kitchen coffee, short walk outside…"
              rows={3}
              data-testid="content-hint"
              style={{ width: "100%", background: "var(--bg-elevated)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: "10px 14px", color: "var(--text)", fontSize: 13, resize: "vertical", lineHeight: 1.6, fontFamily: "var(--font)" }}
            />
          </div>

          {/* ── Audio ────────────────────────────────────────────────── */}
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
              <Music size={16} color="var(--accent-2)" />
              <span style={{ fontWeight: 600 }}>Audio Settings</span>
            </div>

            <label style={{ fontSize: 12, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 600, display: "block", marginBottom: 8 }}>
              Audio Mode
            </label>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 16 }}>
              {AUDIO_MODES.map(({ value, label, description }) => (
                <label key={value} style={{
                  display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
                  background: audioMode === value ? "rgba(254,44,85,0.08)" : "var(--bg-elevated)",
                  border: `1px solid ${audioMode === value ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: "var(--radius-sm)", padding: "10px 12px",
                }}>
                  <input type="radio" name="audio_mode" value={value} checked={audioMode === value}
                    onChange={() => setAudioMode(value)} style={{ marginTop: 2, accentColor: "var(--accent)" }} />
                  <div>
                    <p style={{ fontSize: 13, fontWeight: 600 }}>{label}</p>
                    <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{description}</p>
                  </div>
                </label>
              ))}
            </div>

            {audioMode === "uploaded_audio" && (
              <div style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.5px", fontWeight: 600, display: "block", marginBottom: 8 }}>
                  Music File
                </label>
                <div
                  onClick={() => musicRef.current?.click()}
                  onDragOver={e => { e.preventDefault(); setDragOverMusic(true); }}
                  onDragLeave={() => setDragOverMusic(false)}
                  onDrop={e => { e.preventDefault(); setDragOverMusic(false); handleMusicFile(e.dataTransfer.files); }}
                  style={{
                    border: `2px dashed ${dragOverMusic ? "var(--accent)" : musicFile ? "var(--success)" : "var(--border)"}`,
                    borderRadius: "var(--radius-sm)", padding: "16px 20px", textAlign: "center", cursor: "pointer",
                    background: dragOverMusic ? "rgba(254,44,85,0.06)" : musicFile ? "rgba(0,214,143,0.04)" : "var(--bg-elevated)",
                  }}
                >
                  <Music size={20} color={musicFile ? "var(--success)" : "var(--text-dim)"} style={{ marginBottom: 6 }} />
                  <p style={{ fontSize: 13, color: musicFile ? "var(--text)" : "var(--text-muted)" }}>
                    {musicFile ? musicFile.name : "Drop an MP3, WAV, or AAC file here"}
                  </p>
                </div>
                <input ref={musicRef} type="file" accept="audio/*" hidden
                  onChange={e => e.target.files && handleMusicFile(e.target.files)} />
                {musicFile && (
                  <button onClick={() => setMusicFile(null)} style={{ marginTop: 6, fontSize: 12, color: "var(--text-muted)", background: "transparent", border: "none", cursor: "pointer", display: "flex", alignItems: "center", gap: 4 }}>
                    <Trash2 size={12} /> Remove
                  </button>
                )}
              </div>
            )}

            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <SliderRow label="Music volume (dBFS)" value={audioVolume} min={-40} max={0} step={1} unit=" dB" onChange={setAudioVolume} />
              <SliderRow label="Original audio mix" value={originalAudioVolume} min={0} max={1} step={0.05} onChange={setOriginalAudioVolume} />
            </div>
          </div>

          {/* ── Rhythm Preset ────────────────────────────────────────── */}
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
              <Sliders size={16} color="var(--accent-2)" />
              <span style={{ fontWeight: 600 }}>Rhythm Preset</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {RHYTHM_PRESETS.map(({ value, label, description }) => (
                <label key={value} style={{
                  display: "flex", alignItems: "flex-start", gap: 10, cursor: "pointer",
                  background: rhythmPreset === value ? "rgba(37,244,238,0.08)" : "var(--bg-elevated)",
                  border: `1px solid ${rhythmPreset === value ? "var(--accent-2)" : "var(--border)"}`,
                  borderRadius: "var(--radius-sm)", padding: "10px 12px",
                }}>
                  <input type="radio" name="rhythm_preset" value={value} checked={rhythmPreset === value}
                    onChange={() => setRhythmPreset(value)} style={{ marginTop: 2, accentColor: "var(--accent-2)" }} />
                  <div>
                    <p style={{ fontSize: 13, fontWeight: 600 }}>{label}</p>
                    <p style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 2 }}>{description}</p>
                  </div>
                </label>
              ))}
            </div>
          </div>

          {/* ── Target Duration + Advanced ──────────────────────────── */}
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius)", padding: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
              <Clock size={16} color="var(--accent-2)" />
              <span style={{ fontWeight: 600 }}>Output Duration</span>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 }}>
              {TARGET_DURATIONS.map(sec => (
                <button
                  key={sec}
                  onClick={() => setTargetDuration(sec)}
                  data-testid={`duration-${sec}`}
                  style={{
                    padding: "8px 20px",
                    borderRadius: "var(--radius-sm)",
                    border: `1px solid ${targetDuration === sec ? "var(--accent-2)" : "var(--border)"}`,
                    background: targetDuration === sec ? "rgba(37,244,238,0.08)" : "var(--bg-elevated)",
                    color: targetDuration === sec ? "var(--accent-2)" : "var(--text-dim)",
                    fontWeight: targetDuration === sec ? 700 : 400,
                    cursor: "pointer", fontSize: 14,
                  }}
                >
                  {sec}s
                </button>
              ))}
            </div>
            <button
              onClick={() => setShowAdvanced(v => !v)}
              style={{
                display: "flex", alignItems: "center", gap: 6,
                background: "transparent", border: "none", cursor: "pointer",
                color: "var(--text-dim)", fontSize: 12, padding: 0,
              }}
            >
              <Sliders size={12} />
              {showAdvanced ? "Hide" : "Show"} advanced batch settings
            </button>
            {showAdvanced && (
              <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <label style={{ fontSize: 12, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                    Max segments fed to planner (leave blank for default)
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={maxSelectedSegments}
                    onChange={e => setMaxSelectedSegments(e.target.value)}
                    placeholder="e.g. 80"
                    data-testid="max-selected-segments"
                    style={{
                      width: 120, background: "var(--bg-elevated)",
                      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
                      padding: "8px 12px", color: "var(--text)", fontSize: 13,
                    }}
                  />
                </div>
                {footageFiles.length > 10 && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--text-muted)", fontSize: 12 }}>
                    <Loader2 size={12} />
                    Batch mode: {footageFiles.length} clips will be analyzed with per-clip capping and deduplication.
                  </div>
                )}
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <input
                    type="checkbox"
                    id="preview-mode"
                    checked={previewMode}
                    onChange={e => setPreviewMode(e.target.checked)}
                    style={{ accentColor: "var(--accent)", width: 14, height: 14, cursor: "pointer" }}
                  />
                  <label htmlFor="preview-mode" style={{ fontSize: 12, color: "var(--text-muted)", cursor: "pointer" }}>
                    Preview mode (480×854, faster render — uncheck for full 1080×1920 quality)
                  </label>
                </div>
              </div>
            )}
          </div>

          {error && (
            <div style={{ background: "rgba(255,71,87,0.1)", border: "1px solid var(--error)", borderRadius: "var(--radius-sm)", padding: "12px 16px", color: "var(--error)", fontSize: 13 }}>
              {error}
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <button
              onClick={run}
              disabled={!canRun}
              data-testid="run-btn"
              style={{
                display: "flex", alignItems: "center", gap: 8,
                background: canRun ? "var(--accent)" : "var(--bg-elevated)",
                color: canRun ? "#fff" : "var(--text-dim)",
                border: "none", borderRadius: "var(--radius-sm)",
                padding: "14px 28px", fontSize: 15, fontWeight: 700,
                cursor: canRun ? "pointer" : "not-allowed", alignSelf: "flex-start",
              }}
            >
              <Film size={17} />
              Edit My Footage
              <ChevronRight size={16} />
            </button>
            {!canRun && (
              <p style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {!hasRef
                  ? (refTab === "url" ? "Add a reference TikTok URL above." : "Upload a reference video above.")
                  : "Upload at least one footage file."}
              </p>
            )}
          </div>
        </div>
      )}

      <style>{`.spin { animation: spin 1s linear infinite; } @keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}
