"""Settings API endpoints."""

import os
import signal

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.models.schemas import SettingsUpdate
from app.services.job_queue import job_queue
from app.services.settings_service import SettingsLockedError, settings_service

router = APIRouter()


def exit_process() -> None:
    """Ask uvicorn for a graceful shutdown; Docker restarts the container."""
    os.kill(os.getpid(), signal.SIGTERM)


@router.get("")
async def read_settings():
    return {"settings": await settings_service.get_all()}


@router.put("")
async def update_settings(payload: SettingsUpdate):
    changes = payload.model_dump(mode="json", exclude_unset=True)
    try:
        return {"settings": await settings_service.update(changes)}
    except SettingsLockedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Set by the environment: {', '.join(exc.names)}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/restart")
async def restart_service(background_tasks: BackgroundTasks):
    if job_queue.current_job_id is not None or job_queue.current_process is not None:
        raise HTTPException(status_code=409, detail="A job is running on this node")

    background_tasks.add_task(exit_process)
    return {"success": True, "restarting": True}
