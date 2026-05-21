# Architecture Documentation

## System Overview

TikTok Style Engine is a production-grade AI-powered SaaS that analyzes reference TikTok videos and automatically generates draft-quality edited videos from raw footage.

```
┌────────────────────┐
│   Web/Mobile App   │
└──────────┬─────────┘
           │ HTTPS
           ▼
┌──────────────────────────────────────────────┐
│              Load Balancer/CDN                │
│         (Cloudflare / AWS ALB)                │
└──────────┬───────────────────────────────────┘
           │
    ┌──────┴──────┬─────────┬───────────┐
    ▼             ▼         ▼           ▼
┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐
│  API   │  │  API   │  │  API   │  │  API   │
│Pod 1   │  │Pod 2   │  │Pod 3   │  │Pod N   │
└───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘
    │           │            │            │
    └───────────┴────────────┴────────────┘
                │
        ┌───────┴───────────────────────┐
        ▼                               ▼
┌──────────────┐              ┌──────────────────┐
│  PostgreSQL  │              │   Redis          │
│  (Primary)   │              │   - Cache        │
│              │              │   - Queue Broker │
│  [Replicas]  │              │   - Results      │
└──────────────┘              └────────┬─────────┘
                                       │
                ┌──────────────────────┘
                ▼
        ┌───────────────┐
        │ Celery Workers│
        ├───────────────┤
        │ Media Queue   │ ← Transcription, analysis
        │ AI Queue      │ ← Claude API calls
        │ Render Queue  │ ← FFmpeg renders
        └───────┬───────┘
                │
                ▼
        ┌───────────────┐
        │   S3 Storage  │
        │  - Raw assets │
        │  - Renders    │
        │  - Thumbnails │
        └───────────────┘
```

## Component Details

### API Server (FastAPI)

**Responsibilities:**
- Handle HTTP requests
- Authentication & authorization (JWT)
- Request validation (Pydantic)
- Job orchestration
- Serve downloads via presigned URLs

**Key Features:**
- Async I/O for high concurrency
- Connection pooling to PostgreSQL
- Redis caching for hot data
- Prometheus metrics export
- Structured JSON logging
- Sentry error tracking

**Scaling:**
- Horizontal: 3-20 pods (HPA based on CPU/memory
- Vertical: 500m-2000m CPU, 1-4 Gi memory
- Stateless (can scale freely)

### Celery Workers

#### Media Queue
- **Tasks:** Transcription (Whisper), silence detection, media analysis (FFprobe)
- **Concurrency:** 2 tasks/worker
- **Scaling:** Based on upload volume
- **Resource:** 1-4 CPU, 2-8 Gi memory

#### AI Queue
- **Tasks:** Style extraction, edit spec generation, revisions
- **Concurrency:** 2 tasks/worker (rate-limited by Claude API)
- **Scaling:** Limited by Anthropic rate limits
- **Resource:** 1-2 CPU, 2-4 Gi memory

#### Render Queue
- **Tasks:** FFmpeg video rendering
- **Concurrency:** 1 task/worker (CPU-intensive)
- **Scaling:** Based on queue depth, max concurrent renders
- **Resource:** 2-8 CPU, 4-16 Gi memory

### PostgreSQL

**Schema:** 10 tables
- `users` → owner of workspaces
- `workspaces` → team/organization boundary
- `projects` → single video project
- `assets` → uploaded media files
- `style_profiles` → extracted style patterns
- `edit_specs` → versioned edit instructions (render contract)
- `renders` → output videos
- `jobs` → async task tracking
- `subscriptions` → billing/plan info
- `api_keys` → API authentication

**Performance:**
- Indexes on foreign keys, status fields
- Connection pooling (async)
- Read replicas for heavy SELECT loads
- Partitioning by date for large tables

### Redis

**Uses:**
1. **Cache:** Style profiles, transcripts, thumbnails
2. **Celery Broker:** Task queue management
3. **Results Backend:** Task result storage
4. **Rate Limiting:** API throttling

**Configuration:**
- Database 0: Cache
- Database 1: Celery broker
- Database 2: Celery results
- Database 15: Test isolation

### S3 Storage

**Buckets:**
- `tiktok-engine-{env}-assets`: Uploaded videos/audio
- `tiktok-engine-{env}-renders`: Output MP4s
- `tiktok-engine-{env}-thumbnails`: Video previews

**Lifecycle:**
- Assets: No expiration (user-owned)
- Renders: Archive to Glacier after 90 days
- Thumbnails: 30-day expiration

**Access:**
- Workers: Direct S3 SDK access (IAM role)
- Users: Presigned URLs (1-hour TTL)

## Data Flow: Complete Pipeline

```
1. User uploads reference video + raw clips
   ↓
2. API saves to S3, creates Asset records
   ↓
3. Celery (Media Queue) transcribes with Whisper
   ↓
4. API triggers style analysis
   ↓
5. Celery (AI Queue) calls Claude to extract style profile
   ↓
6. Claude analyzes:
   - Hook strategy
   - Cut duration patterns
   - Caption style
   - Zoom/motion behavior
   - Energy curve
   ↓
7. Celery (AI Queue) generates edit spec
   ↓
8. Claude produces JSON render contract:
   - Video track (clips, in/out points, motion)
   - Text track (captions, timing, animation)
   - Audio track (music, ducking)
   ↓
9. API creates EditSpec record, triggers render
   ↓
10. Celery (Render Queue) executes FFmpeg pipeline:
    - Normalize clips (fps, resolution, aspect)
    - Trim to in/out points
    - Apply motion (zoompan filter)
    - Generate ASS subtitles
    - Mix audio with ducking
    - Concatenate clips
    - Export 1080×1920 MP4
    ↓
11. Upload to S3, create Render record
    ↓
12. User downloads via presigned URL
```

## Security Architecture

### Authentication Flow

```
User → [Login] → API
              ↓
         Verify password
              ↓
         Generate JWT (access + refresh)
              ↓
         Return tokens
              
User → [API Request + Token] → API
                             ↓
                        Verify JWT signature
                             ↓
                        Extract user_id
                             ↓
                        Load User from DB
                             ↓
                        Check permissions
                             ↓
                        Process request
```

### Authorization Layers

1. **Authentication:** JWT token validation
2. **Ownership:** User can only access their workspaces/projects
3. **Plan Limits:** Check subscription tier for feature access
4. **Usage Limits:** Enforce monthly render quotas

### Secrets Management

**Development:**
- `.env` file (git-ignored)

**Production:**
- Kubernetes Secrets
- AWS Secrets Manager / GCP Secret Manager
- Sealed Secrets for GitOps
- Never commit secrets to git

### Input Validation

- **API:** Pydantic schemas with strict types
- **FFmpeg:** Path validation, no shell metacharacters
- **File uploads:** Size limits, format allowlist
- **User input:** SQL injection protection via ORM

## Observability

### Metrics (Prometheus)

**API:**
- `http_requests_total{method, endpoint, status}`
- `http_request_duration_seconds{method, endpoint}`
- `renders_in_progress`
- `active_projects`

**Workers:**
- `renders_total{status}`
- `render_duration_seconds`
- `ai_requests_total{provider, model, status}`
- `transcriptions_total{status}`

### Logs (Structured)

**Format:** JSON in production, pretty console in dev

**Fields:**
- `timestamp` (ISO 8601)
- `level` (DEBUG/INFO/WARNING/ERROR)
- `request_id` (correlation)
- `user_id` (context)
- `message`
- `extra` (arbitrary context)

**Aggregation:** Sent to Sentry for errors, stdout for K8s log collection

### Tracing

**Request ID:** Generated per-request, propagated through:
- HTTP headers (`X-Request-ID`)
- Celery tasks
- Database queries (as comment)
- Logs

### Alerts

**Critical:**
- API pods down
- Database connection failures
- S3 upload failures
- Render failure rate >20%

**Warning:**
- API latency p99 >5s
- Queue depth >100
- Memory usage >85%

## Deployment

### Environments

| Environment | Purpose | DB | Hosting |
|-------------|---------|----|---------||
| Development | Local dev | SQLite/Postgres | Docker Compose |
| Staging | Pre-prod testing | Cloud Postgres | Kubernetes (GKE/EKS) |
| Production | Live users | Cloud Postgres | Kubernetes (Multi-region) |

### CI/CD Pipeline

```
PR Created → Run Tests → Security Scan
                              ↓
                         Build Docker Image
                              ↓
                         Push to Registry
                              ↓
Merge to develop → Deploy to Staging
                              ↓
                         Run E2E Tests
                              ↓
Release Tagged → Deploy to Production (with approval)
```

### Zero-Downtime Deploys

1. Build new image
2. Push to registry with version tag
3. Update K8s Deployment (rolling update)
   - Start new pods
   - Wait for readiness probe
   - Terminate old pods
4. Database migrations run before deployment
5. Rollback = `kubectl rollout undo`

## Disaster Recovery

### RTO/RPO

- **RTO:** 4 hours (time to fully recover)
- **RPO:** 15 minutes database, 0 minutes S3 (versioning)

### Backup Strategy

**Database:**
- Automated snapshots every 6 hours
- 30-day retention
- Cross-region replication

**S3:**
- Versioning enabled
- Lifecycle policies
- Cross-region replication for critical buckets

**Recovery Procedures:**
1. Restore DB from latest snapshot
2. Point application to restored DB
3. Verify data integrity
4. Resume traffic

## Performance Targets

| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| API latency (p99) | 2s | 5s |
| Render time (30s video) | 60s | 120s |
| Transcription time (1 min) | 30s | 90s |
| Cache hit rate | 60% | 40% |
| Uptime | 99.9% | 99.5% |

## Cost Optimization

1. **Autoscaling:** Scale down workers during off-peak
2. **S3 Lifecycle:** Archive old renders to Glacier
3. **Spot Instances:** Use for render workers (fault-tolerant)
4. **Reserved Instances:** For baseline load
5. **Cache Aggressively:** Reduce database/AI API calls
6. **Batch Operations:** Group transcriptions, render in bulk

## Future Architecture

**Phase 2:**
- Remotion.js preview player
- Real-time collaboration (WebSockets)
- Event sourcing for edit history

**Phase 3:**
- Multi-region deployment
- Edge rendering (CloudFlare Workers)
- ML model for style transfer (on-device)
