# Contributing to TikTok Style Engine

Thank you for your interest in contributing to TikTok Style Engine! This document provides guidelines and best practices for contributing to the project.

---

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Code Standards](#code-standards)
- [Testing Requirements](#testing-requirements)
- [Commit Guidelines](#commit-guidelines)
- [Pull Request Process](#pull-request-process)
- [Architecture Decisions](#architecture-decisions)

---

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on the problem, not the person
- Assume good intentions

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- FFmpeg 6+
- Docker & Docker Compose (for full stack)

### Development Setup

```bash
# 1. Fork and clone the repository
git clone https://github.com/YOUR_USERNAME/tiktok.git
cd tiktok

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # or `.venv\Scripts\activate` on Windows

# 3. Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt  # Development tools

# 4. Install pre-commit hooks
pre-commit install

# 5. Copy environment template
cp .env.example .env
# Edit .env with your development credentials

# 6. Start backing services
docker compose up -d postgres redis

# 7. Run migrations
alembic upgrade head

# 8. Seed database (optional)
python -c "from app.services.style_presets import seed_builtin_presets; import asyncio; asyncio.run(seed_builtin_presets())"

# 9. Run tests to verify setup
pytest
```

### Development Services

```bash
# API server (with auto-reload)
uvicorn app.main:app --reload --log-config app/logging_config.py

# Workers (separate terminals)
celery -A app.workers.celery_app worker -Q media -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q ai -c 2 --loglevel=info
celery -A app.workers.celery_app worker -Q render -c 1 --loglevel=info

# Task monitoring
celery -A app.workers.celery_app flower --port=5555

# Test watcher (runs tests on file changes)
pytest-watch
```

---

## Development Workflow

### Branching Strategy

- `main` — Production-ready code
- `develop` — Integration branch for features
- `feature/*` — New features
- `fix/*` — Bug fixes
- `docs/*` — Documentation updates
- `refactor/*` — Code refactoring

### Feature Development

```bash
# 1. Pull latest changes
git checkout develop
git pull origin develop

# 2. Create feature branch
git checkout -b feature/my-awesome-feature

# 3. Make changes and commit
git add .
git commit -m "feat: add awesome feature"

# 4. Write tests
pytest tests/unit/test_my_feature.py

# 5. Ensure all tests pass
pytest

# 6. Push and create PR
git push origin feature/my-awesome-feature
```

---

## Code Standards

### Python Style

We follow [PEP 8](https://pep8.org/) with some modifications:

- **Line length:** 100 characters (not 80)
- **Imports:** Sorted with `isort`
- **Formatting:** Enforced with `black`
- **Linting:** `ruff` for fast linting
- **Type hints:** Required for all public functions

### Code Formatting

```bash
# Format code
black app/ tests/

# Sort imports
isort app/ tests/

# Lint
ruff check app/ tests/

# Type checking
mypy app/
```

### Docstrings

Use Google-style docstrings:

```python
def process_video(
    video_path: str,
    output_format: str = "mp4",
    *,
    quality: int = 23,
) -> Path:
    """Process a video file and return the output path.
    
    Args:
        video_path: Path to the input video file.
        output_format: Desired output format (mp4, webm, etc.).
        quality: Encoding quality (0-51, lower is better).
    
    Returns:
        Path to the processed video file.
    
    Raises:
        FileNotFoundError: If input video doesn't exist.
        RenderError: If FFmpeg processing fails.
    
    Example:
        >>> output = process_video("input.mp4", quality=20)
        >>> print(output)
        PosixPath('output/render_123.mp4')
    """
    ...
```

### Import Organization

```python
# 1. Standard library
import asyncio
import json
from typing import List, Optional

# 2. Third-party
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

# 3. Local application
from app.auth import get_current_user
from app.database import get_db
from app.models.db import Project, User
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| **Classes** | PascalCase | `StyleProfile`, `RenderEngine` |
| **Functions** | snake_case | `extract_style`, `render_video` |
| **Constants** | UPPER_SNAKE_CASE | `MAX_RETRIES`, `DEFAULT_TIMEOUT` |
| **Private** | Prefix with `_` | `_internal_helper` |
| **Async functions** | snake_case | `async def fetch_data()` |

---

## Testing Requirements

### Test Coverage

- **Minimum coverage:** 80% (enforced in CI)
- **New features:** Require 90%+ coverage
- **Critical paths:** 100% coverage (auth, billing, rendering)

### Test Types

```python
import pytest

# Unit tests — test individual functions/classes
@pytest.mark.unit
def test_style_extraction():
    """Test style profile extraction from reference transcript."""
    ...

# Integration tests — test multiple components together
@pytest.mark.integration
async def test_full_pipeline(db_session, test_project):
    """Test complete render pipeline from upload to download."""
    ...

# End-to-end tests — test entire user workflows
@pytest.mark.e2e
async def test_user_registration_to_first_render(client):
    """Test complete user journey: register → upload → render → download."""
    ...

# Slow tests — mark tests that take >1s
@pytest.mark.slow
async def test_actual_video_render():
    """Test real FFmpeg render (takes ~10 seconds)."""
    ...
```

### Running Tests

```bash
# All tests
pytest

# Specific markers
pytest -m unit
pytest -m integration
pytest -m "not slow"

# Specific file
pytest tests/unit/test_ai_orchestrator.py

# Specific test
pytest tests/unit/test_ai_orchestrator.py::test_extract_style

# With coverage
pytest --cov=app --cov-report=html
open htmlcov/index.html

# Parallel execution (faster)
pytest -n auto

# Verbose output
pytest -v

# Stop on first failure
pytest -x
```

### Test Fixtures

Use fixtures from `tests/conftest.py`:

```python
async def test_project_creation(db_session, test_user, test_workspace):
    """Test creating a project."""
    project = Project(
        workspace_id=test_workspace.id,
        title="Test Project",
    )
    db_session.add(project)
    await db_session.commit()
    
    assert project.id is not None
```

### Mocking External Services

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.unit
async def test_ai_call_with_mock(monkeypatch):
    """Test AI orchestrator without calling actual API."""
    mock_response = {
        "hook_style": "curiosity",
        "avg_cut_duration_sec": 1.5,
    }
    
    mock_call = AsyncMock(return_value=mock_response)
    monkeypatch.setattr(
        "app.services.ai_orchestrator.AIOrchestrator._call_json",
        mock_call,
    )
    
    orchestrator = AIOrchestrator()
    result = await orchestrator.extract_style(["reference transcript"])
    
    assert result["hook_style"] == "curiosity"
    mock_call.assert_called_once()
```

---

## Commit Guidelines

We follow [Conventional Commits](https://www.conventionalcommits.org/):

### Commit Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat` — New feature
- `fix` — Bug fix
- `docs` — Documentation changes
- `style` — Formatting, missing semicolons, etc.
- `refactor` — Code restructuring
- `perf` — Performance improvements
- `test` — Adding or updating tests
- `chore` — Maintenance tasks
- `ci` — CI/CD changes

### Examples

```bash
# Feature
git commit -m "feat(batch): add A/B variant generation"

# Bug fix
git commit -m "fix(render): handle missing audio tracks gracefully"

# Documentation
git commit -m "docs(api): update authentication examples"

# Breaking change
git commit -m "feat(auth)!: migrate to JWT from basic auth

BREAKING CHANGE: All endpoints now require JWT tokens in Authorization header.
See migration guide for details."
```

---

## Pull Request Process

### Before Submitting

- [ ] All tests pass (`pytest`)
- [ ] Code coverage ≥80% (`pytest --cov=app`)
- [ ] Code formatted (`black app/ tests/`)
- [ ] Imports sorted (`isort app/ tests/`)
- [ ] No linting errors (`ruff check app/ tests/`)
- [ ] Type checking passes (`mypy app/`)
- [ ] Documentation updated (if applicable)
- [ ] CHANGELOG.md updated (for user-facing changes)

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update

## Testing
Describe how you tested these changes

## Screenshots (if applicable)
Add screenshots for UI changes

## Checklist
- [ ] My code follows the style guidelines
- [ ] I have performed a self-review
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
- [ ] Any dependent changes have been merged and published
```

### Review Process

1. **Automated checks:** CI must pass before review
2. **Code review:** At least one approval required
3. **Testing:** Reviewer should test locally if possible
4. **Merge:** Squash and merge into `develop`

---

## Architecture Decisions

### When to Add a New Service

Create a new service module when:
- Logic is complex (>200 lines)
- Functionality is reusable across multiple endpoints
- External API integration is involved
- Domain logic should be separated from HTTP handlers

Example structure:
```python
# app/services/my_service.py
class MyService:
    """Service for X functionality."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def do_something(self, params) -> Result:
        """Do something useful."""
        ...
```

### When to Add a New API Router

Create a new router when:
- Logical grouping of ≥3 related endpoints
- RESTful resource management (CRUD operations)
- Clear domain boundary

### Database Migrations

```bash
# Create migration
alembic revision --autogenerate -m "add user preferences table"

# Review generated migration
vim migrations/versions/xxx_add_user_preferences_table.py

# Test migration
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# Commit migration
git add migrations/versions/xxx_add_user_preferences_table.py
git commit -m "feat(db): add user preferences table"
```

### Adding Dependencies

1. Add to `requirements.txt` with pinned version
2. Update `requirements-dev.txt` if dev-only
3. Document in PR why dependency is needed
4. Check license compatibility

### Error Handling

Use custom exceptions from `app/error_handling.py`:

```python
from app.error_handling import RenderError, retry_on_api_error

@retry_on_api_error(max_attempts=3)
async def risky_operation():
    """Operation that might fail and should be retried."""
    if something_wrong:
        raise RenderError("Specific error message for debugging")
```

### Logging

Use structured logging:

```python
import logging

logger = logging.getLogger(__name__)

logger.info(
    "Render completed",
    extra={
        "render_id": str(render.id),
        "duration_sec": duration,
        "output_size_mb": file_size / 1024 / 1024,
    },
)
```

---

## Performance Guidelines

### Async/Await

- Always use `async def` for I/O-bound operations
- Use `await` for database queries, HTTP requests, file I/O
- Don't block the event loop with CPU-intensive sync code

```python
# Good
async def process_data(db: AsyncSession):
    result = await db.execute(query)
    return result.scalars().all()

# Bad (blocks event loop)
async def process_data(db: AsyncSession):
    time.sleep(5)  # Don't do this!
```

### Database Queries

- Use `select()` with explicit columns instead of `SELECT *`
- Add indexes for frequently queried columns
- Use `joinedload()` to avoid N+1 queries
- Batch operations when possible

### Caching

Use the `@cached` decorator for expensive operations:

```python
from app.cache import cached

@cached(ttl=3600, key_prefix="style")
async def get_style_profile(profile_id: str):
    """Cached for 1 hour."""
    return await db.get(StyleProfile, profile_id)
```

---

## Security Guidelines

- **Never commit secrets** — Use environment variables
- **Validate all inputs** — Use Pydantic schemas
- **Sanitize user content** — Prevent injection attacks
- **Use prepared statements** — SQLAlchemy ORM handles this
- **Hash passwords** — Use `passlib` with bcrypt
- **Rate limit endpoints** — Use FastAPI rate limiting
- **HTTPS only in production** — Enforce in config

---

## Questions?

- Open a GitHub Discussion
- Join our Slack workspace
- Email: dev@example.com

**Thank you for contributing! 🎉**
