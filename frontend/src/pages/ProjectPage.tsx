import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
} from "lucide-react";
import * as api from "../api";
import type { Project, Asset, EditSpec, StyleProfile, Render, Job } from "../api";
import StepReference from "../components/StepReference";
import StepUpload from "../components/StepUpload";
import StepEditSpec from "../components/StepEditSpec";
import StepExport from "../components/StepExport";

const STEPS = ["Reference", "Upload", "Edit & Captions", "Export"];

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [specs, setSpecs] = useState<EditSpec[]>([]);
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [renders, setRenders] = useState<Render[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    try {
      const [p, a, sp, st, r, j] = await Promise.all([
        api.getProject(projectId),
        api.listAssets(projectId),
        api.listEditSpecs(projectId),
        api.listStyles(projectId),
        api.listRenders(projectId),
        api.listJobs(projectId),
      ]);
      setProject(p);
      setAssets(a);
      setSpecs(sp);
      setStyles(st);
      setRenders(r);
      setJobs(j);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    refresh();
    // Poll every 4s for status updates
    const id = setInterval(refresh, 4000);
    return () => clearInterval(id);
  }, [refresh]);

  // Determine which steps are "completed"
  const referenceAssets = assets.filter((a) => a.type === "reference_video");
  const userAssets = assets.filter((a) => a.type !== "reference_video");
  const latestSpec = specs[0] || null;
  const latestRender = renders[0] || null;
  const completedSteps = [
    referenceAssets.length > 0 || styles.length > 0,
    userAssets.length > 0,
    latestSpec !== null,
    latestRender?.status === "completed",
  ];

  if (loading) {
    return (
      <div className="empty-state">
        <div className="spinner spinner-lg" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="empty-state">
        <h3>Project not found</h3>
        <button className="btn btn-secondary mt-4" onClick={() => navigate("/")}>
          Back to Dashboard
        </button>
      </div>
    );
  }

  return (
    <>
      {/* Header */}
      <div className="page-header">
        <div className="flex items-center gap-3 mb-4">
          <button className="btn btn-ghost btn-icon" onClick={() => navigate("/")}>
            <ArrowLeft size={20} />
          </button>
          <div>
            <h2>{project.title}</h2>
            {project.goal && <p>{project.goal}</p>}
          </div>
        </div>
      </div>

      {/* Stepper */}
      <div className="stepper">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`stepper-step ${i === step ? "active" : ""} ${completedSteps[i] ? "completed" : ""}`}
            onClick={() => setStep(i)}
          >
            <span className="step-number">
              {completedSteps[i] ? <CheckCircle2 size={14} /> : i + 1}
            </span>
            {s}
          </div>
        ))}
      </div>

      {/* Step Content */}
      {step === 0 && (
        <StepReference
          projectId={project.id}
          assets={referenceAssets}
          styles={styles}
          jobs={jobs}
          onRefresh={refresh}
          onNext={() => setStep(1)}
        />
      )}
      {step === 1 && (
        <StepUpload
          projectId={project.id}
          assets={userAssets}
          onRefresh={refresh}
          onNext={() => setStep(2)}
        />
      )}
      {step === 2 && (
        <StepEditSpec
          projectId={project.id}
          spec={latestSpec}
          allSpecs={specs}
          jobs={jobs}
          onRefresh={refresh}
          onNext={() => setStep(3)}
        />
      )}
      {step === 3 && (
        <StepExport
          projectId={project.id}
          render={latestRender}
          renders={renders}
          jobs={jobs}
          onRefresh={refresh}
        />
      )}
    </>
  );
}
