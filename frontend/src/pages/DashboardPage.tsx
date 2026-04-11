import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus,
  Film,
  Trash2,
  Clock,
  FolderOpen,
} from "lucide-react";
import * as api from "../api";
import type { Project } from "../api";

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newTitle, setNewTitle] = useState("");
  const [newGoal, setNewGoal] = useState("");
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true);
    try {
      setProjects(await api.listProjects());
    } catch {
      // backend not running
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleCreate = async () => {
    if (!newTitle.trim()) return;
    try {
      const p = await api.createProject({
        title: newTitle.trim(),
        goal: newGoal.trim() || undefined,
      });
      navigate(`/project/${p.id}`);
    } catch (err) {
      console.error(err);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this project?")) return;
    try {
      await api.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.id !== id));
    } catch (err) {
      console.error(err);
    }
  };

  const formatDate = (iso: string) =>
    new Date(iso).toLocaleDateString("en-US", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });

  return (
    <>
      <div className="page-header flex justify-between items-center">
        <div>
          <h2>Projects</h2>
          <p>Create a project to start editing TikTok-style videos with AI</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          <Plus size={18} /> New Project
        </button>
      </div>

      {loading ? (
        <div className="empty-state">
          <div className="spinner spinner-lg" />
        </div>
      ) : projects.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon"><FolderOpen size={48} /></div>
          <h3>No projects yet</h3>
          <p>Create your first project to start analyzing TikTok styles and generating edits.</p>
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
            <Plus size={18} /> Create Project
          </button>
        </div>
      ) : (
        <div className="card-grid">
          {projects.map((p) => (
            <div
              key={p.id}
              className="card"
              style={{ cursor: "pointer" }}
              onClick={() => navigate(`/project/${p.id}`)}
            >
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                  <Film size={20} style={{ color: "var(--accent)" }} />
                  <h3 style={{ fontSize: 16, fontWeight: 600 }}>{p.title}</h3>
                </div>
                <span className={`badge badge-${p.status}`}>{p.status}</span>
              </div>
              {p.goal && (
                <p className="text-sm text-muted" style={{ marginBottom: 12 }}>{p.goal}</p>
              )}
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted flex items-center gap-2">
                  <Clock size={14} /> {formatDate(p.created_at)}
                </span>
                <button
                  className="btn btn-ghost btn-sm btn-icon"
                  onClick={(e) => { e.stopPropagation(); handleDelete(p.id); }}
                  title="Delete project"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Create Project Modal ──────────────────────────────────────── */}
      {showCreate && (
        <div className="modal-overlay" onClick={() => setShowCreate(false)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>New Project</h3>
            <div className="form-group">
              <label>Title</label>
              <input
                className="form-input"
                placeholder="e.g. Morning Routine TikTok"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                autoFocus
                onKeyDown={(e) => e.key === "Enter" && handleCreate()}
              />
            </div>
            <div className="form-group">
              <label>Goal / Prompt</label>
              <textarea
                className="form-textarea"
                placeholder="Describe what this TikTok should be about...&#10;e.g. A 30-second hook-driven video about morning productivity"
                value={newGoal}
                onChange={(e) => setNewGoal(e.target.value)}
              />
            </div>
            <div className="flex gap-3" style={{ justifyContent: "flex-end" }}>
              <button className="btn btn-secondary" onClick={() => setShowCreate(false)}>
                Cancel
              </button>
              <button className="btn btn-primary" onClick={handleCreate} disabled={!newTitle.trim()}>
                Create Project
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
