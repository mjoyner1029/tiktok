# 🎬 Chat-Based Video Creation - Complete Guide

## Overview

The TikTok Style Engine now features a **conversational chat interface** where users can:
- **Upload** videos and images through chat
- **Share** TikTok URLs to extract editing styles  
- **Receive** AI-generated videos matching reference styles
- **Tweak** videos through natural conversation
- **Download** completed renders

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│   Frontend  │────▶│  Chat API    │────▶│   Chat      │
│  React UI   │     │  (FastAPI)   │     │  Processor  │
└─────────────┘     └──────────────┘     └─────────────┘
                           │                     │
                           │                     ▼
                           │              ┌─────────────┐
                           │              │     AI      │
                           │              │ Orchestrator│
                           │              └─────────────┘
                           │                     │
                           ▼                     ▼
                    ┌──────────────┐     ┌─────────────┐
                    │  PostgreSQL  │     │   Celery    │
                    │  (Chat Data) │     │   Workers   │
                    └──────────────┘     └─────────────┘
```

## Backend Implementation

### Database Models (`app/models/db.py`)

#### ChatConversation
```python
class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    
    id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID]  # ForeignKey to User
    project_id: Mapped[Optional[uuid.UUID]]  # ForeignKey to Project
    title: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    
    # Relationships
    user: Mapped["User"]
    project: Mapped[Optional["Project"]]
    messages: Mapped[list["ChatMessage"]]
```

#### ChatMessage
```python
class MessageRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"
    system = "system"

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    
    id: Mapped[uuid.UUID]
    conversation_id: Mapped[uuid.UUID]
    role: Mapped[MessageRole]
    content: Mapped[str]
    attachments: Mapped[Optional[dict]]  # uploaded files/URLs
    metadata: Mapped[Optional[dict]]     # AI response metadata
    created_at: Mapped[datetime]
```

### API Endpoints (`app/api/chat.py`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/chat/conversations` | Create new chat |
| GET | `/api/v1/chat/conversations` | List user's chats |
| GET | `/api/v1/chat/conversations/{id}` | Get chat with messages |
| POST | `/api/v1/chat/conversations/{id}/messages` | Send message |
| POST | `/api/v1/chat/conversations/{id}/upload` | Upload files |
| DELETE | `/api/v1/chat/conversations/{id}` | Delete chat |

### Chat Processor (`app/services/chat_processor.py`)

The brain of the chat system. Handles:

#### TikTok URL Extraction
```python
def extract_tiktok_urls(text: str) -> list[str]:
    # Matches all TikTok URL formats:
    # - https://www.tiktok.com/@user/video/123
    # - https://vm.tiktok.com/abc
    # - https://www.tiktok.com/t/xyz
```

#### Intelligent Response Generation
```python
async def process_chat_message(
    conversation: ChatConversation,
    user_message: str,
    attachments: dict,
    db: AsyncSession,
    user: User,
) -> Tuple[str, dict]:
    """
    Returns (response_text, metadata)
    
    Handles:
    - TikTok URL references
    - Status checks
    - Revision requests
    - Help requests
    - General conversation
    """
```

#### Workflow Intelligence

1. **Detects TikTok URLs** → Downloads reference video
2. **Auto-creates Project** when files uploaded
3. **Triggers Pipeline** when both reference + content ready
4. **Checks Job Status** on user request
5. **Handles Revisions** with AIOrchestrator
6. **Provides Guidance** based on current state

### Security & Performance

#### Rate Limiting (`app/middleware/rate_limiting.py`)
```python
limits = {
    "/api/v1/chat/*/message": 30,      # 30 messages/min
    "/api/v1/projects/*/analyze": 10,   # 10 AI calls/min
    "/api/v1/projects/*/render": 5,     # 5 renders/min
}
```

#### Signed Download URLs (`app/services/storage.py`)
```python
# Instead of: /uploads/video.mp4
# Returns:    /api/v1/download/eyJhbGc...
# Expires:    1 hour
```

## Frontend Implementation

### React Component (`frontend/src/components/ChatInterface.tsx`)

Features:
- ✅ Material-UI design
- ✅ Real-time message updates
- ✅ File upload with preview
- ✅ Auto-scroll to latest message
- ✅ Loading indicators
- ✅ Download button for renders
- ✅ Status chips (analyzing, rendering, etc.)

### Usage
```tsx
import ChatInterface from './components/ChatInterface';

function App() {
  return <ChatInterface />;
}
```

## Example User Flow

### 1. User Opens Chat
```
System: 👋 Hi! I'm your AI video editor. Upload your videos and share a 
        TikTok URL you like, and I'll create a matching video!
```

### 2. User Shares TikTok URL
```
User: Make me a video like https://www.tiktok.com/@creator/video/123

AI: 🎯 Got it! I'm analyzing the style from that TikTok video.
    Now upload your videos and images, and I'll create a TikTok 
    matching that style!
```

### 3. User Uploads Content
```
User: [Uploads 3 videos + 2 images]

AI: ✅ Files uploaded! I have everything I need now. Let me analyze 
    the reference style and generate your video. This will take a 
    few minutes...
```

### 4. User Checks Status
```
User: Is it done?

AI: ⏳ Working on it! 2 task(s) in progress:
    - import_url: running
    - full_pipeline: pending
```

### 5. Video Complete
```
AI: ✅ Your video is ready! Duration: 15.3s
    Download: /api/v1/download/eyJhbGc...
    
    [Download Video] button
```

### 6. User Requests Changes
```
User: Make the cuts faster and add more zoom effects

AI: ✅ Updated the edit plan (v2)! Ready to render with these changes.
```

## API Examples

### Create Conversation
```bash
curl -X POST http://localhost:8000/api/v1/chat/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "My TikTok Project"}'
```

Response:
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "project_id": null,
  "title": "My TikTok Project",
  "messages": [
    {
      "id": "uuid",
      "role": "system",
      "content": "👋 Hi! Upload your videos and share a TikTok...",
      "created_at": "2025-01-10T12:00:00Z"
    }
  ],
  "created_at": "2025-01-10T12:00:00Z"
}
```

### Send Message
```bash
curl -X POST http://localhost:8000/api/v1/chat/conversations/$CONV_ID/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "https://www.tiktok.com/@user/video/123"}'
```

### Upload Files
```bash
curl -X POST http://localhost:8000/api/v1/chat/conversations/$CONV_ID/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@video1.mp4" \
  -F "files=@video2.mp4" \
  -F "files=@image.jpg"
```

## Installation & Setup

### 1. Install Dependencies
```bash
pip install -r requirements.txt
# Requires: itsdangerous>=2.1
```

### 2. Create Database Migration
```bash
# Install asyncpg first
pip install asyncpg

# Generate migration
PYTHONPATH=/Users/mjoyner/tiktok alembic revision --autogenerate -m "Add chat tables"

# Apply migration
alembic upgrade head
```

### 3. Set Environment Variables
```bash
export URL_SIGNING_KEY="your-secret-key-different-from-jwt-secret"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/tiktok"
export REDIS_URL="redis://localhost:6379/0"
```

### 4. Start Services
```bash
# Terminal 1: Redis
redis-server

# Terminal 2: Celery Worker
celery -A app.workers.celery_app worker \
  --loglevel=info \
  -Q media,ai,render

# Terminal 3: FastAPI Server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Terminal 4: Frontend (optional)
cd frontend
npm install
npm run dev
```

## Load Testing

### Run Load Test
```bash
python scripts/load_test.py \
  --users 10 \
  --duration 60 \
  --url http://localhost:8000
```

### Expected Output
```
🚀 Starting load test: 10 users for 60 seconds
   Target: http://localhost:8000

======================================================================
📊 LOAD TEST RESULTS
======================================================================
Duration:          62.34s
Concurrent Users:  10
Total Requests:    543
Successful:        520 (95.8%)
Failed:            23 (4.2%)

Response Times:
  Mean:   0.342s
  Median: 0.287s
  Min:    0.045s
  Max:    2.143s
  P95:    0.892s
  P99:    1.456s

Throughput:        8.71 req/s

✅ PASS: Success rate >= 95%
```

## Testing

### Unit Tests
```bash
# Test chat processor
pytest tests/unit/test_chat_processor.py -v

# Test rate limiting
pytest tests/unit/test_rate_limiting.py -v
```

### Integration Tests
```bash
# Test full chat workflow
pytest tests/integration/test_chat_api.py -v
```

### Manual Testing with cURL
```bash
# 1. Register user
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "test@example.com", "password": "test123", "name": "Test User"}'

# 2. Get token from response
TOKEN="your-jwt-token"

# 3. Create chat
CONV_ID=$(curl -X POST http://localhost:8000/api/v1/chat/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Chat"}' | jq -r .id)

# 4. Send message
curl -X POST http://localhost:8000/api/v1/chat/conversations/$CONV_ID/messages \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Help me create a video"}'

# 5. Upload files
curl -X POST http://localhost:8000/api/v1/chat/conversations/$CONV_ID/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "files=@test_video.mp4"
```

## Troubleshooting

### Issue: "ModuleNotFoundError: No module named 'asyncpg'"
**Solution**: `pip install asyncpg`

### Issue: Rate limit exceeded (429)
**Solution**: Wait 60 seconds or increase limits in `rate_limiting.py`

### Issue: Signed URL expired
**Solution**: URLs expire after 1 hour. Refresh the conversation to get new URL.

### Issue: Celery tasks not running
**Solution**: 
1. Check Redis is running: `redis-cli ping` → should return "PONG"
2. Check Celery worker logs
3. Verify queue names match in tasks.py and celery worker command

### Issue: Chat processor not responding
**Solution**:
1. Check AIOrchestrator has valid ANTHROPIC_API_KEY
2. Check database connection
3. Check logs: `tail -f logs/app.log`

## Production Deployment

### Docker Compose
```yaml
version: '3.8'
services:
  web:
    build: .
    ports:
      - "8000:8000"
    environment:
      - URL_SIGNING_KEY=${URL_SIGNING_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    depends_on:
      - postgres
      - redis
  
  worker:
    build: .
    command: celery -A app.workers.celery_app worker
    depends_on:
      - redis
  
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
```

### Kubernetes
See `/k8s/` directory for manifests:
- `deployment.yaml` - Web server + workers
- `service.yaml` - Load balancer
- `configmap.yaml` - Configuration
- `secrets.yaml` - API keys

## Monitoring

### Health Check
```bash
curl http://localhost:8000/health
```

### Metrics
```bash
curl http://localhost:8000/metrics
```

### Chat Analytics
```sql
-- Most active conversations
SELECT conversation_id, COUNT(*) as message_count
FROM chat_messages
GROUP BY conversation_id
ORDER BY message_count DESC
LIMIT 10;

-- Average messages per conversation
SELECT AVG(message_count)
FROM (
  SELECT conversation_id, COUNT(*) as message_count
  FROM chat_messages
  GROUP BY conversation_id
) sub;

-- User engagement
SELECT user_id, COUNT(DISTINCT conversation_id) as conv_count
FROM chat_conversations
GROUP BY user_id
ORDER BY conv_count DESC;
```

## Future Enhancements

### Phase 1 (Current)
- ✅ Basic chat interface
- ✅ File uploads
- ✅ TikTok URL extraction
- ✅ AI responses
- ✅ Status checking

### Phase 2 (Planned)
- WebSocket for real-time updates
- Voice input/output
- Multi-language support
- Conversation export (PDF/JSON)
- Sharing/collaboration

### Phase 3 (Future)
- Video preview in chat
- In-chat video editing tools
- Templates and presets via chat
- Batch processing through conversation
- Analytics dashboard

## Support

For issues or questions:
1. Check [CHAT_IMPLEMENTATION.md](./CHAT_IMPLEMENTATION.md)
2. Review [API.md](./API.md) for endpoint details
3. See [RUNBOOK.md](./RUNBOOK.md) for operations
4. Open GitHub issue with logs and reproduction steps

## License

See [LICENSE](../LICENSE) file for details.
