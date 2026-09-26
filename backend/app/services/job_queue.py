import asyncio
import json
import logging
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy import or_
from app.database import AsyncSessionLocal
from app.models.job import Job
from app.config import settings
from app.services.conversion_service import conversion_service
from app.services.lifecycle import prune_history

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(self) -> None:
        self.current_job_id: Optional[int] = None
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.running = False
        self.worker_task: Optional[asyncio.Task] = None
        self.distributed_task: Optional[asyncio.Task] = None
        self.websocket_manager = None
        self.cancelled_job_ids: dict[int, str] = {}
        self._wake_event: Optional[asyncio.Event] = None
        self._queue_changed_event = asyncio.Event()
        self._paused_event: Optional[asyncio.Event] = None
        self._dispatch_lock = asyncio.Lock()

    def set_websocket_manager(self, ws_manager) -> None:
        self.websocket_manager = ws_manager

    def pause(self) -> None:
        if self._paused_event:
            self._paused_event.clear()
        logger.info("Queue paused")

    def resume(self) -> None:
        if self._paused_event:
            self._paused_event.set()
        if self._wake_event:
            self._wake_event.set()
        logger.info("Queue resumed")

    async def cancel_current_job(self, reason: str = "Cancelled by user") -> bool:
        if self.current_process:
            logger.info(f"Cancelling job {self.current_job_id}")
            if self.current_job_id:
                self.cancelled_job_ids[self.current_job_id] = reason

            try:
                # The wrapper spawns ffmpeg and helpers, so signal the whole group.
                os.killpg(os.getpgid(self.current_process.pid), signal.SIGTERM)

                await asyncio.sleep(0.5)

                if self.current_process.returncode is None:
                    try:
                        os.killpg(os.getpgid(self.current_process.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                return True
            except ProcessLookupError:
                logger.warning(f"Process {self.current_job_id} already terminated")
                return True
            except Exception as e:
                logger.error(f"Error cancelling job {self.current_job_id}: {e}")
                return False
        return False

    async def add_job(self, job_id: int):
        self.wake()
        logger.info(f"Job {job_id} signaled worker")

        if self.websocket_manager:
            status = await self.get_queue_status_async()
            await self.websocket_manager.broadcast(
                {
                    "type": "queue_update",
                    "queue_size": status["pending_count"],
                    "active_job_id": status["active_job_id"],
                }
            )

    async def start_worker(self) -> None:
        if self.running:
            logger.warning("Worker already running")
            return

        self._wake_event = asyncio.Event()
        self._paused_event = asyncio.Event()

        # A restart while paused stays paused.
        async with AsyncSessionLocal() as db:
            from app.models.app_state import AppState

            result = await db.execute(
                select(AppState).where(AppState.key == "queue_paused")
            )
            row = result.scalar_one_or_none()
            if row and row.value and row.value.lower() == "true":
                self._paused_event.clear()
                logger.info("Queue started in paused state")
            else:
                self._paused_event.set()

        self.running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        if settings.DISTRIBUTED_ENABLED:
            from app.services.distributed import distributed_service

            await distributed_service.start()
            self.distributed_task = asyncio.create_task(self._distributed_loop())
        logger.info("Job queue worker started")

    async def stop_worker(self) -> None:
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
        if self.distributed_task:
            self.distributed_task.cancel()
            try:
                await self.distributed_task
            except asyncio.CancelledError:
                pass
            self.distributed_task = None
        self.current_process = None
        self.current_job_id = None
        if settings.DISTRIBUTED_ENABLED:
            from app.services.distributed import distributed_service

            await distributed_service.stop()
        logger.info("Job queue worker stopped")

    def _can_claim_unassigned_jobs(self) -> bool:
        if not settings.DISTRIBUTED_ENABLED:
            return True

        from app.services.distributed import distributed_service

        return distributed_service.holds_queue

    def _can_process_assigned_jobs_while_paused(self) -> bool:
        if not settings.DISTRIBUTED_ENABLED:
            return False

        from app.services.distributed import distributed_service

        return not distributed_service.is_leader

    async def _claim_next_job(self, db) -> Optional[Job]:
        worker_filter = Job.assigned_worker_id == settings.DISTRIBUTED_NODE_ID
        if self._can_claim_unassigned_jobs():
            worker_filter = or_(Job.assigned_worker_id.is_(None), worker_filter)

        result = await db.execute(
            select(Job.id)
            .where(
                Job.status == "pending",
                worker_filter,
            )
            .order_by(
                Job.queue_position.asc().nullslast(),
                Job.created_at.asc(),
            )
            .limit(1)
        )
        job_id = result.scalar_one_or_none()
        if job_id is None:
            return None

        claimed = await db.execute(
            update(Job)
            .where(
                Job.id == job_id,
                Job.status == "pending",
                worker_filter,
            )
            .values(
                status="processing",
                assigned_worker_id=settings.DISTRIBUTED_NODE_ID,
                assigned_worker_name=settings.DISTRIBUTED_NODE_NAME,
            )
        )
        await db.commit()
        if claimed.rowcount != 1:
            return None

        result = await db.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()

    async def _worker_loop(self):
        logger.info("Worker loop started")

        while self.running:
            try:
                if (
                    self._paused_event
                    and not self._paused_event.is_set()
                    and not self._can_process_assigned_jobs_while_paused()
                ):
                    try:
                        await asyncio.wait_for(self._paused_event.wait(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                async with self._dispatch_lock:
                    async with AsyncSessionLocal() as db:
                        job = await self._claim_next_job(db)

                if job is None:
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
                self.wake()

                await self._process_job(job.id)

                self.current_job_id = None
                self.wake()

                if self.websocket_manager:
                    status = await self.get_queue_status_async()
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

    async def _distributed_loop(self) -> None:
        """Coordinate remote jobs while the local worker is busy."""
        from app.services.distributed import distributed_service

        last_progress_sync = 0.0
        last_coordination = 0.0

        while self.running:
            try:
                now = time.monotonic()
                progress_interval = max(0.5, settings.DISTRIBUTED_PROGRESS_SECONDS)
                coordination_interval = max(1.0, settings.DISTRIBUTED_HEARTBEAT_SECONDS)

                if now - last_progress_sync >= progress_interval:
                    if await distributed_service.sync_remote_jobs(
                        self.websocket_manager
                    ):
                        self.wake()
                    last_progress_sync = now

                if (
                    self._queue_changed_event.is_set()
                    or now - last_coordination >= coordination_interval
                ):
                    self._queue_changed_event.clear()
                    if not distributed_service.is_leader:
                        await distributed_service.reconcile_with_leader()
                    elif await distributed_service.own_queue(self.websocket_manager):
                        if self._paused_event is None or self._paused_event.is_set():
                            async with self._dispatch_lock:
                                delegated = (
                                    await distributed_service.delegate_pending_jobs(
                                        self.websocket_manager
                                    )
                                )
                            if delegated and self._wake_event:
                                self._wake_event.set()
                        # Also renews this node's hold on the shared ledger.
                        await distributed_service.publish_queue()
                    last_coordination = now
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in distributed loop: %s", e, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._queue_changed_event.wait(),
                    timeout=min(progress_interval, coordination_interval),
                )
            except asyncio.TimeoutError:
                pass

    async def _process_job(self, job_id: int):
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(select(Job).where(Job.id == job_id))
                job = result.scalar_one_or_none()

                if not job:
                    logger.error(f"Job {job_id} not found in database")
                    return

                job.status = "processing"  # type: ignore[assignment]
                job.started_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                await db.commit()

                if self.websocket_manager:
                    await self.websocket_manager.broadcast(
                        {
                            "type": "job_status",
                            "job_id": job_id,
                            "status": "processing",
                            "error": None,
                        }
                    )

                settings = json.loads(job.settings) if job.settings else {}  # type: ignore

                async def on_progress(job_id: int, progress_data: dict):
                    async with AsyncSessionLocal() as progress_db:
                        try:
                            update_values = {
                                "progress_percent": progress_data.get("percent", 0.0),
                                "current_fps": progress_data.get("fps"),
                                "eta_seconds": progress_data.get("eta_seconds"),
                            }

                            # Stored so the log view can follow the encode live.
                            if "current_log" in progress_data:
                                update_values["log"] = progress_data["current_log"]

                            await progress_db.execute(
                                update(Job)
                                .where(Job.id == job_id)
                                .values(**update_values)
                            )
                            await progress_db.commit()

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

                async def on_process(process):
                    self.current_process = process

                success, log = await conversion_service.convert_file(
                    job_id=job_id,
                    source_file=job.source_file,  # type: ignore
                    output_file=job.output_file,  # type: ignore
                    conversion_settings=settings,
                    progress_callback=on_progress,
                    process_callback=on_process,
                )

                self.current_process = None

                if job_id in self.cancelled_job_ids:
                    job.status = "cancelled"  # type: ignore[assignment]
                    job.error_message = self.cancelled_job_ids.pop(job_id)  # type: ignore[assignment]
                    success = False
                else:
                    job.status = "completed" if success else "failed"  # type: ignore[assignment]
                    if not success:
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

        try:
            await prune_history()
        except Exception as e:
            logger.error(f"Error pruning history: {e}")

    def get_queue_status(self) -> dict:
        """Get current queue status (synchronous, returns last known pending count)."""
        return {
            "pending_count": len(self.cancelled_job_ids),
            "active_job_id": self.current_job_id,
            "running": self.running,
        }

    def wake(self) -> None:
        """Signal that the queue changed so the worker and cluster sync react."""
        if self._wake_event:
            self._wake_event.set()
        self._queue_changed_event.set()

    async def get_queue_status_async(self) -> dict:
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


job_queue = JobQueue()
