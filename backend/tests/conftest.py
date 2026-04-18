"""Test configuration and fixtures."""

import asyncio
import os
import tempfile
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

# Create a temp DB file before any app imports
TEST_DB_FILE = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TEST_DB_FILE.close()
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DB_FILE.name}"

# Patch config BEFORE importing app modules
import app.config as _config_module  # noqa: E402

_config_module.settings.DATABASE_URL = TEST_DATABASE_URL
_config_module.settings.DATABASE_PATH = TEST_DB_FILE.name

# Patch other things
import starlette.staticfiles  # noqa: E402
import alembic.command  # noqa: E402
import app.services.lifecycle as _lifecycle_module  # noqa: E402

original_staticfiles_init = starlette.staticfiles.StaticFiles.__init__


def _patched_staticfiles_init(
    self,
    directory=None,
    packages=None,
    html=False,
    check_dir=True,
    follow_symlink=False,
):
    if directory == "/app/frontend":
        directory = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
        )
    original_staticfiles_init(
        self,
        directory=directory,
        packages=packages,
        html=html,
        check_dir=check_dir,
        follow_symlink=follow_symlink,
    )


starlette.staticfiles.StaticFiles.__init__ = _patched_staticfiles_init  # type: ignore


async def _noop_async(*a, **k):
    pass


alembic.command.upgrade = lambda *a, **k: None
_lifecycle_module.sync_builtin_presets = _noop_async
_lifecycle_module.recover_interrupted_jobs = _noop_async
_lifecycle_module.prune_history = _noop_async
_config_module.settings.ensure_directories = lambda *a, **k: None  # type: ignore

# Prevent background worker from starting in tests (avoids DB lock races)
from app.services.job_queue import JobQueue, job_queue  # noqa: E402

_original_start_worker = JobQueue.start_worker
_original_stop_worker = JobQueue.stop_worker
JobQueue.start_worker = _noop_async  # type: ignore
JobQueue.stop_worker = _noop_async  # type: ignore
job_queue._wake_event = asyncio.Event()
job_queue._paused_event = asyncio.Event()
job_queue._paused_event.set()

# Now import app modules (they will use the patched DATABASE_URL)
from app.database import Base, get_db, AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.preset import Preset  # noqa: E402
from app.models.app_state import AppState  # noqa: E402


@pytest_asyncio.fixture(scope="function")
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
def client():
    async def _get_test_db():
        async with AsyncSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _get_test_db
    asyncio.run(_init_db())

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()
    from app.services.job_queue import job_queue

    asyncio.run(job_queue.stop_worker())
    asyncio.run(_drop_db())


@pytest.fixture(scope="function")
def seeded_client(client):
    asyncio.run(_seed_test_data())
    return client


@pytest.fixture
def original_jobqueue_methods():
    return {"start": _original_start_worker, "stop": _original_stop_worker}


async def _init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop_db():
    async with AsyncSessionLocal() as db:
        from sqlalchemy import text

        # Clear all tables instead of dropping to avoid schema recreation issues
        for table in reversed(Base.metadata.sorted_tables):
            await db.execute(text(f"DELETE FROM {table.name}"))
        await db.commit()


async def _seed_test_data():
    async with AsyncSessionLocal() as db:
        db.add(
            Preset(
                name="Default",
                description="General purpose",
                is_builtin=True,
                crf=26,
                encoder_preset=4,
                svt_params="tune=0:film-grain=8",
                audio_bitrate="96k",
                skip_crop_detect=False,
                max_resolution=1080,
            )
        )
        db.add(
            Preset(
                name="Animated",
                description="Animated",
                is_builtin=True,
                crf=35,
                encoder_preset=4,
                svt_params="tune=0",
                audio_bitrate="96k",
                skip_crop_detect=False,
                max_resolution=1080,
            )
        )
        db.add(
            Preset(
                name="Grainy",
                description="Grainy",
                is_builtin=True,
                crf=26,
                encoder_preset=4,
                svt_params="tune=0:film-grain=16",
                audio_bitrate="96k",
                skip_crop_detect=False,
                max_resolution=1080,
            )
        )
        db.add(AppState(key="default_preset_id", value="1"))
        db.add(AppState(key="queue_paused", value="false"))
        await db.commit()
