"""Job queue manager with background worker."""

import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from sqlalchemy import select, update
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.services.conversion_service import conversion_service
from app.services.lifecycle import prune_history

logger = logging.getLogger(__name__)


class JobQueue:
    """Manages job queue and background worker."""

    def __init__(self) -> None:
        self.current_job_id: Optional[int] = None
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self.worker_task: Optional[asyncio.Task] = None
        self.websocket_manager = None
        self.cancelled_job_ids: set[int] = set()
        self._wake_event: Optional[asyncio.Event] = None
        self._paused_event: Optional[asyncio.Event] = None

    def set_websocket_manager(self, ws_manager) -> None:
        """Set WebSocket manager for broadcasting updates."""
        self.websocket_manager = ws_manager

    def pause(self) -> None:
        """Pause the worker loop."""
        if self._paused_event:
            self._paused_event.clear()
        logger.info("Queue paused")

    def resume(self) -> None:
        """Resume the worker loop."""
        if self._paused_event:
            self._paused_event.set()
        if self._wake_event:
            self._wake_event.set()
        logger.info("Queue resumed")

    async def cancel_current_job(self) -> bool:
        """
        Cancel the currently processing job.

        Returns:
            True if job was cancelled, False if no job running
        """
        if self.current_process:
            logger.info(f"Cancelling job {self.current_job_id}")
            # Mark as cancelled
            if self.current_job_id:
                self.cancelled_job_ids.add(self.current_job_id)

            try:
                # Kill the entire process group
                os.killpg(os.getpgid(self.current_process.pid), signal.SIGTERM)

                # Give it a moment to terminate gracefully
                await asyncio.sleep(0.5)

                # If still running, force kill the group
                if self.current_process.returncode is None:
                    try:
                        os.killpg(os.getpgid(self.current_process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # Process already gone
                return True
            except ProcessLookupError:
                logger.warning(f"Process {self.current_job_id} already terminated")
                return True
            except Exception as e:
                logger.error(f"Error cancelling job {self.current_job_id}: {e}")
                return False
        return False

    async def add_job(self, job_id: int):
        """
        Signal worker that a new job is available.

        Args:
            job_id: Database job ID (unused, wake only)
        """
        if self._wake_event:
            self._wake_event.set()
        logger.info(f"Job {job_id} signaled worker")

        # Broadcast queue update
        if self.websocket_manager:
            status = self.get_queue_status()
            await self.websocket_manager.broadcast(
                {
                    "type": "queue_update",
                    "queue_size": status["pending_count"],
                    "active_job_id": status["active_job_id"],
                }
            )

    async def start_worker(self) -> None:
        """Start background worker task."""
        if self.running:
            logger.warning("Worker already running")
            return

        self._wake_event = asyncio.Event()
        self._paused_event = asyncio.Event()

        # Rehydrate pause state from DB (restart under paused stays paused)
        async with AsyncSessionLocal() as db:
            from app.models.app_state import AppState

            result = await db.execute(
                select(AppState).where(AppState.key == "queue_paused")
            )
            row = result.scalar_one_or_none()
            if row and row.value.lower() == "true":
                self._paused_event.clear()
                logger.info("Queue started in paused state")
            else:
                self._paused_event.set()

        self.running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Job queue worker started")

    async def stop_worker(self) -> None:
        """Stop background worker task."""
        if not self.running:
            return

        self.running = False
        if self._wake_event:
            self._wake_event.set()
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Job queue worker stopped")

    async def _worker_loop(self):
        """Background worker that processes jobs sequentially from DB."""
        logger.info("Worker loop started")

        while self.running:
            try:
                # Check pause state
                if self._paused_event and not self._paused_event.is_set():
                    try:
                        await asyncio.wait_for(self._paused_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                async with AsyncSessionLocal() as db:
                    # Load next pending job by queue_position
                    result = await db.execute(
                        select(Job)
                        .where(Job.status == "pending")
                        .order_by(
                            Job.queue_position.asc().nullslast(), Job.created_at.asc()
                        )
                        .limit(1)
                    )
                    job = result.scalar_one_or_none()

                if job is None:
                    # No pending jobs; wait for wake signal
                    if self._wake_event:
                        self._wake_event.clear()
                        try:
                            await asyncio.wait_for(self._wake_event.wait(), timeout=1.0)
                        except asyncio.TimeoutError:
                            pass
                    else:
                        await asyncio.sleep(1.0)
                    continue

                self.current_job_id = job.id
                logger.info(f"Processing job {job.id}")

                # Process the job
                await self._process_job(job.id)

                self.current_job_id = None

                # Broadcast queue update
                if self.websocket_manager:
                    status = self.get_queue_status()
                    await self.websocket_manager.broadcast(
                        {
                            "type": "queue_update",
                            "queue_size": status["pending_count"],
                            "active_job_id": status["active_job_id"],
                        }
                    )

            except asyncio.CancelledError:
                logger.info("Worker loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in worker loop: {e}", exc_info=True)
                self.current_job_id = None

    async def _process_job(self, job_id: int):
        """
        Process a single job.

        Args:
            job_id: Database job ID
        """
        async with AsyncSessionLocal() as db:
            try:
                # Fetch job from database
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()

                if not job:
                    logger.error(f"Job {job_id} not found in database")
                    return

                # Update status to processing
                job.status = "processing"  # type: ignore[assignment]
                job.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                await db.commit()

                # Broadcast status change
                if self.websocket_manager:
                    await self.websocket_manager.broadcast(
                        {
                            "type": "job_status",
                            "job_id": job_id,
                            "status": "processing",
                            "error": None,
                        }
                    )

                # Parse settings
                settings = json.loads(job.settings) if job.settings else {}  # type: ignore

                # Define progress callback
                async def on_progress(job_id: int, progress_data: dict):
                    """Callback for progress updates."""
                    async with AsyncSessionLocal() as progress_db:
                        try:
                            # Update values
                            update_values = {
                                "progress_percent": progress_data.get("percent", 0.0),
                                "current_fps": progress_data.get("fps"),
                                "eta_seconds": progress_data.get("eta_seconds"),
                            }

                            # Update log if available (allows real-time log viewing)
                            if "current_log" in progress_data:
                                update_values["log"] = progress_data["current_log"]

                            # Update database
                            await progress_db.execute(
                                update(Job)
                                .where(Job.id == job_id)
                                .values(**update_values)
                            )
                            await progress_db.commit()

                            # Broadcast to WebSocket clients
                            if self.websocket_manager:
                                await self.websocket_manager.broadcast(
                                    {
                                        "type": "job_progress",
                                        "job_id": job_id,
                                        "data": progress_data,
                                    }
                                )
                        except Exception as e:
                            logger.error(
                                f"Error updating progress for job {job_id}: {e}"
                            )

                # Define process callback to store reference
                async def on_process(process):
                    """Callback to store process reference for cancellation."""
                    self.current_process = process

                # Execute conversion
                success, log = await conversion_service.convert_file(
                    job_id=job_id,
                    source_file=job.source_file,  # type: ignore
                    output_file=job.output_file,  # type: ignore
                    conversion_settings=settings,
                    progress_callback=on_progress,
                    process_callback=on_process,
                )

                # Clear process reference
                self.current_process = None

                # Check if job was explicitly cancelled
                if job_id in self.cancelled_job_ids:
                    job.status = "cancelled"  # type: ignore[assignment]
                    job.error_message = "Cancelled by user"  # type: ignore[assignment]
                    self.cancelled_job_ids.remove(job_id)
                    success = False
                else:
                    # Update final status
                    job.status = "completed" if success else "failed"  # type: ignore[assignment]
                    if not success:
                        # Extract error message from log
                        error_lines = [
                            line
                            for line in log.split("\n")
                            if line.startswith("ERROR:")
                        ]
                        job.error_message = (
                            error_lines[-1] if error_lines else "Conversion failed"  # type: ignore[assignment]
                        )

                job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                job.progress_percent = 100.0 if success else job.progress_percent  # type: ignore[assignment]
                job.log = log  # type: ignore[assignment]

                # Calculate file sizes for completed jobs
                if success:
                    try:
                        source_path = Path(job.source_file)
                        output_path = Path(job.output_file)
                        if source_path.exists():
                            job.source_size_bytes = source_path.stat().st_size  # type: ignore[assignment]
                        if output_path.exists():
                            job.output_size_bytes = output_path.stat().st_size  # type: ignore[assignment]
                    except Exception as e:
                        logger.warning(
                            f"Could not calculate file sizes for job {job_id}: {e}"
                        )

                await db.commit()

                # Broadcast final status
                if self.websocket_manager:
                    await self.websocket_manager.broadcast(
                        {
                            "type": "job_status",
                            "job_id": job_id,
                            "status": job.status,
                            "error": job.error_message,
                            "source_size_bytes": job.source_size_bytes,
                            "output_size_bytes": job.output_size_bytes,
                        }
                    )

                logger.info(f"Job {job_id} finished with status: {job.status}")

            except Exception as e:
                logger.error(f"Error processing job {job_id}: {e}", exc_info=True)

                # Mark job as failed
                try:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one_or_none()
                    if job:
                        job.status = "failed"  # type: ignore[assignment]
                        job.error_message = str(e)  # type: ignore[assignment]
                        job.completed_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                        await db.commit()

                        if self.websocket_manager:
                            await self.websocket_manager.broadcast(
                                {
                                    "type": "job_status",
                                    "job_id": job_id,
                                    "status": "failed",
                                    "error": str(e),
                                }
                            )
                except Exception as db_error:
                    logger.error(f"Error updating failed job {job_id}: {db_error}")

        # Prune history after each finished job
        try:
            await prune_history()
        except Exception as e:
            logger.error(f"Error pruning history: {e}")

    def get_queue_status(self) -> dict:
        """Get current queue status (synchronous, returns last known pending count)."""
        return {
            "pending_count": 0,
            "active_job_id": self.current_job_id,
            "running": self.running,
        }

    async def get_queue_status_async(self) -> dict:
        """Get current queue status asynchronously."""
        async with AsyncSessionLocal() as db:
            from sqlalchemy import func

            result = await db.execute(
                select(func.count()).select_from(
                    select(Job).where(Job.status == "pending").subquery()
                )
            )
            pending_count = result.scalar() or 0

        return {
            "pending_count": pending_count,
            "active_job_id": self.current_job_id,
            "running": self.running,
        }


# Global job queue instance
job_queue = JobQueue()
