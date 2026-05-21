# API Documentation

## Authentication

All authenticated endpoints require a JWT token in the `Authorization` header:

```http
Authorization: Bearer <access_token>
```

### Register

**POST** `/api/v1/auth/register`

Create a new user account.

**Request:**
```json
{
  "email": "user@example.com",
  "password": "securepassword123",
  "name": "John Doe"
}
```

**Response:** `201 Created`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "name": "John Doe",
    "plan": "starter",
    "created_at": "2026-04-15T10:00:00Z"
  }
}
```

### Login

**POST** `/api/v1/auth/login`

Authenticate with email and password.

**Request:** (`application/x-www-form-urlencoded`)
```
username=user@example.com
password=securepassword123
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": { ... }
}
```

### Refresh Token

**POST** `/api/v1/auth/refresh`

Refresh an expired access token.

**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response:** `200 OK`
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer",
  "user": { ... }
}
```

---

## Projects

### Create Project

**POST** `/api/v1/projects/`

**Auth Required:** Yes

**Request:**
```json
{
  "title": "Morning Routine TikTok",
  "goal": "Create viral content in Alex's style",
  "target_platform": "tiktok"
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "workspace_id": "uuid",
  "title": "Morning Routine TikTok",
  "status": "draft",
  "goal": "Create viral content in Alex's style",
  "target_platform": "tiktok",
  "created_at": "2026-04-15T10:00:00Z",
  "updated_at": "2026-04-15T10:00:00Z"
}
```

### List Projects

**GET** `/api/v1/projects/`

**Auth Required:** Yes

**Response:** `200 OK`
```json
[
  {
    "id": "uuid",
    "title": "Project 1",
    "status": "ready",
    ...
  }
]
```

### Get Project

**GET** `/api/v1/projects/{project_id}`

**Auth Required:** Yes

### Update Project

**PATCH** `/api/v1/projects/{project_id}`

**Auth Required:** Yes

**Request:**
```json
{
  "title": "Updated Title"
}
```

### Delete Project

**DELETE** `/api/v1/projects/{project_id}`

**Auth Required:** Yes

**Response:** `204 No Content`

### Run Pipeline

**POST** `/api/v1/projects/{project_id}/pipeline`

**Auth Required:** Yes

Runs the complete pipeline: transcribe → analyze style → generate edit spec → render.

**Response:** `202 Accepted`
```json
{
  "job_id": "uuid",
  "status": "pending",
  "message": "Pipeline started"
}
```

### Analyze Style

**POST** `/api/v1/projects/{project_id}/analyze`

**Auth Required:** Yes

Analyze reference videos and generate edit spec (doesn't render).

**Response:** `202 Accepted`

### Render

**POST** `/api/v1/projects/{project_id}/render`

**Auth Required:** Yes

Render from the latest edit spec.

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "project_id": "uuid", 
  "edit_spec_id": "uuid",
  "status": "queued",
  "created_at": "2026-04-15T10:00:00Z"
}
```

### Revise Edit Spec

**POST** `/api/v1/projects/{project_id}/revise`

**Auth Required:** Yes

Revise the latest edit spec with natural language feedback.

**Request:**
```json
{
  "feedback": "Make the cuts faster and add more zoom on keywords"
}
```

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "project_id": "uuid",
  "version": 2,
  "spec_json": { ... },
  "source": "revised",
  "revision_note": "Make the cuts faster...",
  "created_at": "2026-04-15T10:00:00Z"
}
```

---

## Assets

### Upload Asset

**POST** `/api/v1/assets/upload/{project_id}`

**Auth Required:** Yes

**Content-Type:** `multipart/form-data`

**Form Fields:**
- `file`: Video/audio/image file
- `asset_type`: `reference_video` | `raw_video` | `audio` | `image`

**Response:** `201 Created`
```json
{
  "id": "uuid",
  "project_id": "uuid",
  "type": "raw_video",
  "filename": "clip.mp4",
  "storage_url": "s3://bucket/path/to/clip.mp4",
  "duration_sec": 45.2,
  "width": 1920,
  "height": 1080,
  "transcript_status": "pending",
  "created_at": "2026-04-15T10:00:00Z"
}
```

### List Assets

**GET** `/api/v1/assets/{project_id}`

**Auth Required:** Yes

### Get Asset

**GET** `/api/v1/assets/detail/{asset_id}`

**Auth Required:** Yes

### Delete Asset

**DELETE** `/api/v1/assets/detail/{asset_id}`

**Auth Required:** Yes

### Transcribe Asset

**POST** `/api/v1/assets/transcribe/{asset_id}`

**Auth Required:** Yes

Trigger transcription for a single asset.

**Response:** `202 Accepted`

---

## Renders

### Get Render

**GET** `/api/v1/renders/{render_id}`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "id": "uuid",
  "project_id": "uuid",
  "edit_spec_id": "uuid",
  "status": "completed",
  "output_url": "s3://bucket/renders/final.mp4",
  "thumbnail_url": "s3://bucket/renders/thumb.jpg",
  "duration_sec": 27.4,
  "file_size_bytes": 15728640,
  "created_at": "2026-04-15T10:00:00Z",
  "finished_at": "2026-04-15T10:02:30Z"
}
```

### Download Render

**GET** `/api/v1/renders/{render_id}/download`

**Auth Required:** Yes

Returns presigned S3 URL or streams file.

**Response:** `302 Found` or `200 OK` (file stream)

### Get Thumbnail

**GET** `/api/v1/renders/{render_id}/thumbnail`

**Auth Required:** Yes

---

## Billing

### Create Checkout Session

**POST** `/api/v1/billing/checkout`

**Auth Required:** Yes

Create a Stripe checkout session for subscription.

**Request:**
```json
{
  "plan": "creator_pro",
  "success_url": "https://app.example.com/success",
  "cancel_url": "https://app.example.com/billing"
}
```

**Response:** `200 OK`
```json
{
  "checkout_url": "https://checkout.stripe.com/c/pay/cs_xxx"
}
```

### Get Billing Portal

**POST** `/api/v1/billing/portal`

**Auth Required:** Yes

Get Stripe billing portal URL.

**Request:**
```json
{
  "return_url": "https://app.example.com/settings"
}
```

**Response:** `200 OK`
```json
{
  "portal_url": "https://billing.stripe.com/p/session/xxx"
}
```

### Get Subscription

**GET** `/api/v1/billing/subscription`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "plan": "creator_pro",
  "status": "active",
  "renders_used_this_month": 12,
  "renders_limit": 100,
  "current_period_end": "2026-05-15T10:00:00Z"
}
```

### Get Usage

**GET** `/api/v1/billing/usage`

**Auth Required:** Yes

**Response:** `200 OK`
```json
{
  "renders_used": 12,
  "renders_limit": 100,
  "can_render": true
}
```

---

## Status Codes

- `200 OK` - Request succeeded
- `201 Created` - Resource created
- `202 Accepted` - Async job started
- `204 No Content` - Success, no response body
- `400 Bad Request` - Invalid request data
- `401 Unauthorized` - Missing or invalid auth token
- `403 Forbidden` - Insufficient permissions
- `404 Not Found` - Resource not found
- `422 Unprocessable Entity` - Validation error
- `429 Too Many Requests` - Rate limit exceeded
- `500 Internal Server Error` - Server error

---

## Error Response Format

```json
{
  "error": "ErrorType",
  "message": "Human-readable error message",
  "request_id": "uuid-for-tracking"
}
```

---

## Rate Limiting

- **Default:** 60 requests/minute per IP
- **Authenticated:** 100 requests/minute per user
- **Render endpoints:** 10 renders/hour per user (plan-dependent)

Headers:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1618243200
```

---

## Webhooks

### Stripe Events

Configure webhook endpoint: `https://api.yourdomain.com/api/v1/billing/webhook`

Supported events:
- `checkout.session.completed`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.payment_failed`

---

## SDKs

### Python

```python
from tiktok_engine_client import TikTokEngine

client = TikTokEngine(api_key="your_api_key")

# Create project
project = client.projects.create(
    title="My Project",
    goal="Create viral content"
)

# Upload reference
asset = client.assets.upload(
    project_id=project.id,
    file=open("reference.mp4", "rb"),
    asset_type="reference_video"
)

# Run pipeline
job = client.projects.run_pipeline(project.id)

# Poll for completion
while True:
    status = client.jobs.get(job.id)
    if status.status in ["completed", "failed"]:
        break
    time.sleep(5)

# Download render
render = client.renders.get(status.result["render_id"])
client.renders.download(render.id, "output.mp4")
```

### TypeScript

```typescript
import { TikTokEngine } from '@tiktok-engine/client';

const client = new TikTokEngine({ apiKey: 'your_api_key' });

// Create and run
const project = await client.projects.create({
  title: 'My Project',
  goal: 'Create viral content',
});

await client.assets.upload(project.id, {
  file: fileBuffer,
  assetType: 'reference_video',
});

const job = await client.projects.runPipeline(project.id);

// Wait for completion
const render = await client.jobs.waitFor(job.id);

// Download
const videoUrl = await client.renders.getDownloadUrl(render.id);
```
