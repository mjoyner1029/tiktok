# Production Runbook for TikTok Style Engine

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Deployment](#deployment)
3. [Monitoring](#monitoring)
4. [Common Operations](#common-operations)
5. [Troubleshooting](#troubleshooting)
6. [Incident Response](#incident-response)
7. [Scaling](#scaling)
8. [Backups & Disaster Recovery](#backups--disaster-recovery)

---

## Architecture Overview

### Components

- **API Server** (FastAPI): Handles HTTP requests, authentication, orchestration
- **Celery Workers**:
  - Media Queue: Transcription, media analysis
  - AI Queue: Claude API calls, style extraction
  - Render Queue: FFmpeg video rendering
- **PostgreSQL**: Primary data store
- **Redis**: Cache + Celery broker + results backend
- **S3**: Asset and render storage

### Data Flow

```
User → API → [ Project → Assets → Style Analysis → Edit Spec → Render ] → Download
            ↓         ↓                ↓             ↓           ↓
         Postgres   S3         Celery (AI)     Celery (AI)  Celery (Render)
```

---

## Deployment

### Prerequisites

- Kubernetes cluster (GKE, EKS, or AKS)
- PostgreSQL 16+ (managed service recommended)
- Redis 7+ (ElastiCache, MemoryStore, etc.)
- S3-compatible storage
- SSL certificate (Let's Encrypt via cert-manager)

### Initial Setup

```bash
# 1. Create namespace
kubectl create namespace tiktok-engine

# 2. Create secrets (use Sealed Secrets or external secrets in production)
kubectl create secret generic tiktok-secrets \
  --from-literal=SECRET_KEY="$(openssl rand -hex 32)" \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-xxx" \
  --from-literal=STRIPE_SECRET_KEY="sk_live_xxx" \
  --from-literal=STRIPE_WEBHOOK_SECRET="whsec_xxx" \
  --from-literal=SENTRY_DSN="https://xxx@sentry.io/xxx" \
  -n tiktok-engine

# 3. Apply manifests
kubectl apply -f k8s/production/

# 4. Run migrations
kubectl run migrate --rm -it \
  --image=ghcr.io/yourorg/tiktok-engine:latest \
  --restart=Never \
  --env-from=configmap/tiktok-config \
  --env-from=secret/tiktok-secrets \
  -- alembic upgrade head

# 5. Verify deployment
kubectl get pods -n tiktok-engine
kubectl logs -f deployment/tiktok-api -n tiktok-engine
```

### Rolling Updates

```bash
# Update API
kubectl set image deployment/tiktok-api api=ghcr.io/yourorg/tiktok-engine:v1.2.3 -n tiktok-engine

# Update workers
kubectl set image deployment/tiktok-worker-media worker=ghcr.io/yourorg/tiktok-engine:v1.2.3 -n tiktok-engine
kubectl set image deployment/tiktok-worker-render worker=ghcr.io/yourorg/tiktok-engine:v1.2.3 -n tiktok-engine

# Watch rollout
kubectl rollout status deployment/tiktok-api -n tiktok-engine
```

---

## Monitoring

### Key Metrics

**API Server:**
- `http_requests_total` - Total requests by endpoint and status
- `http_request_duration_seconds` - Request latency
- `renders_in_progress` - Active render count
- `active_projects` - Total active projects

**Workers:**
- `renders_total{status="completed"}` - Successful renders
- `renders_total{status="failed"}` - Failed renders
- `render_duration_seconds` - Render time distribution
- `ai_requests_total` - Claude API usage
- `transcriptions_total` - Transcription count

### Dashboards

Access Grafana: `https://grafana.yourdomain.com`

Import dashboard ID: `tiktok-engine-overview.json`

### Alerts

**Critical:**
- API server down (>2 pods unavailable)
- Database connection failure rate >10%
- Render failure rate >20%
- S3 upload failures
- Redis unavailable

**Warning:**
- API latency p99 >5s
- Worker queue depth >100
- Disk usage >80%
- Memory usage >85%

### Logs

```bash
# API logs
kubectl logs -f deployment/tiktok-api -n tiktok-engine

# Worker logs
kubectl logs -f deployment/tiktok-worker-render -n tiktok-engine

# Search logs in Sentry
# Visit: https://sentry.io/organizations/yourorg/issues

# Query logs (if using CloudWatch/Stackdriver)
# Filter by request_id to trace a single request
```

---

## Common Operations

### Scale Workers

```bash
# Scale render workers for high load
kubectl scale deployment/tiktok-worker-render --replicas=10 -n tiktok-engine

# Scale media workers
kubectl scale deployment/tiktok-worker-media --replicas=5 -n tiktok-engine
```

### Clear Cache

```bash
# Connect to Redis
kubectl port-forward svc/redis 6379:6379 -n tiktok-engine

# In another terminal
redis-cli
> FLUSHDB
```

### Manual Migration

```bash
# Create migration
kubectl run migrate-create --rm -it \
  --image=ghcr.io/yourorg/tiktok-engine:latest \
  --restart=Never \
  -- alembic revision --autogenerate -m "description"

# Apply migrations
kubectl run migrate --rm -it \
  --image=ghcr.io/yourorg/tiktok-engine:latest \
  --restart=Never \
  --env-from=configmap/tiktok-config \
  --env-from=secret/tiktok-secrets \
  -- alembic upgrade head
```

### Reset Monthly Usage

```bash
# Run monthly (via CronJob or manually)
python -c "
from app.services.billing import reset_monthly_usage
from app.database import get_session
import asyncio

async def main():
    async with get_session() as db:
        await reset_monthly_usage(db)

asyncio.run(main())
"
```

---

## Troubleshooting

### API Returns 500 Errors

**Symptoms:** API returning 500, errors in Sentry

**Diagnosis:**
```bash
# Check API pods
kubectl get pods -n tiktok-engine
kubectl logs deployment/tiktok-api -n tiktok-engine --tail=100

# Check database connectivity
kubectl run pg-test --rm -it --image=postgres:16 --restart=Never -- \
  psql postgresql://user:pass@host:5432/db -c "SELECT 1"
```

**Resolution:**
- Check Sentry for stack traces
- Verify environment variables
- Check database connection pool exhaustion
- Review recent deployments

### Renders Stuck in "queued" Status

**Symptoms:** Renders not progressing, queue depth increasing

**Diagnosis:**
```bash
# Check worker status
kubectl get pods -l app=tiktok-worker,queue=render -n tiktok-engine

# Check worker logs
kubectl logs -l app=tiktok-worker,queue=render -n tiktok-engine --tail=50

# Check Celery flower
kubectl port-forward svc/flower 5555:5555 -n tiktok-engine
# Visit http://localhost:5555
```

**Resolution:**
- Restart workers: `kubectl rollout restart deployment/tiktok-worker-render -n tiktok-engine`
- Check FFmpeg binary availability
- Verify S3 write permissions
- Scale up render workers if queue is backed up

### High Memory Usage

**Symptoms:** OOM kills, pods restarting frequently

**Diagnosis:**
```bash
# Check memory usage
kubectl top pods -n tiktok-engine

# Check events
kubectl get events -n tiktok-engine --sort-by='.lastTimestamp'
```

**Resolution:**
- Increase resource limits in deployment.yaml
- Check for memory leaks (Whisper model caching)
- Review concurrent render limits
- Enable memory profiling temporarily

### Claude API Rate Limits

**Symptoms:** AI tasks failing with 429 errors

**Diagnosis:**
- Check Sentry for `anthropic.RateLimitError`
- Check logs for rate limit messages

**Resolution:**
- Implement exponential backoff (already in place via `tenacity`)
- Request rate limit increase from Anthropic
- Add request queuing/throttling
- Cache style profiles more aggressively

---

## Incident Response

### Severity Levels

**P0 (Critical):** Complete outage, data loss
**P1 (High):** Major feature broken, affecting >50% of users
**P2 (Medium):** Minor feature broken, workaround exists
**P3 (Low):** Cosmetic issue, no user impact

### Response Process

1. **Acknowledge**: Respond in #incidents Slack channel
2. **Investigate**: Check monitoring dashboards, logs, Sentry
3. **Mitigate**: Apply immediate fix or rollback
4. **Communicate**: Update status page
5. **Resolve**: Deploy permanent fix
6. **Postmortem**: Write incident report within 48h

### Rollback Procedure

```bash
# Rollback API deployment
kubectl rollout undo deployment/tiktok-api -n tiktok-engine

# Rollback database migration
kubectl run migrate-rollback --rm -it \
  --image=ghcr.io/yourorg/tiktok-engine:latest \
  --restart=Never \
  --env-from=configmap/tiktok-config \
  --env-from=secret/tiktok-secrets \
  -- alembic downgrade -1
```

---

## Scaling

### Horizontal Scaling

**API Servers:**
- Auto-scales 3-20 replicas based on CPU/memory
- Target: 70% CPU utilization

**Workers:**
- Render queue: Scale based on queue depth (target <50 pending)
- Media queue: Scale with upload volume
- AI queue: Limited by Claude API rate limits

### Vertical Scaling

Update resource requests/limits in `k8s/production/deployment.yaml`:

```yaml
resources:
  requests:
    cpu: "2000m"      # Increase for more CPU
    memory: "4Gi"     # Increase for more memory
  limits:
    cpu: "8000m"
    memory: "16Gi"
```

### Database Scaling

- Enable read replicas for heavy SELECT workloads
- Consider Aurora Serverless for auto-scaling
- Partition large tables (renders, assets) by date

---

## Backups & Disaster Recovery

### Database Backups

- **Automated**: Daily snapshots via managed PostgreSQL service
- **Retention**: 30 days for production
- **Testing**: Monthly restore drills

### S3 Backups

- **Versioning**: Enabled on production bucket
- **Lifecycle**: Move to Glacier after 90 days
- **Retention**: 1 year

### Recovery Procedures

**Database restore:**
```bash
# Restore from snapshot (AWS RDS example)
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier tiktok-engine-restored \
  --db-snapshot-identifier snap-20260415
```

**S3 restore:**
```bash
# Restore specific version
aws s3api copy-object \
  --copy-source bucket/key?versionId=ABC123 \
 --bucket bucket \
  --key key
```

### RTO/RPO

- **RTO (Recovery Time Objective):** 4 hours
- **RPO (Recovery Point Objective):** 15 minutes (database), 0 (S3 versioned)

---

## Contact

- **On-call**: #oncall-tiktok-engine Slack channel
- **Escalation**: engineering@yourcompany.com
- **Docs**: https://docs.internal/tiktok-engine
