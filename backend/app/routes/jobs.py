"""Job management API endpoints."""

import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.job import Job
from app.models.preset import Preset
from app.models.schemas import (
    JobCreate,
    JobBatchCreate,
    JobResponse,
    JobListResponse,
    JobCreateResponse,
    JobPatchRequest,
    JobPositionPatchRequest,
)
from app.services.job_queue import job_queue
from app.services.conversion_service import conversion_service
from app.utils.validation import validate_conversion_settings

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_job_settings(
    db: AsyncSession,
    preset_id: Optional[int],
    settings_override: Optional[dict],
    source_file: str,
):
    """Resolve job settings, preset link, and snapshot name."""
    preset = None
    if preset_id is not None:
        result = await db.execute(select(Preset).where(Preset.id == preset_id))
        preset = result.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Preset not found")

    if preset and not settings_override:
        # Use preset exactly
        settings = {
            "crf": preset.crf,
            "encoder_preset": preset.encoder_preset,
            "svt_params": preset.svt_params,
            "audio_bitrate": preset.audio_bitrate,
            "skip_crop_detect": preset.skip_crop_detect,
            "max_resolution": preset.max_resolution,
        }
        return preset.id, preset.name, settings

    if settings_override and not preset:
        # Ad-hoc custom settings
        settings = settings_override
        validate_conversion_settings(settings)
        return None, "Custom", settings

    if preset and settings_override:
        # Override preset with user tweaks
        settings = {
            "crf": settings_override.get("crf", preset.crf),
            "encoder_preset": settings_override.get(
                "encoder_preset", preset.encoder_preset
            ),
            "svt_params": settings_override.get("svt_params", preset.svt_params),
            "audio_bitrate": settings_override.get(
                "audio_bitrate", preset.audio_bitrate
            ),
            "skip_crop_detect": settings_override.get(
                "skip_crop_detect", preset.skip_crop_detect
            ),
            "max_resolution": settings_override.get(
                "max_resolution", preset.max_resolution
            ),
        }
        validate_conversion_settings(settings)
        return preset.id, f"{preset.name} (modified)", settings

    raise HTTPException(status_code=400, detail="Invalid preset/settings combination")


async def _assign_queue_position(db: AsyncSession) -> int:
    """Assign the next queue position for pending jobs."""
    result = await db.execute(
        select(func.max(Job.queue_position)).where(Job.status == "pending")
    )
    max_pos = result.scalar() or 0
    return max_pos + 1


@router.post("", response_model=JobCreateResponse)
async def create_job(job_data: JobCreate, db: AsyncSession = Depends(get_db)):
    """Create a single conversion job."""
    try:
        settings_override = (
            job_data.settings.model_dump() if job_data.settings else None
        )
        preset_id, preset_name_snapshot, settings = await _resolve_job_settings(
            db, job_data.preset_id, settings_override, job_data.source_file
        )

        output_file = conversion_service.get_output_path(job_data.source_file)
        queue_position = await _assign_queue_position(db)

        job = Job(
            source_file=job_data.source_file,
            output_file=output_file,
            preset_id=preset_id,
            preset_name_snapshot=preset_name_snapshot,
            settings=json.dumps(settings),
            notes=job_data.notes,
            queue_position=queue_position,
            status="pending",
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        await job_queue.add_job(job.id)  # type: ignore

        logger.info(f"Created job {job.id} for {job_data.source_file}")
        return JobCreateResponse(job_ids=[job.id])  # type: ignore

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch", response_model=JobCreateResponse)
async def create_batch_jobs(
    batch_data: JobBatchCreate, db: AsyncSession = Depends(get_db)
):
    """Create multiple conversion jobs."""
    try:
        settings_override = (
            batch_data.settings.model_dump() if batch_data.settings else None
        )
        sorted_files = sorted(batch_data.files)
        job_ids = []

        for source_file in sorted_files:
            preset_id, preset_name_snapshot, settings = await _resolve_job_settings(
                db, batch_data.preset_id, settings_override, source_file
            )
            output_file = conversion_service.get_output_path(source_file)
            queue_position = await _assign_queue_position(db)

            job = Job(
                source_file=source_file,
                output_file=output_file,
                preset_id=preset_id,
                preset_name_snapshot=preset_name_snapshot,
                settings=json.dumps(settings),
                notes=batch_data.notes,
                queue_position=queue_position,
                status="pending",
            )
            db.add(job)
            await db.flush()
            job_ids.append(job.id)

        await db.commit()

        for job_id in job_ids:
            await job_queue.add_job(job_id)  # type: ignore

        if job_ids:
            logger.info(f"Created {len(job_ids)} batch jobs")

        return JobCreateResponse(job_ids=job_ids)  # type: ignore

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating batch jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = Query(
        None, description="Filter by status (comma-separated for multiple)"
    ),
    q: Optional[str] = Query(
        None, description="Search query for source_file or preset_name_snapshot"
    ),
    preset_id: Optional[int] = Query(None, description="Filter by preset_id"),
    date_from: Optional[str] = Query(
        None, description="Filter created_at from (ISO datetime)"
    ),
    date_to: Optional[str] = Query(
        None, description="Filter created_at to (ISO datetime)"
    ),
    sort: str = Query(
        "created_at", description="Sort field (created_at, completed_at)"
    ),
    order: str = Query("desc", description="Sort order (asc, desc)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List jobs with optional filtering."""
    try:
        query = select(Job)

        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            if statuses:
                query = query.where(Job.status.in_(statuses))

        if q:
            like_q = f"%{q}%"
            query = query.where(
                (Job.source_file.ilike(like_q))
                | (Job.preset_name_snapshot.ilike(like_q))
            )

        if preset_id is not None:
            query = query.where(Job.preset_id == preset_id)

        if date_from:
            query = query.where(Job.created_at >= date_from)
        if date_to:
            query = query.where(Job.created_at <= date_to)

        # Sorting
        sort_col = getattr(Job, sort, Job.created_at)
        if order.lower() == "desc":
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col.asc())

        # Count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()

        query = query.limit(limit).offset(offset)
        result = await db.execute(query)
        jobs = result.scalars().all()

        return JobListResponse(
            jobs=[JobResponse.model_validate(job) for job in jobs],
            total=total,  # type: ignore
        )

    except Exception as e:
        logger.error(f"Error listing jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Get job details by ID."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        return JobResponse.model_validate(job)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{job_id}")
async def patch_job(
    job_id: int, data: JobPatchRequest, db: AsyncSession = Depends(get_db)
):
    """Update user-editable job fields."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if data.notes is not None:
            job.notes = data.notes  # type: ignore[assignment]

        await db.commit()
        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error patching job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/{job_id}/position")
async def patch_job_position(
    job_id: int, data: JobPositionPatchRequest, db: AsyncSession = Depends(get_db)
):
    """Reorder a pending job."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "pending":
            raise HTTPException(
                status_code=409, detail="Cannot reorder non-pending job"
            )

        # Load all pending job ids ordered by queue_position
        pending_result = await db.execute(
            select(Job)
            .where(Job.status == "pending")
            .order_by(Job.queue_position.asc().nullslast(), Job.created_at.asc())
        )
        pending_jobs = list(pending_result.scalars().all())

        ids = [j.id for j in pending_jobs]
        if job_id not in ids:
            raise HTTPException(
                status_code=404, detail="Job not found in pending queue"
            )

        target_slot = min(max(data.absolute, 1), len(ids))
        ids.remove(job_id)  # type: ignore
        ids.insert(target_slot - 1, job_id)  # type: ignore

        for idx, jid in enumerate(ids, start=1):
            await db.execute(
                update(Job).where(Job.id == jid).values(queue_position=idx)
            )

        await db.commit()
        job_queue._wake_event.set()  # type: ignore
        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/retry", response_model=JobCreateResponse)
async def retry_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Retry a finished job with the same settings."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        settings = json.loads(job.settings) if job.settings else {}  # type: ignore
        validate_conversion_settings(settings)

        # Verify preset still exists
        preset_id = job.preset_id
        if preset_id is not None:
            preset_result = await db.execute(
                select(Preset).where(Preset.id == preset_id)
            )
            if preset_result.scalar_one_or_none() is None:
                preset_id = None  # type: ignore[assignment]

        queue_position = await _assign_queue_position(db)

        new_job = Job(
            source_file=job.source_file,
            output_file=job.output_file,
            preset_id=preset_id,
            preset_name_snapshot=job.preset_name_snapshot or "Custom",
            settings=json.dumps(settings),
            queue_position=queue_position,
            status="pending",
        )
        db.add(new_job)
        await db.commit()
        await db.refresh(new_job)

        await job_queue.add_job(new_job.id)  # type: ignore
        return JobCreateResponse(job_ids=[new_job.id])  # type: ignore

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/save-as-preset", response_model=dict)
async def save_job_as_preset(
    job_id: int,
    name: str = Query(...),
    description: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Save a job's settings as a new preset."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        from app.routes.presets import _check_name_collision

        if await _check_name_collision(db, name):
            raise HTTPException(status_code=409, detail="Preset name already exists")

        settings = json.loads(job.settings) if job.settings else {}  # type: ignore
        validate_conversion_settings(settings)

        preset = Preset(
            name=name,
            description=description,
            is_builtin=False,
            crf=settings.get("crf", 26),
            encoder_preset=settings.get("encoder_preset", 4),
            svt_params=settings.get("svt_params", ""),
            audio_bitrate=settings.get("audio_bitrate", "96k"),
            skip_crop_detect=settings.get("skip_crop_detect", False),
            max_resolution=settings.get("max_resolution", 1080),
        )
        db.add(preset)
        await db.commit()
        await db.refresh(preset)

        return {"id": preset.id, "name": preset.name}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving job as preset {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/queued")
async def clear_queued_jobs(db: AsyncSession = Depends(get_db)):
    """Clear all pending jobs."""
    try:
        result = await db.execute(delete(Job).where(Job.status == "pending"))
        deleted_count = result.rowcount  # type: ignore
        await db.commit()

        # Wake worker so it re-evaluates
        job_queue._wake_event.set()  # type: ignore

        logger.info(f"Cleared {deleted_count} queued jobs")
        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing queued jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/completed")
async def clear_completed_jobs(db: AsyncSession = Depends(get_db)):
    """Clear all completed, failed, and cancelled jobs."""
    try:
        result = await db.execute(
            delete(Job).where(Job.status.in_(["completed", "failed", "cancelled"]))
        )
        deleted_count = result.rowcount  # type: ignore
        await db.commit()

        logger.info(f"Cleared {deleted_count} finished jobs")
        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing finished jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/all")
async def clear_all_jobs(db: AsyncSession = Depends(get_db)):
    """Clear ALL jobs (including pending and processing)."""
    try:
        # Cancel any currently processing job first
        if job_queue.current_job_id:
            await job_queue.cancel_current_job()

        # Delete all jobs from database
        result = await db.execute(delete(Job))
        deleted_count = result.rowcount  # type: ignore
        await db.commit()

        # Wake worker
        job_queue._wake_event.set()  # type: ignore

        logger.info(f"Cleared all {deleted_count} jobs (force clear)")
        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing all jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/history")
async def delete_history_older_than(
    older_than: str = Query(..., description="ISO datetime cutoff"),
    db: AsyncSession = Depends(get_db),
):
    """Delete finished jobs older than a given timestamp."""
    try:
        result = await db.execute(
            delete(Job).where(
                Job.status.in_(["completed", "failed", "cancelled"]),
                Job.completed_at < older_than,
            )
        )
        deleted_count = result.rowcount  # type: ignore
        await db.commit()

        logger.info(f"Deleted {deleted_count} history jobs older than {older_than}")
        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error deleting history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{job_id}")
async def delete_or_cancel_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Cancel a pending/processing job, or delete a finished job from history."""
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status in ["pending", "processing"]:
            if job.status == "processing":
                if job_queue.current_job_id == job_id:
                    cancelled = await job_queue.cancel_current_job()
                    if not cancelled:
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to send cancel signal to running process",
                        )
                # Worker will handle the status update
            else:  # pending
                await db.delete(job)
                await db.commit()
                job_queue._wake_event.set()  # type: ignore

            logger.info(f"Cancelled job {job_id}")
            return {"success": True, "message": f"Job {job_id} cancelled"}

        elif job.status in ["completed", "failed", "cancelled"]:
            await db.delete(job)
            await db.commit()
            logger.info(f"Deleted job {job_id} from history")
            return {"success": True, "message": f"Job {job_id} deleted"}

        else:
            raise HTTPException(
                status_code=400, detail=f"Cannot delete job with status '{job.status}'"
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting/cancelling job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
