"""Main FastAPI application."""

import asyncio
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from app.config import APP_ROOT, ASSET_ROOT, settings
from app.database import init_db


def configure_logging(level_name: str | None = None) -> None:
    level = getattr(logging, level_name or settings.LOG_LEVEL)
    logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logging.getLogger().setLevel(level)
    # Uvicorn pins its own loggers to INFO before importing the app.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).setLevel(level)
    # Cluster and UI polling would otherwise log several requests per second.
    request_level = level if level == logging.DEBUG else max(level, logging.WARNING)
    for name in ("uvicorn.access", "httpx"):
        logging.getLogger(name).setLevel(request_level)


configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting conversion service...")

    settings.ensure_directories()

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(APP_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_ROOT / "alembic"))
    # alembic.ini would replace the handlers and level set by configure_logging.
    cfg.attributes["configure_logger"] = False
    await asyncio.to_thread(command.upgrade, cfg, "head")
    logger.info("Database migrated to head")

    await init_db()

    from app.services.settings_service import settings_service

    await settings_service.apply_startup()

    from app.services.lifecycle import (
        sync_builtin_presets,
        recover_interrupted_jobs,
        prune_history,
    )

    await sync_builtin_presets()
    await recover_interrupted_jobs()
    await prune_history()

    from app.services.websocket_manager import websocket_manager
    from app.services.job_queue import job_queue

    job_queue.set_websocket_manager(websocket_manager)

    await job_queue.start_worker()

    yield

    logger.info("Shutting down conversion service...")
    await job_queue.stop_worker()

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


app = FastAPI(
    title="Video Conversion Service",
    description="Web-based video conversion service with real-time progress tracking",
    version="1.10.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.routes import (  # noqa: E402
    cluster,
    files,
    jobs,
    websocket,
    presets,
    queue,
    settings as settings_routes,
)

app.include_router(files.router, prefix="/api/files", tags=["files"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(queue.router, prefix="/api/queue", tags=["queue"])
app.include_router(cluster.router, prefix="/api/cluster", tags=["cluster"])
app.include_router(settings_routes.router, prefix="/api/settings", tags=["settings"])
app.include_router(websocket.router, tags=["websocket"])


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    from app.services.job_queue import job_queue

    queue_status = await job_queue.get_queue_status_async()

    return {
        "status": "healthy",
        "pending_count": queue_status["pending_count"],
        "active_job": queue_status["active_job_id"],
        "distributed_enabled": settings.DISTRIBUTED_ENABLED,
        "node_id": settings.DISTRIBUTED_NODE_ID,
    }


# Mounted last so the catch-all path does not shadow the API routes.
app.mount(
    "/", StaticFiles(directory=str(ASSET_ROOT / "frontend"), html=True), name="frontend"
)
