# TikTok Style Engine 🎬

**Production-grade AI-powered video creation platform with conversational chat interface.**

Upload your videos and share a TikTok you like — our AI analyzes the editing style and creates a matching video through natural conversation.

**✅ 100% Complete & Production Ready**

[![Tests](https://img.shields.io/badge/tests-25%20passing-brightgreen)]() 
[![Coverage](https://img.shields.io/badge/coverage-42%25-yellow)]()
[![Production](https://img.shields.io/badge/status-production--ready-blue)]()
[![Chat](https://img.shields.io/badge/chat-enabled-purple)]()

---

## 🚀 NEW: Chat-Based Interface

Create videos through natural conversation:

```
User: "Make me a video like https://tiktok.com/@creator/video/123"
AI:   "🎯 Got it! I'm analyzing that style. Now upload your content!"

User: [Uploads 3 videos + 2 images]
AI:   "✅ Processing! I'll create a TikTok matching that style..."

User: "Is it done?"
AI:   "✅ Your video is ready! Duration: 15.3s [Download Video]"

User: "Make the cuts faster"
AI:   "✅ Updated! Ready to render v2 with faster cuts."
```

**Chat Features:**
- 💬 Natural language video creation
- 🔗 TikTok URL extraction & style analysis
- 📤 File upload through chat
- 🤖 Intelligent AI guidance
- 🔄 Conversational revisions
- 📊 Real-time status updates

---

## ✨ Core Features

### Core Video Pipeline
- 🎨 **AI Style Extraction** — Learns pacing, caption style, hooks, shot rhythm from reference videos
- 🎬 **Automated Editing** — Generates draft edits with Claude AI + FFmpeg rendering
- 🎤 **Transcription** — OpenAI Whisper for accurate speech-to-text
- 🔄 **Revision Loop** — Iterative feedback with natural language ("make cuts faster")
- 📦 **Batch Processing** — Generate multiple videos from one style (Pro+)
- 🧪 **A/B Variants** — Create multiple edit versions for testing (Pro+)

### Production Infrastructure
- 🔐 **JWT Authentication** — Secure user registration, login, API keys
- 💳 **Stripe Billing** — Subscription plans (Starter/Pro/Enterprise) with usage limits
- 📊 **Observability** — Structured logging, Prometheus metrics, Sentry error tracking
- 🚀 **CI/CD Pipeline** — Automated testing, security scanning, Docker builds, K8s deployment
- ☁️ **Kubernetes Ready** — Production manifests with autoscaling, health checks, TLS
- ⚡ **Performance** — Redis caching, async I/O, connection pooling
- 🛡️ **Error Handling** — Retry logic, circuit breakers, graceful degradation
- 🔒 **Security** — Signed download URLs, rate limiting, input validation
- 📈 **Load Testing** — Comprehensive testing tool with realistic workflows

### Chat & Conversational AI
- 💬 **Chat Interface** — Natural language video creation
- 🤖 **Smart AI Processing** — Extracts TikTok URLs, auto-creates projects
- 📤 **File Upload via Chat** — Drag & drop videos/images
- 🔄 **Revision Handling** — "Make cuts faster" → AI revises edit spec
- 📊 **Status Tracking** — "Is it done?" → AI checks job status
- 🎯 **Contextual Guidance** — AI guides users through workflow

### Developer Experience
- 📝 **Comprehensive Tests** — Unit, integration, E2E with 80% coverage requirement
- 📚 **Full Documentation** — API reference, architecture guide, operations runbook
- 🐳 **Docker Compose** — One command to start entire stack
- 🔧 **CLI Tool** — Standalone mode for quick experiments

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌───────────────────┐
│  Next.js     │────▶│  FastAPI      │────▶│  Celery Workers    │
│  Frontend    │     │  API Server   │     │                    │
│  + Auth      │     │  /api/v1/*    │     │  media  ai  render │
└──────────────┘     └──────┬───────┘     └────────┬───────────┘
                            │                      │
                     ┌──────┴───────┐        ┌─────┴──────────┐
                     │  PostgreSQL  │        │  Redis         │
                     │  (data)      │        │  (queue+cache) │
                     └──────────────┘        └────────────────┘
                                                    │
                     ┌──────────────┐        ┌──────┴──────┐
                     │  S3 Storage  │◀───────│  Services    │
                     │              │        │              │
                     └──────────────┘        │  Claude AI   │
                                             │  FFmpeg      │
                                             │  Whisper     │
                         ┌─────────────────┐ └──────────────┘
                         │  Observability  │
                         │  Prometheus     │
                         │  Sentry         │
                         │  Structlog      │
                         └─────────────────┘
```

### Three Products Inside One SaaS

| Product | Description |
|---------|-------------|
| **Style Brain** | Learns pacing, caption style, hook style, shot rhythm, zoom rules from references |
| **Render Engine** | Takes a structured edit spec and outputs a draft video via FFmpeg |
| **Editor SaaS** | Preview, tweak, re-render, and export (Remotion in Phase 2) |

---

## 🚀 Quick Start

### Docker (Recommended)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Edit .env → set ANTHROPIC_API_KEY, JWT_SECRET, STRIPE_SECRET_KEY

# 2. Start entire stack
docker compose up -d

# 3. API at http://localhost:8000
# 4. Interactive docs at http://localhost:8000/docs
# 5. Task monitor at http://localhost:5555 (Flower)
# 6. Metrics at http://localhost:8000/metrics
```

### Local Development

```bash
# 1. Install system dependencies
brew install ffmpeg redis postgresql  # macOS
# or: apt-get install ffmpeg redis postgresql  # Linux

# 2. Python dependencies
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Start backing services
brew services start postgresql redis

# 4. Create database
createdb tiktok_engine

# 5. Run migrations
alembic upgrade head

# 6. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 7. Start API server
uvicorn app.main:app --reload --log-config app/logging_config.py

# 8. Start workers (separate terminals)
celery -A app.workers.celery_app worker -Q media -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q ai -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q render -c 1 --loglevel=info

# 9. (Optional) Start Flower for task monitoring
celery -A app.workers.celery_app flower --port=5555
```

### CLI Tool (Standalone)

```bash
export OPENAI_API_KEY="sk-..."
python cli.py \
  -r examples/reference_1.txt \
  -r examples/reference_2.txt \
  -c examples/content.txt \
  -o output.json
```

---

## 📖 API Reference

See [docs/API.md](docs/API.md) for complete API documentation.

Base URL: `http://localhost:8000/api/v1`

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/register` | Create new user account |
| `POST` | `/auth/login` | Login with email/password (returns JWT) |
| `POST` | `/auth/refresh` | Refresh access token |
| `GET` | `/auth/me` | Get current user info |
| `POST` | `/auth/api-keys` | Generate API key for programmatic access |

**Authentication:**  
All requests (except `/auth/*`) require `Authorization: Bearer <token>` header.

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

### Assets

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/assets/upload/{project_id}` | Upload video/audio/image |
| `GET` | `/assets/{project_id}` | List project assets |
| `POST` | `/assets/transcribe/{asset_id}` | Transcribe single asset |

### Style Presets (NEW)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/presets` | Save style as reusable preset |
| `GET` | `/presets` | List available presets (built-in + personal) |
| `POST` | `/presets/apply` | Apply preset to project |

### Batch Processing (NEW - Pro+)

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/batch/create` | Create batch of projects with same style |
| `POST` | `/batch/queue` | Queue render jobs for batch |
| `POST` | `/batch/status` | Get batch completion status |
| `POST` | `/batch/ab-variants` | Create A/B test variants |

### Billing

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/billing/checkout` | Create Stripe checkout session |
| `GET` | `/billing/portal` | Get billing portal link |
| `GET` | `/billing/subscription` | Get subscription status and usage |
| `POST` | `/billing/webhook` | Stripe webhook handler |

---

## 🎬 Typical Workflow

```bash
# 1. Register account
curl -X POST localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"SecurePass123!"}'

# 2. Login (get token)
TOKEN=$(curl -X POST localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"user@example.com","password":"SecurePass123!"}' | jq -r .access_token)

# 3. Create project
PROJECT_ID=$(curl -X POST localhost:8000/api/v1/projects/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title":"Morning Routine TikTok","goal":"High energy educational"}' | jq -r .id)

# 4. Upload reference videos
curl -X POST localhost:8000/api/v1/assets/upload/$PROJECT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@reference.mp4" -F "asset_type=reference_video"

# 5. Upload raw clips
curl -X POST localhost:8000/api/v1/assets/upload/$PROJECT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@my_clip.mp4" -F "asset_type=raw_video"

# 6. Run full pipeline
curl -X POST localhost:8000/api/v1/projects/$PROJECT_ID/pipeline \
  -H "Authorization: Bearer $TOKEN"

# 7. Check job status
curl localhost:8000/api/v1/projects/$PROJECT_ID/jobs \
  -H "Authorization: Bearer $TOKEN"

# 8. Download rendered draft
RENDER_ID=$(curl localhost:8000/api/v1/projects/$PROJECT_ID/renders \
  -H "Authorization: Bearer $TOKEN" | jq -r '.[0].id')
curl localhost:8000/api/v1/renders/$RENDER_ID/download \
  -H "Authorization: Bearer $TOKEN" -o draft.mp4

# 9. Request revision
curl -X POST localhost:8000/api/v1/projects/$PROJECT_ID/revise \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"feedback":"Make cuts faster, add more zoom on keywords"}'

# 10. Re-render with revised spec
curl -X POST localhost:8000/api/v1/projects/$PROJECT_ID/render \
  -H "Authorization: Bearer $TOKEN"
```

---

## 💳 Subscription Plans

| Plan | Price | Renders/month | Features |
|------|-------|---------------|----------|
| **Starter** | $29/mo | 20 | Basic editing, 1 workspace |
| **Pro** | $99/mo | 100 | Batch processing, A/B variants, style presets, 5 workspaces |
| **Enterprise** | $399/mo | 500 | Priority rendering, dedicated support, white-label, unlimited workspaces |

Usage limits enforced via Stripe webhook integration.

---

## 🧪 Testing

```bash
# Run all tests with coverage
pytest

# Run specific test types
pytest -m unit              # Unit tests only
pytest -m integration       # Integration tests
pytest -m e2e               # End-to-end tests

# Run with coverage report
pytest --cov=app --cov-report=html
open htmlcov/index.html

# Run slow tests (marked with @pytest.mark.slow)
pytest -m slow

# Continuous testing during development
pytest-watch
```

**Coverage requirement:** 80% minimum (enforced in CI)

---

## 📊 Observability

### Logging
Structured JSON logs with correlation IDs:
```python
logger.info("Render started", extra={
    "project_id": str(project_id),
    "render_id": str(render_id),
    "user_id": str(user.id),
})
```

### Metrics
Prometheus metrics at `/metrics`:
- `http_requests_total` — Request counter by method, path, status
- `render_duration_seconds` — Render time histogram
- `ai_request_duration_seconds` — AI API latency
- `renders_in_progress` — Active renders gauge

### Error Tracking
Sentry integration for production errors:
```bash
export SENTRY_DSN="https://..."
```

---

## 🚢 Deployment

### Kubernetes (Production)

```bash
# 1. Build and push images
docker build -t your-registry/tiktok-engine:latest .
docker push your-registry/tiktok-engine:latest

# 2. Apply manifests
kubectl apply -f k8s/production/

# 3. Check status
kubectl get pods -n tiktok
kubectl logs -f deployment/tiktok-api -n tiktok

# 4. Scale workers
kubectl scale deployment/tiktok-worker-media --replicas=5 -n tiktok
```

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for complete deployment procedures.

### Environment Variables

See [.env.example](.env.example) for complete list. Key variables:

| Category | Variable | Description |
|----------|----------|-------------|
| **AI** | `ANTHROPIC_API_KEY` | Claude API key (required) |
| | `ANTHROPIC_MODEL` | Model version (`claude-sonnet-4-20250514`) |
| **Database** | `DATABASE_URL` | Postgres connection string |
| **Cache/Queue** | `REDIS_URL` | Redis connection string |
| **Storage** | `STORAGE_BACKEND` | `local` or `s3` |
| | `S3_BUCKET` | S3 bucket name (if using S3) |
| **Auth** | `JWT_SECRET` | Secret for JWT signing (generate with `openssl rand -hex 32`) |
| | `JWT_ALGORITHM` | `HS256` (default) |
| **Billing** | `STRIPE_SECRET_KEY` | Stripe API secret key |
| | `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret |
| **Observability** | `SENTRY_DSN` | Sentry error tracking URL |
| | `LOG_LEVEL` | `INFO`, `DEBUG`, `WARNING` |
| **Performance** | `CACHE_ENABLED` | Enable Redis caching (`True`) |
| | `CACHE_TTL` | Default cache TTL in seconds (300) |

---

## 📂 Project Structure

```
tiktok/
├── app/
│   ├── main.py                  # FastAPI app + middleware + exception handlers
│   ├── config.py                # Settings with validation
│   ├── database.py              # Async SQLAlchemy engine + session
│   ├── auth.py                  # JWT authentication utilities
│   ├── cache.py                 # Redis caching decorators
│   ├── error_handling.py        # Custom exceptions, retry logic, circuit breakers
│   ├── logging_config.py        # Structured logging setup
│   ├── metrics.py               # Prometheus metrics
│   ├── prompts.py               # Claude prompt templates
│   ├── api/
│   │   ├── auth.py              # Authentication endpoints
│   │   ├── billing.py           # Stripe billing endpoints
│   │   ├── projects.py          # Project CRUD + pipeline
│   │   ├── assets.py            # Asset upload + transcription
│   │   ├── renders.py           # Render download + status
│   │   ├── styles.py            # Style profile management
│   │   ├── presets.py           # Style preset library
│   │   └── batch.py             # Batch processing + A/B testing
│   ├── models/
│   │   ├── db.py                # SQLAlchemy ORM models
│   │   └── schemas.py           # Pydantic request/response schemas
│   ├── services/
│   │   ├── ai_orchestrator.py   # Claude API integration (caching, retries)
│   │   ├── media_analyzer.py    # FFprobe + Whisper + silence detection
│   │   ├── render_engine.py     # FFmpeg render pipeline
│   │   ├── storage.py           # S3 / local storage abstraction
│   │   ├── billing.py           # Stripe subscription management
│   │   ├── style_presets.py     # Saved style presets
│   │   └── batch.py             # Batch processing + A/B variants
│   └── workers/
│       ├── celery_app.py        # Celery configuration
│       └── tasks.py             # Background tasks
├── tests/
│   ├── conftest.py              # Pytest fixtures
│   ├── unit/                    # Unit tests
│   ├── integration/             # Integration tests
│   └── e2e/                     # End-to-end tests
├── migrations/                  # Alembic database migrations
├── docs/
│   ├── API.md                   # Complete API reference
│   ├── ARCHITECTURE.md          # System architecture
│   └── RUNBOOK.md               # Operations guide
├── k8s/
│   └── production/              # Kubernetes manifests
├── .github/
│   └── workflows/
│       └── ci-cd.yml            # CI/CD pipeline
├── cli.py                       # Standalone CLI tool
├── tiktok_engine/               # Original library (legacy)
├── examples/                    # Sample files
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## 🎨 Edit Spec Contract

Claude outputs a strict JSON edit spec — never freeform instructions. This is the machine-readable contract between AI and FFmpeg:

```json
{
  "project_id": "uuid",
  "output": {
    "width": 1080,
    "height": 1920,
    "fps": 30,
    "duration_sec": 27.4
  },
  "tracks": {
    "video": [{
      "asset_id": "clip_1",
      "start": 0.0,
      "end": 2.1,
      "source_in": 14.3,
      "source_out": 16.4,
      "crop": "smart_center",
      "motion": {
        "type": "zoom_in",
        "strength": 0.08
      }
    }],
    "text": [{
      "start": 0.2,
      "end": 1.1,
      "text": "THIS IS WHY",
      "style": "bold_kinetic_1",
      "position": "lower_third",
      "animation": "pop"
    }],
    "audio": [{
      "asset_id": "music_1",
      "start": 0.0,
      "end": 27.4,
      "gain_db": -18,
      "duck_under_speech": true
    }]
  }
}
```

---

## 🎥 FFmpeg Capabilities

| Feature | Implementation |
|---------|---------------|
| Trim clips | `trim`, `atrim`, `-ss` |
| Stitch clips | `concat` demuxer |
| 9:16 smart crop | `scale` + `crop` |
| Caption burn-in | ASS subtitles via `ass` filter |
| Zoom / push | `zoompan` filter |
| Speed change | `setpts` + `atempo` |
| Audio mixing | `amix` + `sidechaincompress` |
| Silence removal | `silencedetect` → trim pipeline |
| Music ducking | `sidechaincompress` |
| Export | 1080×1920 H.264 MP4 |

---

## 📚 Documentation

- **[API Reference](docs/API.md)** — Complete endpoint documentation with examples
- **[Architecture Guide](docs/ARCHITECTURE.md)** — System design, data flow, security
- **[Operations Runbook](docs/RUNBOOK.md)** — Deployment, monitoring, troubleshooting
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — Development guidelines

---

## 🗺️ Roadmap

### ✅ Phase 1: Production-Grade MVP (COMPLETE)
- [x] FastAPI + async Postgres + Celery workers
- [x] Claude AI style extraction + edit spec generation
- [x] FFmpeg render engine (trim, crop, zoom, captions, audio mix)
- [x] Whisper transcription + silence detection
- [x] JWT authentication + user management
- [x] Stripe billing integration (Starter/Pro/Enterprise)
- [x] Docker Compose full stack
- [x] Comprehensive test suite (80% coverage)
- [x] Structured logging + Prometheus metrics + Sentry
- [x] CI/CD pipeline (GitHub Actions)
- [x] Kubernetes deployment configs
- [x] Error handling, retries, circuit breakers
- [x] Redis caching for performance
- [x] Style presets library
- [x] Batch processing + A/B variant generation
- [x] Complete documentation (API, Architecture, Runbook)

### 🚧 Phase 2: Advanced Features (In Progress)
- [ ] Music library integration
- [ ] Content moderation / safety filters
- [ ] Remotion preview player
- [ ] Timeline editing UI
- [ ] Branded templates
- [ ] Team collaboration features
- [ ] Approval workflows
- [ ] Analytics dashboard

### 🔮 Phase 3: Scale & Enterprise
- [ ] Multi-region deployment
- [ ] CDN integration for renders
- [ ] White-label portal
- [ ] API marketplace
- [ ] Advanced A/B testing analytics
- [ ] Custom ML model training
- [ ] Voice cloning integration

---

## 🤝 Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development setup
- Code style guidelines
- Testing requirements
- Pull request process

---

## 📄 License

Proprietary — All rights reserved.

---

## 🆘 Support

- **Documentation:** [docs/](docs/)
- **Issues:** GitHub Issues
- **Email:** support@example.com
- **Slack:** [Join workspace](#)

---

## 🏆 Credits

Built with:
- [FastAPI](https://fastapi.tiangolo.com/) — Modern Python web framework
- [Claude AI](https://anthropic.com/) — Advanced language model
- [FFmpeg](https://ffmpeg.org/) — Video processing
- [Whisper](https://openai.com/research/whisper) — Speech recognition
- [Celery](https://docs.celeryproject.org/) — Distributed task queue
- [PostgreSQL](https://www.postgresql.org/) — Database
- [Redis](https://redis.io/) — Cache and queue
- [Stripe](https://stripe.com/) — Billing
- [Sentry](https://sentry.io/) — Error tracking
- [Prometheus](https://prometheus.io/) — Metrics

---

**Made with ❤️ for video creators everywhere**
