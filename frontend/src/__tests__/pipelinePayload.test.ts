/**
 * Tests that startPipeline() builds the correct FormData fields.
 * Axios is mocked so no real HTTP request is made.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import axios from "axios";
import { startPipeline } from "../api";
import type { PipelineStartOptions } from "../api";

vi.mock("axios");
const mockedPost = vi.mocked(axios.post);

const makeFile = (name: string, type = "video/mp4") =>
  new File(["x"], name, { type });

const makeAudioFile = (name: string) =>
  new File(["x"], name, { type: "audio/mpeg" });

const BASE_OPTS: PipelineStartOptions = {
  referenceUrls: ["https://www.tiktok.com/@a/video/1"],
  referenceFiles: [],
  footageFiles: [makeFile("clip1.mp4"), makeFile("clip2.mp4")],
  musicFile: null,
  audioMode: "reference_audio",
  audioVolume: -18,
  originalAudioVolume: 0,
  rhythmPreset: "loose_sync",
  contentHint: "morning routine",
  preview: true,
};

const MOCK_RESULT = {
  project_id: "proj-123",
  video_url: "/api/v1/pipeline/video/proj-123/preview.mp4",
  audio_mode: "reference_audio",
  rhythm_preset: "loose_sync",
  bpm: 120,
  beat_count: 32,
  timeline_duration: 15.0,
  clip_count: 4,
};

beforeEach(() => {
  vi.clearAllMocks();
  mockedPost.mockResolvedValue({ data: MOCK_RESULT });
});

describe("startPipeline – FormData construction", () => {
  it("appends reference_url", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.getAll("reference_url")).toEqual(["https://www.tiktok.com/@a/video/1"]);
  });

  it("appends one footage file per item", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.getAll("footage")).toHaveLength(2);
  });

  it("sends audio_mode and rhythm_preset", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("audio_mode")).toBe("reference_audio");
    expect(form.get("rhythm_preset")).toBe("loose_sync");
  });

  it("sends audio_volume as string", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("audio_volume")).toBe("-18");
  });

  it("sends original_audio_volume as string", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("original_audio_volume")).toBe("0");
  });

  it("sends content_hint", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("content_hint")).toBe("morning routine");
  });

  it("sends preview=true", async () => {
    await startPipeline(BASE_OPTS);
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("preview")).toBe("true");
  });

  it("does NOT append music_file when opts.musicFile is null", async () => {
    await startPipeline({ ...BASE_OPTS, musicFile: null });
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("music_file")).toBeNull();
  });

  it("appends music_file when provided", async () => {
    const music = makeAudioFile("song.mp3");
    await startPipeline({ ...BASE_OPTS, musicFile: music });
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.get("music_file")).toBe(music);
  });

  it("uses reference files instead of URLs in file mode", async () => {
    const refFile = makeFile("ref.mp4");
    await startPipeline({ ...BASE_OPTS, referenceUrls: [], referenceFiles: [refFile] });
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.getAll("reference_url")).toHaveLength(0);
    expect(form.get("reference_file")).toBe(refFile);
  });

  it("filters out non-http reference URLs", async () => {
    await startPipeline({ ...BASE_OPTS, referenceUrls: ["not-a-url", "https://tiktok.com/@x/video/2"] });
    const [, form] = mockedPost.mock.calls[0] as [string, FormData, ...unknown[]];
    expect(form.getAll("reference_url")).toEqual(["https://tiktok.com/@x/video/2"]);
  });

  it("calls onStage callback with 'analyzing_style'", async () => {
    const onStage = vi.fn();
    await startPipeline(BASE_OPTS, onStage);
    expect(onStage).toHaveBeenCalledWith("analyzing_style");
  });

  it("returns the API response data", async () => {
    const result = await startPipeline(BASE_OPTS);
    expect(result.project_id).toBe("proj-123");
    expect(result.bpm).toBe(120);
  });
});
