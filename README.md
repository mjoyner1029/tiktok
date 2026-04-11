# TikTok Style Engine

AI-powered SaaS backend that analyzes reference TikTok video styles and automatically produces draft-quality edited videos from raw clips.

**Core promise:** Upload a reference TikTok and your raw clip. Get a short-form draft in that style in under 5 minutes.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│  Next.js     │────▶│  FastAPI      │────▶│  Celery Workers    │
│  Frontend    │     │  API Server   │     │                    │
│  (Phase 2)   │     │  /api/v1/*    │     │  media  ai  render │
└──────────────┘     └──────┬───────┘     └────────┬───────────┘
                            │                      │
                     ┌──────┴───────┐        ┌─────┴──────────┐
                     │  PostgreSQL  │        │  Redis Queue    │
                     │  (data)      │        │  (jobs)         │
                     └──────────────┘        └────────────────┘
                                                    │
                     ┌──────────────┐        ┌──────┴──────┐
                     │  S3 / Local  │◀───────│  Services    │
                     │  Storage     │        │              │
                     └──────────────┘        │  Claude AI   │
                                             │  FFmpeg      │
                                             │  Whisper     │
                                             └──────────────┘
```

### Three Products Inside One SaaS

| Product | Description |
|---------|-------------|
| **Style Brain** | Learns pacing, caption style, hook style, shot rhythm, zoom rules from references |
| **Render Engine** | Takes a structured edit spec and outputs a draft video via FFmpeg |
| **Editor SaaS** | Preview, tweak, re-render, and export (Remotion in Phase 2) |

## Quick Start

### Docker (recommended)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY

# 2. Start everything
docker compose up -d

# 3. API is at http://localhost:8000
# 4. Docs at http://localhost:8000/docs
# 5. Flower (task monitor) at http://localhost:5555
```

### Local Development

```bash
# 1. Install system deps
brew install ffmpeg redis postgresql  # macOS

# 2. Python deps
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Start services
brew services start postgresql redis

# 4. Create DB
createdb tiktok_engine

# 5. Configure
cp .env.example .env  # edit ANTHROPIC_API_KEY

# 6. Run API
uvicorn app.main:app --reload

# 7. Run workers (separate terminals)
celery -A app.workers.celery_app worker -Q media -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q ai -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q render -c 1 --loglevel=info
```

### CLI Tool (standalone, no infrastructure needed)

```bash
export OPENAI_API_KEY="sk-..."
python cli.py \
  -r examples/reference_1.txt \
  -r examples/reference_2.txt \
  -c examples/content.txt \
  -o output.json
```

## API Reference

Base URL: `http://localhost:8000/api/v1`

### Projects

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/projects/` | Create a new project |
| `GET` | `/projects/` | List all projects |
| `GET` | `/projects/{id}` | Get project details |
| `PATCH` | `/projects/{id}` | Update project |
| `DELETE` | `/projects/{id}` | Delete project |
| `POST` | `/projects/{id}/analyze` | Trigger style analysis + edit spec generation |
| `POST` | `/projects/{id}/render` | Render from latest edit spec |
| `POST` | `/projects/{id}/pipeline` | Full pipeline: transcribe → analyze → render |
| `POST` | `/projects/{id}/revise` | Revise edit spec with feedback |
| `GET` | `/projects/{id}/specs` | List edit specs |
| `GET` | `/projects/{id}/renders` | List renders |
| `GET` | `/projects/{id}/styles` | List style profiles |
| `GET` | `/projects/{id}/jobs` | List jobs |

### Assets

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/assets/upload/{project_id}` | Upload video/audio/image |
| `GET` | `/assets/{project_id}` | List project assets |
| `GET` | `/assets/detail/{asset_id}` | Get asset detail |
| `DELETE` | `/assets/detail/{asset_id}` | Delete asset |
| `POST` | `/assets/transcribe/{asset_id}` | Transcribe single asset |
| `POST` | `/assets/transcribe-all/{project_id}` | Transcribe all pending assets |

### Renders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/renders/{id}` | Get render status |
| `GET` | `/renders/{id}/download` | Download rendered MP4 |
| `GET` | `/renders/{id}/thumbnail` | Download thumbnail |

### Styles

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/styles/` | List all saved styles |
| `GET` | `/styles/{id}` | Get style profile |
| `DELETE` | `/styles/{id}` | Delete style profile |

## Typical Workflow

```bash
# 1. Create project
curl -X POST localhost:8000/api/v1/projects/ \
  -H "Content-Type: application/json" \
  -d '{"title": "Morning Routine TikTok", "goal": "Make it feel like creator X energy"}'

# 2. Upload reference videos
curl -X POST localhost:8000/api/v1/assets/upload/{PROJECT_ID} \
  -F "file=@reference.mp4" -F "asset_type=reference_video"

# 3. Upload raw clips
curl -X POST localhost:8000/api/v1/assets/upload/{PROJECT_ID} \
  -F "file=@my_clip.mp4" -F "asset_type=raw_video"

# 4. Run full pipeline (transcribe → style extract → edit spec → render)
curl -X POST localhost:8000/api/v1/projects/{PROJECT_ID}/pipeline

# 5. Check job status
curl localhost:8000/api/v1/projects/{PROJECT_ID}/jobs

# 6. Download the rendered draft
curl localhost:8000/api/v1/renders/{RENDER_ID}/download -o draft.mp4

# 7. Request revision
curl -X POST localhost:8000/api/v1/projects/{PROJECT_ID}/revise \
  -H "Content-Type: application/json" \
  -d '{"feedback": "Make the cuts faster and add more zoom on keywords"}'

# 8. Re-render with revised spec
curl -X POST localhost:8000/api/v1/projects/{PROJECT_ID}/render
```

## Render Contract

Claude outputs a strict JSON edit spec — never freeform instructions. This is the machine-readable contract between AI and FFmpeg:

```json
{
  "project_id": "proj_123",
  "output": {
    "width": 1080, "height": 1920, "fps": 30, "duration_sec": 27.4
  },
  "tracks": {
    "video": [{
      "asset_id": "clip_1",
      "start": 0.0, "end": 2.1,
      "source_in": 14.3, "source_out": 16.4,
      "crop": "smart_center",
      "motion": {"type": "zoom_in", "strength": 0.08}
    }],
    "text": [{
      "start": 0.2, "end": 1.1,
      "text": "THIS IS WHY",
      "style": "bold_kinetic_1",
      "position": "lower_third",
      "animation": "pop"
    }],
    "audio": [{
      "asset_id": "music_1",
      "start": 0.0, "end": 27.4,
      "gain_db": -18,
      "duck_under_speech": true
    }]
  }
}
```

## FFmpeg Render Capabilities (V1)

| Feature | FFmpeg Filter / Method |
|---------|----------------------|
| Trim clips | `trim`, `atrim`, `-ss` |
| Stitch clips | `concat` demuxer |
| 9:16 smart crop | `scale` + `crop` |
| Caption burn-in | ASS subtitles via `ass` filter |
| Zoom / push | `zoompan` filter |
| Speed change | `setpts` + `atempo` |
| Audio mixing | `amix` + `sidechaincompress` |
| Silence removal | `silencedetect` → trim pipeline |
| Background music ducking | `sidechaincompress` |
| Export | 1080×1920 H.264 MP4 |

## Project Structure

```
tiktok/
├── app/
│   ├── main.py                  # FastAPI app + lifespan
│   ├── config.py                # Pydantic settings (env-driven)
│   ├── database.py              # Async SQLAlchemy engine + session
│   ├── prompts.py               # Claude prompt templates
│   ├── api/
│   │   ├── projects.py          # Project CRUD + pipeline triggers
│   │   ├── assets.py            # Upload + transcription endpoints
│   │   ├── renders.py           # Download + status endpoints
│   │   └── styles.py            # Style profile management
│   ├── models/
│   │   ├── db.py                # SQLAlchemy ORM (10 tables)
│   │   └── schemas.py           # Pydantic schemas + render contract
│   ├── services/
│   │   ├── ai_orchestrator.py   # Claude: style extraction, edit spec, revision
│   │   ├── media_analyzer.py    # FFprobe + Whisper + silence detection
│   │   ├── render_engine.py     # FFmpeg render pipeline
│   │   └── storage.py           # S3 / local file storage
│   └── workers/
│       ├── celery_app.py        # Celery config + queue routing
│       └── tasks.py             # Async tasks: transcribe, analyze, render
├── cli.py                       # Standalone CLI tool (no infra needed)
├── tiktok_engine/               # Original library (still works standalone)
├── migrations/                  # Alembic migration environment
├── examples/                    # Sample reference + content files
├── docker-compose.yml           # Full stack: API + workers + PG + Redis
├── Dockerfile
├── .env.example
├── requirements.txt
└── alembic.ini
```

## Database Schema

10 tables: `users` → `workspaces` → `projects` → `assets` / `style_profiles` / `edit_specs` / `renders` / `jobs` + `subscriptions`

```bash
# Migrations
alembic upgrade head                              # apply
alembic revision --autogenerate -m "description"  # create new
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Claude API key | required |
| `ANTHROPIC_MODEL` | Model name | `claude-sonnet-4-20250514` |
| `DATABASE_URL` | Postgres connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection | `redis://localhost:6379/0` |
| `STORAGE_BACKEND` | `local` or `s3` | `local` |
| `S3_BUCKET` | S3 bucket name | — |
| `WHISPER_MODEL` | Whisper model size | `base` |
| `EXPORT_WIDTH` / `EXPORT_HEIGHT` | Output resolution | 1080 × 1920 |

See [.env.example](.env.example) for the full list.

## Roadmap

### Phase 1: FFmpeg MVP (current)
- [x] FastAPI + async Postgres + Celery workers
- [x] Claude AI style extraction + edit spec generation
- [x] FFmpeg render engine (trim, crop, zoom, captions, audio mix)
- [x] Whisper transcription + silence detection
- [x] Docker Compose full stack
- [x] Asset upload + download + revision loop
- [ ] Saved style presets (reuse across projects)
- [ ] Music library integration
- [ ] Batch content generation
- [ ] A/B draft variants

### Phase 2: Remotion Editor
- [ ] Remotion preview player in app
- [ ] Timeline editing UI
- [ ] Branded templates
- [ ] Richer motion graphics

### Phase 3: SaaS Features
- [ ] Auth (Clerk / Auth0)
- [ ] Stripe billing (Starter $29 / Pro $99 / Agency $399+)
- [ ] Team workspaces + approval flows
- [ ] API access tier
- [ ] White-label agency portal
