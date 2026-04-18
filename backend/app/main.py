"""Main FastAPI application."""

import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from app.config import settings
from app.database import init_db

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure logging output if Uvicorn hijacked the root logger but didn't set level/handlers as expected
if not logging.getLogger().handlers:
    console = logging.StreamHandler()
    console.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logging.getLogger().addHandler(console)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    logger.info("Starting conversion service...")

    settings.ensure_directories()

    # Run Alembic migrations
    from alembic import command
    from alembic.config import Config

    cfg = Config("/app/alembic.ini")
    cfg.set_main_option("script_location", "/app/alembic")
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("Database migrated to head")

    await init_db()

    # Sync built-in presets and recover jobs
    from app.services.lifecycle import (
        sync_builtin_presets,
        recover_interrupted_jobs,
        prune_history,
    )

    await sync_builtin_presets()
    await recover_interrupted_jobs()
    await prune_history()

    # Import and set up WebSocket manager
    from app.services.websocket_manager import websocket_manager
    from app.services.job_queue import job_queue

    # Connect WebSocket manager to job queue
    job_queue.set_websocket_manager(websocket_manager)

    # Start job queue worker
    await job_queue.start_worker()

    yield

    # Shutdown
    logger.info("Shutting down conversion service...")
    await job_queue.stop_worker()

    # Clean up temp directory on shutdown
    import shutil

    temp_path = Path(settings.TEMP_DIR)
    if temp_path.exists():
        try:
            for item in temp_path.iterdir():
                if item.is_file():
                    item.unlink()
                    logger.info(f"Cleaned temp file on shutdown: {item.name}")
                elif item.is_dir():
                    shutil.rmtree(item)
                    logger.info(f"Cleaned temp directory on shutdown: {item.name}")
            logger.info("Temp directory cleaned on shutdown")
        except Exception as e:
            logger.error(f"Error cleaning temp directory on shutdown: {e}")


# Create FastAPI app
app = FastAPI(
    title="Video Conversion Service",
    description="Web-based video conversion service with real-time progress tracking",
    version="1.0.0",
    lifespan=lifespan,
)

# Add GZip Middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
from app.routes import files, jobs, websocket, presets, queue  # noqa: E402

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(websocket.router, tags=["websocket"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from app.services.job_queue import job_queue

    queue_status = await job_queue.get_queue_status_async()

    return {
        "status": "healthy",
        "pending_count": queue_status["pending_count"],
        "active_job": queue_status["active_job_id"],
    }


# Mount static files for frontend (must be last!)
app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")
