"""Main FastAPI application."""

import logging
import os
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

    # Clean slate: Delete database if it exists
    if os.path.exists(settings.DATABASE_PATH):
        try:
            os.remove(settings.DATABASE_PATH)
            logger.info(
                f"Deleted existing database at {settings.DATABASE_PATH} for clean startup"
            )
        except Exception as e:
            logger.error(f"Failed to delete database: {e}")

    settings.ensure_directories()
    await init_db()
    logger.info("Database initialized")

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
from app.routes import files, jobs, websocket  # noqa: E402

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(websocket.router, tags=["websocket"])


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from app.services.job_queue import job_queue

    queue_status = job_queue.get_queue_status()

    return {
        "status": "healthy",
        "queue_size": queue_status["queue_size"],
        "active_job": queue_status["active_job_id"],
    }


@app.get("/api/presets")
async def get_presets():
    """Get conversion presets."""
    from app.models.schemas import ConversionSettings

    return {
        "default": ConversionSettings(
            crf=26,
            preset=4,
            svt_params="tune=0:film-grain=8",
            audio_bitrate="96k",
            skip_crop_detect=False,
        ).model_dump(),
        "animated": ConversionSettings(
            crf=35,
            preset=4,
            svt_params="tune=0:enable-qm=1:max-tx-size=32",
            audio_bitrate="96k",
            skip_crop_detect=False,
        ).model_dump(),
        "grainy": ConversionSettings(
            crf=26,
            preset=4,
            svt_params="tune=0:film-grain=16:film-grain-denoise=1",
            audio_bitrate="96k",
            skip_crop_detect=False,
        ).model_dump(),
    }


# Mount static files for frontend (must be last!)
app.mount("/", StaticFiles(directory="/app/frontend", html=True), name="frontend")
