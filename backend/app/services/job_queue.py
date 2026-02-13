"""Job queue manager with background worker."""
import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select, update
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.services.conversion_service import conversion_service

logger = logging.getLogger(__name__)


class JobQueue:
    """Manages job queue and background worker."""

    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.current_job_id: Optional[int] = None
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self.worker_task: Optional[asyncio.Task] = None
        self.websocket_manager = None
        self.cancelled_job_ids = set()
        self.removed_job_ids = set()

    def set_websocket_manager(self, ws_manager):
        """Set WebSocket manager for broadcasting updates."""
        self.websocket_manager = ws_manager

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
                        pass # Process already gone
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
        Add job to queue.

        Args:
            job_id: Database job ID
        """
        await self.queue.put(job_id)
        logger.info(f"Job {job_id} added to queue. Queue size: {self.queue.qsize()}")

        # Broadcast queue update
        if self.websocket_manager:
            await self.websocket_manager.broadcast({
                "type": "queue_update",
                "queue_size": self.queue.qsize(),
                "active_job_id": self.current_job_id,
            })

    async def start_worker(self):
        """Start background worker task."""
        if self.running:
            logger.warning("Worker already running")
            return

        self.running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info("Job queue worker started")

    async def stop_worker(self):
        """Stop background worker task."""
        if not self.running:
            return

        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info("Job queue worker stopped")

    async def _worker_loop(self):
        """Background worker that processes jobs sequentially."""
        logger.info("Worker loop started")

        while self.running:
            try:
                # Get next job from queue (with timeout to allow checking running flag)
                try:
                    job_id = await asyncio.wait_for(self.queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                # Skip jobs that were removed while queued
                if job_id in self.removed_job_ids:
                    self.removed_job_ids.discard(job_id)
                    self.queue.task_done()
                    continue

                self.current_job_id = job_id
                logger.info(f"Processing job {job_id}")

                # Process the job
                await self._process_job(job_id)

                # Mark task as done
                self.queue.task_done()
                self.current_job_id = None

                # Broadcast queue update
                if self.websocket_manager:
                    await self.websocket_manager.broadcast({
                        "type": "queue_update",
                        "queue_size": self.queue.qsize(),
                        "active_job_id": self.current_job_id,
                    })

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
                job.status = "processing"
                job.started_at = datetime.now(timezone.utc)
                await db.commit()

                # Broadcast status change
                if self.websocket_manager:
                    await self.websocket_manager.broadcast({
                        "type": "job_status",
                        "job_id": job_id,
                        "status": "processing",
                        "error": None,
                    })

                # Parse settings
                settings = json.loads(job.settings) if job.settings else {}

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
                                await self.websocket_manager.broadcast({
                                    "type": "job_progress",
                                    "job_id": job_id,
                                    "data": progress_data,
                                })
                        except Exception as e:
                            logger.error(f"Error updating progress for job {job_id}: {e}")

                # Define process callback to store reference
                async def on_process(process):
                    """Callback to store process reference for cancellation."""
                    self.current_process = process

                # Execute conversion
                success, log = await conversion_service.convert_file(
                    job_id=job_id,
                    source_file=job.source_file,
                    output_file=job.output_file,
                    conversion_settings=settings,
                    progress_callback=on_progress,
                    process_callback=on_process,
                )

                # Clear process reference
                self.current_process = None

                # Check if job was explicitly cancelled
                if job_id in self.cancelled_job_ids:
                    job.status = "cancelled"
                    job.error_message = "Cancelled by user"
                    self.cancelled_job_ids.remove(job_id)
                    success = False
                else:
                    # Update final status
                    job.status = "completed" if success else "failed"
                    if not success:
                        # Extract error message from log
                        error_lines = [line for line in log.split("\n") if line.startswith("ERROR:")]
                        job.error_message = error_lines[-1] if error_lines else "Conversion failed"

                job.completed_at = datetime.now(timezone.utc)
                job.progress_percent = 100.0 if success else job.progress_percent
                job.log = log

                await db.commit()

                # Broadcast final status
                if self.websocket_manager:
                    await self.websocket_manager.broadcast({
                        "type": "job_status",
                        "job_id": job_id,
                        "status": job.status,
                        "error": job.error_message,
                    })

                logger.info(f"Job {job_id} finished with status: {job.status}")

            except Exception as e:
                logger.error(f"Error processing job {job_id}: {e}", exc_info=True)

                # Mark job as failed
                try:
                    result = await db.execute(select(Job).where(Job.id == job_id))
                    job = result.scalar_one_or_none()
                    if job:
                        job.status = "failed"
                        job.error_message = str(e)
                        job.completed_at = datetime.now(timezone.utc)
                        await db.commit()

                        if self.websocket_manager:
                            await self.websocket_manager.broadcast({
                                "type": "job_status",
                                "job_id": job_id,
                                "status": "failed",
                                "error": str(e),
                            })
                except Exception as db_error:
                    logger.error(f"Error updating failed job {job_id}: {db_error}")

    def get_queue_status(self) -> dict:
        """Get current queue status."""
        return {
            "queue_size": self.queue.qsize(),
            "active_job_id": self.current_job_id,
            "running": self.running,
        }


# Global job queue instance
job_queue = JobQueue()
