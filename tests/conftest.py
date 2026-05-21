"""Pytest configuration and fixtures."""

import asyncio
import os
import uuid
from pathlib import Path
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.main import app
from app.models.db import User, Workspace, Project, Asset, AssetType


# ── Test Settings ────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Override settings for testing."""
    return Settings(
        environment="testing",
        debug=True,
        database_url="sqlite+aiosqlite:///:memory:",
        database_echo=False,
        redis_url="redis://localhost:6379/15",
        storage_backend="local",
        storage_local_root="./test_storage",
        render_output_dir="./test_renders",
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "test-key"),
        secret_key="test-secret-key-change-in-production",
        allowed_origins=["http://localhost:3000", "http://testserver"],
        celery_broker_url="memory://",
        celery_result_backend="cache+memory://",
    )


@pytest.fixture(scope="session", autouse=True)
def override_settings(test_settings: Settings):
    """Override get_settings dependency."""
    app.dependency_overrides[get_settings] = lambda: test_settings
    yield
    app.dependency_overrides.clear()


# ── Database ─────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def async_engine(test_settings: Settings):
    """Create async engine for testing."""
    engine = create_async_engine(
        test_settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test."""
    async_session = sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest.fixture
def override_get_db(db_session: AsyncSession):
    """Override get_db dependency."""
    async def _get_test_db():
        yield db_session
    
    app.dependency_overrides[get_db] = _get_test_db
    yield
    app.dependency_overrides.pop(get_db, None)


# ── HTTP Client ──────────────────────────────────────────────────────────────


@pytest.fixture
def client(override_get_db) -> TestClient:
    """FastAPI test client."""
    return TestClient(app)


# ── Database Seeds ───────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def test_user(db_session: AsyncSession) -> User:
    """Create a test user."""
    user = User(
        email="test@example.com",
        name="Test User",
        plan="creator_pro",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_workspace(db_session: AsyncSession, test_user: User) -> Workspace:
    """Create a test workspace."""
    workspace = Workspace(
        owner_id=test_user.id,
        name="Test Workspace",
    )
    db_session.add(workspace)
    await db_session.commit()
    await db_session.refresh(workspace)
    return workspace


@pytest_asyncio.fixture
async def test_project(db_session: AsyncSession, test_workspace: Workspace) -> Project:
    """Create a test project."""
    project = Project(
        workspace_id=test_workspace.id,
        title="Test Project",
        goal="Create an engaging TikTok",
        target_platform="tiktok",
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)
    return project


@pytest_asyncio.fixture
async def test_asset(db_session: AsyncSession, test_project: Project, tmp_path: Path) -> Asset:
    """Create a test video asset."""
    # Create a dummy video file
    video_file = tmp_path / "test_video.mp4"
    video_file.write_text("fake video content")
    
    asset = Asset(
        project_id=test_project.id,
        type=AssetType.raw_video,
        filename="test_video.mp4",
        storage_url=str(video_file),
        duration_sec=10.0,
        width=1080,
        height=1920,
    )
    db_session.add(asset)
    await db_session.commit()
    await db_session.refresh(asset)
    return asset


# ── File System ──────────────────────────────────────────────────────────────


@pytest.fixture
def temp_storage(tmp_path: Path) -> Path:
    """Temporary storage directory."""
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return storage


@pytest.fixture
def temp_renders(tmp_path: Path) -> Path:
    """Temporary renders directory."""
    renders = tmp_path / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    return renders


# ── Mocks ────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_claude_response():
    """Mock Anthropic Claude API response."""
    return {
        "hook_style": "curiosity",
        "avg_cut_duration_sec": 1.5,
        "caption_style": "bold, all caps, 2-4 words",
        "caption_position": "lower_third",
        "caption_max_words": 4,
        "zoom_pattern": "zoom in on key words",
        "structure": "hook → problem → solution → CTA",
        "tone": "educational",
        "energy_curve": "high_start → sustain → peak_end",
        "ideal_duration_sec": 30.0,
        "music_style": "upbeat electronic",
    }


@pytest.fixture
def mock_edit_spec():
    """Mock edit specification."""
    return {
        "project_id": "test-project",
        "output": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "duration_sec": 15.0,
        },
        "tracks": {
            "video": [
                {
                    "asset_id": "test-asset-1",
                    "start": 0.0,
                    "end": 5.0,
                    "source_in": 0.0,
                    "source_out": 5.0,
                    "crop": "smart_center",
                    "motion": {"type": "zoom_in", "strength": 0.05},
                    "speed": 1.0,
                }
            ],
            "text": [
                {
                    "start": 0.5,
                    "end": 2.0,
                    "text": "WATCH THIS",
                    "style": "bold_kinetic_1",
                    "position": "lower_third",
                    "font_size": 64,
                    "color": "#FFFFFF",
                    "animation": "pop",
                }
            ],
            "audio": [],
        },
    }


@pytest.fixture(autouse=True)
def cleanup_test_dirs(test_settings: Settings):
    """Clean up test directories after each test."""
    yield
    
    # Cleanup
    import shutil
    
    for path in [test_settings.storage_local_root, test_settings.render_output_dir]:
        if os.path.exists(path) and "test_" in path:
            shutil.rmtree(path, ignore_errors=True)
