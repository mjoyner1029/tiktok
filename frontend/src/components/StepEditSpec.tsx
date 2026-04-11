import { useState } from "react";
import {
  Sparkles,
  Send,
  Plus,
  Trash2,
  ArrowRight,
  Film,
  Type,
  Music,
  RotateCcw,
  Wand2,
} from "lucide-react";
import * as api from "../api";
import type { EditSpec, Job } from "../api";

interface Props {
  projectId: string;
  spec: EditSpec | null;
  allSpecs: EditSpec[];
  jobs: Job[];
  onRefresh: () => void;
  onNext: () => void;
}

interface TextClip {
  start: string;
  end: string;
  text: string;
  style: string;
  position: string;
}

export default function StepEditSpec({
  projectId, spec, allSpecs, jobs, onRefresh, onNext,
}: Props) {
  const [generating, setGenerating] = useState(false);
  const [revising, setRevising] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"visual" | "json">("visual");


  // Editable captions — initialize from spec
  const specJson = spec?.spec_json as any;
  const textTracks: TextClip[] = specJson?.tracks?.text || [];
  const videoTracks = specJson?.tracks?.video || [];
  const audioTracks = specJson?.tracks?.audio || [];

  const [editedCaptions, setEditedCaptions] = useState<TextClip[]>(textTracks);
  const [userText, setUserText] = useState("");

  const analysisJobs = jobs.filter(
    (j) => j.type === "analyze_style" && (j.status === "pending" || j.status === "running")
  );
  const isAnalyzing = analysisJobs.length > 0;

  // Generate edit spec from AI
  const handleGenerate = async () => {
    setError("");
    setGenerating(true);
    try {
      await api.startAnalysis(projectId);
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Generation failed");
    } finally {
      setGenerating(false);
    }
  };

  // Revise with feedback
  const handleRevise = async () => {
    if (!feedback.trim()) return;
    setError("");
    setRevising(true);
    try {
      await api.reviseEditSpec(projectId, feedback.trim());
      setFeedback("");
      onRefresh();
    } catch (err: any) {
      setError(err?.response?.data?.detail || "Revision failed");
    } finally {
      setRevising(false);
    }
  };

  // Caption editing
  const updateCaption = (index: number, field: keyof TextClip, value: string) => {
    setEditedCaptions((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  };

  const addCaption = () => {
    const last = editedCaptions[editedCaptions.length - 1];
    const start = last ? parseFloat(last.end) || 0 : 0;
    setEditedCaptions((prev) => [
      ...prev,
      { start: start.toFixed(1), end: (start + 2).toFixed(1), text: "", style: "bold_kinetic_1", position: "lower_third" },
    ]);
  };

  const removeCaption = (index: number) => {
    setEditedCaptions((prev) => prev.filter((_, i) => i !== index));
  };

  // If no spec yet
  if (!spec) {
    return (
      <div>
        <div className="section">
          <div className="section-header">
            <h3>Edit Plan & Captions</h3>
          </div>
          <div className="empty-state">
            <div className="empty-state-icon"><Wand2 size={48} /></div>
            <h3>No edit plan yet</h3>
            <p>
              The AI will analyze your reference videos and create a complete edit plan
              with cuts, zoom patterns, captions, and audio mixing.
            </p>
            <button
              className="btn btn-primary btn-lg"
              onClick={handleGenerate}
              disabled={generating || isAnalyzing}
            >
              {isAnalyzing ? (
                <><span className="spinner" /> AI is analyzing...</>
              ) : (
                <><Sparkles size={18} /> Generate Edit Plan</>
              )}
            </button>
            {error && <p className="text-error text-sm mt-4">{error}</p>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="section">
        <div className="section-header">
          <h3>Edit Plan v{spec.version}</h3>
          <div className="flex gap-2">
            <button
              className={`btn btn-sm ${tab === "visual" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setTab("visual")}
            >
              Visual
            </button>
            <button
              className={`btn btn-sm ${tab === "json" ? "btn-primary" : "btn-secondary"}`}
              onClick={() => setTab("json")}
            >
              JSON
            </button>
          </div>
        </div>

        {spec.revision_note && (
          <p className="text-sm text-muted mb-4">
            <RotateCcw size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
            Last revision: "{spec.revision_note}"
          </p>
        )}
      </div>

      {tab === "json" ? (
        <div className="section">
          <div className="json-viewer">{JSON.stringify(specJson, null, 2)}</div>
        </div>
      ) : (
        <>
          {/* ── Video Track ─────────────────────────────────────────── */}
          <div className="section">
            <div className="timeline-track">
              <h4><Film size={14} style={{ marginRight: 6, verticalAlign: -2 }} /> Video Track ({videoTracks.length} clips)</h4>
              {videoTracks.length === 0 ? (
                <p className="text-sm text-muted">No video clips in spec</p>
              ) : (
                videoTracks.map((clip: any, i: number) => (
                  <div key={i} className="clip-row">
                    <span className="clip-time">
                      {Number(clip.start).toFixed(1)}s — {Number(clip.end).toFixed(1)}s
                    </span>
                    <span className="clip-label">
                      {clip.asset_id}
                    </span>
                    <span className="clip-meta">
                      {clip.crop} · {clip.motion?.type || "static"}
                      {clip.speed && clip.speed !== 1 ? ` · ${clip.speed}×` : ""}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* ── Caption Track / Text Editor ─────────────────────────── */}
          <div className="section">
            <div className="section-header">
              <h3>
                <Type size={18} style={{ marginRight: 8, verticalAlign: -3 }} />
                Captions & Text
              </h3>
              <button className="btn btn-secondary btn-sm" onClick={addCaption}>
                <Plus size={14} /> Add Caption
              </button>
            </div>
            <p className="text-sm text-muted mb-4">
              Edit the text, timing, and position of each caption. The AI generated these from your content.
            </p>

            {editedCaptions.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <p>No captions yet. Add some or let the AI generate them.</p>
              </div>
            ) : (
              editedCaptions.map((cap, i) => (
                <div key={i} className="caption-row">
                  <div className="caption-time-inputs">
                    <input
                      value={cap.start}
                      onChange={(e) => updateCaption(i, "start", e.target.value)}
                      placeholder="0.0"
                      title="Start time"
                    />
                    <input
                      value={cap.end}
                      onChange={(e) => updateCaption(i, "end", e.target.value)}
                      placeholder="2.0"
                      title="End time"
                    />
                  </div>
                  <div className="caption-text-input">
                    <input
                      value={cap.text}
                      onChange={(e) => updateCaption(i, "text", e.target.value)}
                      placeholder="Caption text..."
                    />
                  </div>
                  <select
                    className="form-select"
                    style={{ width: 140, padding: "6px 8px", fontSize: 12 }}
                    value={cap.position}
                    onChange={(e) => updateCaption(i, "position", e.target.value)}
                  >
                    <option value="center">Center</option>
                    <option value="lower_third">Lower Third</option>
                    <option value="upper_third">Upper Third</option>
                    <option value="top">Top</option>
                    <option value="bottom">Bottom</option>
                  </select>
                  <button className="btn btn-ghost btn-sm btn-icon" onClick={() => removeCaption(i)}>
                    <Trash2 size={14} />
                  </button>
                </div>
              ))
            )}
          </div>

          {/* ── User Text Input ─────────────────────────────────────── */}
          <div className="section">
            <div className="section-header">
              <h3>Your Text / Script</h3>
            </div>
            <p className="text-sm text-muted mb-4">
              Enter or paste your own text. The AI can incorporate this into the edit plan
              and generate captions from it.
            </p>
            <textarea
              className="form-textarea"
              placeholder="Type your script, talking points, or caption text here...&#10;&#10;e.g. You won't BELIEVE what happened next...&#10;This simple trick changed everything."
              value={userText}
              onChange={(e) => setUserText(e.target.value)}
              rows={5}
            />
          </div>

          {/* ── Audio Track ─────────────────────────────────────────── */}
          <div className="section">
            <div className="timeline-track">
              <h4><Music size={14} style={{ marginRight: 6, verticalAlign: -2 }} /> Audio Track ({audioTracks.length} clips)</h4>
              {audioTracks.length === 0 ? (
                <p className="text-sm text-muted">No audio clips in spec</p>
              ) : (
                audioTracks.map((clip: any, i: number) => (
                  <div key={i} className="clip-row">
                    <span className="clip-time">
                      {Number(clip.start).toFixed(1)}s — {Number(clip.end).toFixed(1)}s
                    </span>
                    <span className="clip-label">{clip.asset_id}</span>
                    <span className="clip-meta">
                      {clip.gain_db !== 0 ? `${clip.gain_db}dB` : "0dB"}
                      {clip.duck_under_speech ? " · ducked" : ""}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}

      {/* ── Revision ──────────────────────────────────────────────── */}
      <div className="section">
        <div className="section-header">
          <h3>Revise with AI</h3>
        </div>
        <p className="text-sm text-muted mb-4">
          Tell the AI what to change — cut style, pacing, caption wording, zoom intensity, etc.
        </p>
        <div className="url-input-bar">
          <input
            placeholder='e.g. "Make cuts faster, add more zoom-ins, change captions to ALL CAPS"'
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleRevise()}
          />
          <button
            className="btn btn-primary"
            onClick={handleRevise}
            disabled={!feedback.trim() || revising}
          >
            {revising ? <span className="spinner" /> : <Send size={16} />}
            Revise
          </button>
        </div>
        {error && <p className="text-error text-sm mt-4">{error}</p>}

        {allSpecs.length > 1 && (
          <p className="text-sm text-muted mt-4">
            {allSpecs.length} versions available — currently viewing v{spec.version}
          </p>
        )}
      </div>

      {/* Navigation */}
      <div className="flex justify-between mt-6">
        <div />
        <button className="btn btn-primary" onClick={onNext}>
          Next: Export <ArrowRight size={16} />
        </button>
      </div>
    </div>
  );
}
