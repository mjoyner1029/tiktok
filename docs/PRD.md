# Product Requirements Document — TikTok Style Engine

**Version:** 1.0  
**Date:** 2026-05-14  
**Status:** Ready for development

---

## 1. Problem Statement

Solo short-form video creators lack the editing skill or time to produce content that consistently matches a specific visual style. Existing tools (CapCut, Opus Clip, Submagic) offer generic AI editing but cannot learn and replicate a creator's *own* established style across new raw footage.

---

## 2. Target User

**Primary:** Solo content creator posting short-form video (TikTok, Reels, Shorts) who:
- Has an existing body of work with a recognizable editing style
- Shoots raw footage but struggles to maintain stylistic consistency at speed
- Is comfortable with a chat-based interface
- Handles audio and captions manually

**Also valid:** The developer themselves, used as a personal tool and test user throughout development.

---

## 3. Core Value Proposition

> Upload your raw clips and a reference video — the engine extracts that editing style and applies it to your footage automatically.

Two equally supported reference modes:

1. **Personal style cloning** — the creator uploads their own past videos as the reference. The system learns their established style and reproduces it consistently on new footage. Legally clean, technically reliable.
2. **External style matching** — the creator provides another creator's video (via file upload or TikTok URL) as a style target. The system replicates the editing approach on the user's own raw clips. Legally gray for URL scraping at commercial scale; file upload is the safer path.

The differentiator from CapCut/Opus Clip/Submagic is **explicit style reference input**: the user controls exactly whose style is being cloned, rather than accepting a generic AI edit.

---

## 4. Feature Scope

### 4.1 In Scope (MVP)

| Feature | Description |
|---|---|
| **Style Reference Input** | Upload any edited video file (own or another creator's) OR provide a TikTok URL as the style reference |
| **Raw Clip Input** | Upload one or more raw video clips to be assembled |
| **Style Analysis** | Extract and encode: cut timing/pacing, transition types, color grade, text overlay placement |
| **Automated Assembly** | Apply extracted style to raw clips — produce a draft edited video |
| **Color Grading** | Match color grade of reference to output clips |
| **Chat Revision Loop** | Natural language revision requests ("make the cuts faster", "warmer color tone") |
| **Preview Window** | In-app video preview before downloading |
| **MP4 Download** | Export final video as a downloadable MP4 file |
| **Monthly Subscription** | Gated access via subscription billing |

### 4.2 Explicitly Out of Scope (MVP)

- Caption generation or overlay (user handles manually)
- Audio selection, mixing, or syncing (user handles manually)
- Direct publishing to TikTok or any platform
- Timeline / keyframe editor (chat revisions are the only correction mechanism)
- Batch rendering multiple videos from one style
- A/B variant generation
- Enterprise/team accounts

---

## 5. User Flow

```
1. User logs in / subscribes
2. User starts a new project via chat
3. User provides style reference:
   a. Uploads a previously edited video file, OR
   b. Pastes a TikTok URL (scraping — see risk section)
4. System analyzes style: pacing, transitions, color grade, text placement
5. User uploads raw clips
6. System assembles draft video matching the extracted style
7. Preview is shown in the chat interface
8. User requests revisions in natural language → system re-renders
9. Steps 7–8 repeat until user is satisfied
10. User downloads finished MP4
```

---

## 6. Style Analysis — Technical Requirements

The following style dimensions must be extracted from the reference video and applied to output:

| Dimension | What to Extract | How to Apply |
|---|---|---|
| **Cut timing / pacing** | Average shot duration, cuts-per-minute, beat alignment | Trim and sequence raw clips to match rhythm |
| **Transition types** | Hard cut, fade, whip pan, zoom punch — frequency and position | Apply matching transitions at cut points |
| **Color grade** | LUT estimation: shadows/highlights/saturation/hue shift | Apply FFmpeg color filter chain to output clips |
| **Text overlay placement** | Position (top/center/bottom), size, timing relative to cut | Apply placeholder text overlays at matched positions/timings |

**Note on TikTok URL input:** Platform re-encoding degrades color accuracy. Color grade matching from URL-sourced references should be treated as approximate. File upload is the preferred path for accurate style matching — for both personal and external references.

**Note on external creator references:** Using another creator's video as a style reference is a feature, not a workaround. The system copies *editing technique* (timing, color, transitions), not content. This is analogous to learning from a style — legally acceptable. Do not reproduce, redistribute, or store the reference creator's video content.

---

## 7. Chat Interface Requirements

- Single conversation thread per project
- System messages must explain what was analyzed and what was applied
- Revision requests must be interpreted as natural language (no command syntax required)
- Preview must be embedded/playable in the chat window, not just a link
- Each revision produces a new versioned render (v1, v2, v3...)
- User can revert to a previous version by referencing it in chat

---

## 8. Business Model

- **Monthly subscription** — single tier for MVP
- Subscription gates all rendering functionality
- Free tier (optional, post-MVP): limited renders per month

---

## 9. Risks & Constraints

| Risk | Severity | Mitigation |
|---|---|---|
| TikTok URL scraping blocked / ToS violation | High | Clearly label URL input as best-effort; prioritize file upload UX; do not build commercial marketing around scraping |
| Color grade accuracy from re-encoded video | Medium | Set user expectation: "approximate match"; allow revision via chat |
| Style analysis quality on short/unusual reference clips | Medium | Require minimum reference duration (suggest ≥ 30s); surface warnings |
| FFmpeg rendering performance at scale | Medium | Queue renders async; show progress in chat |
| Scope creep (captions, audio, publishing) | High | Hard no in MVP; revisit post-launch based on user feedback |

---

## 10. What Already Exists in the Codebase

The following infrastructure is already built and should be leveraged, not rebuilt:

- JWT authentication (`app/auth.py`)
- Stripe billing (`app/services/billing.py`)
- Chat processor (`app/services/chat_processor.py`)
- AI orchestrator (`app/services/ai_orchestrator.py`)
- Media analyzer (`app/services/media_analyzer.py`)
- Render engine (`app/services/render_engine.py`)
- Style presets (`app/services/style_presets.py`)
- Redis caching, async workers, Kubernetes manifests

**Priority:** Wire the existing services together to fulfill the user flow in Section 5 before adding any new infrastructure.

---

## 11. Success Metrics (MVP)

| Metric | Target |
|---|---|
| Style analysis → render pipeline works end-to-end | Required for launch |
| Chat revision loop produces a changed render | Required for launch |
| User can download a valid MP4 | Required for launch |
| Color grade visually resembles reference | Subjective; developer self-assessment |
| Cut timing within ±15% of reference pacing | Measurable via FFmpeg analysis |

---

## 12. Out of Scope — Infrastructure Already Overbuilt for MVP

The following exist in the codebase but are **not required for MVP** and should not consume development time:

- Kubernetes deployment (develop locally/Docker first)
- Prometheus metrics
- Sentry error tracking
- Batch processing / A/B variants
- Enterprise billing tiers
