"""Queue management API endpoints."""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.app_state import AppState
from app.models.schemas import QueueStatusResponse
from app.services.job_queue import job_queue

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_queue_paused(db: AsyncSession) -> bool:
    result = await db.execute(select(AppState).where(AppState.key == "queue_paused"))
    row = result.scalar_one_or_none()
    return row.value.lower() == "true" if row else False


async def _set_queue_paused(db: AsyncSession, paused: bool) -> None:
    row = await db.get(AppState, "queue_paused")
    if row:
        row.value = "true" if paused else "false"  # type: ignore[assignment]
    else:
        db.add(AppState(key="queue_paused", value="true" if paused else "false"))
    await db.commit()


@router.get("", response_model=QueueStatusResponse)
async def get_queue_status(db: AsyncSession = Depends(get_db)):
    """Get queue status."""
    paused = await _get_queue_paused(db)
    status = await job_queue.get_queue_status_async()
    return {
        "paused": paused,
        "active_job_id": status["active_job_id"],
        "pending_count": status["pending_count"],
    }


@router.post("/pause")
async def pause_queue(db: AsyncSession = Depends(get_db)):
    """Pause the queue."""
    await _set_queue_paused(db, True)
    job_queue.pause()
    return {"success": True, "paused": True}


@router.post("/resume")
async def resume_queue(db: AsyncSession = Depends(get_db)):
    """Resume the queue."""
    await _set_queue_paused(db, False)
    job_queue.resume()
    return {"success": True, "paused": False}
