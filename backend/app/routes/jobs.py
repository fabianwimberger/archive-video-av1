"""Job management API endpoints."""

import asyncio
import json
import logging

from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.job import Job
from app.models.schemas import (
    JobCreate,
    JobBatchCreate,
    JobResponse,
    JobListResponse,
    JobCreateResponse,
)
from app.services.job_queue import job_queue
from app.services.conversion_service import conversion_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("", response_model=JobCreateResponse)
async def create_job(job_data: JobCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a single conversion job.

    Args:
        job_data: Job creation data
        db: Database session

    Returns:
        Created job IDs
    """
    try:
        # Create job settings
        settings = job_data.settings.model_dump() if job_data.settings else {}

        # Calculate output file path
        output_file = conversion_service.get_output_path(job_data.source_file)

        # Create job
        job = Job(
            source_file=job_data.source_file,
            output_file=output_file,
            mode=job_data.mode,
            settings=json.dumps(settings),
            status="pending",
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        # Add to queue
        await job_queue.add_job(job.id)

        logger.info(f"Created job {job.id} for {job_data.source_file}")

        return JobCreateResponse(job_ids=[job.id])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/batch", response_model=JobCreateResponse)
async def create_batch_jobs(
    batch_data: JobBatchCreate, db: AsyncSession = Depends(get_db)
):
    """
    Create multiple conversion jobs.

    Args:
        batch_data: Batch job creation data
        db: Database session

    Returns:
        Created job IDs
    """
    try:
        settings = batch_data.settings.model_dump() if batch_data.settings else {}
        job_ids = []

        # Sort files alphabetically for processing
        sorted_files = sorted(batch_data.files)

        for source_file in sorted_files:
            output_file = conversion_service.get_output_path(source_file)
            job = Job(
                source_file=source_file,
                output_file=output_file,
                mode=batch_data.mode,
                settings=json.dumps(settings),
                status="pending",
            )
            db.add(job)
            await db.flush()
            job_ids.append(job.id)

        await db.commit()

        # Add all newly created jobs to queue
        for job_id in job_ids:
            await job_queue.add_job(job_id)

        if job_ids:
            logger.info(f"Created {len(job_ids)} batch jobs")

        return JobCreateResponse(job_ids=job_ids)

    except Exception as e:
        logger.error(f"Error creating batch jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=JobListResponse)
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """
    List jobs with optional filtering.

    Args:
        status: Optional status filter
        limit: Maximum number of jobs to return
        offset: Offset for pagination
        db: Database session

    Returns:
        List of jobs and total count
    """
    try:
        # Build query
        query = select(Job).order_by(Job.created_at.desc())

        if status:
            query = query.where(Job.status == status)

        # Get total count
        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar()

        # Get jobs
        query = query.limit(limit).offset(offset)
        result = await db.execute(query)
        jobs = result.scalars().all()

        return JobListResponse(
            jobs=[JobResponse.model_validate(job) for job in jobs],
            total=total,
        )

    except Exception as e:
        logger.error(f"Error listing jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get job details by ID.

    Args:
        job_id: Job ID
        db: Database session

    Returns:
        Job details
    """
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


@router.delete("/queued")
async def clear_queued_jobs(db: AsyncSession = Depends(get_db)):
    """
    Clear all pending (queued) jobs.

    Args:
        db: Database session

    Returns:
        Number of jobs deleted
    """
    try:
        # Mark pending jobs as removed so worker skips them
        result = await db.execute(select(Job).where(Job.status == "pending"))
        pending_jobs = result.scalars().all()
        for job in pending_jobs:
            job_queue.removed_job_ids.add(job.id)

        # Delete pending jobs from database
        result = await db.execute(delete(Job).where(Job.status == "pending"))
        deleted_count = result.rowcount
        await db.commit()

        logger.info(f"Cleared {deleted_count} queued jobs")

        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing queued jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/completed")
async def clear_completed_jobs(db: AsyncSession = Depends(get_db)):
    """
    Clear all completed, failed, and cancelled jobs.

    Args:
        db: Database session

    Returns:
        Number of jobs deleted
    """
    try:
        result = await db.execute(
            delete(Job).where(Job.status.in_(["completed", "failed", "cancelled"]))
        )
        deleted_count = result.rowcount
        await db.commit()

        logger.info(f"Cleared {deleted_count} finished jobs")

        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing finished jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/all")
async def clear_all_jobs(db: AsyncSession = Depends(get_db)):
    """
    Clear ALL jobs (including pending and processing).
    WARNING: This will cancel running jobs!

    Args:
        db: Database session

    Returns:
        Number of jobs deleted
    """
    try:
        # Cancel any currently processing job first
        if job_queue.current_job_id:
            await job_queue.cancel_current_job()

        # Drain pending jobs from in-memory queue
        while not job_queue.queue.empty():
            try:
                job_queue.queue.get_nowait()
                job_queue.queue.task_done()
            except asyncio.QueueEmpty:
                break

        # Delete all jobs from database
        result = await db.execute(delete(Job))
        deleted_count = result.rowcount
        await db.commit()

        logger.info(f"Cleared all {deleted_count} jobs (force clear)")

        return {"deleted_count": deleted_count}

    except Exception as e:
        logger.error(f"Error clearing all jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{job_id}")
async def delete_or_cancel_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """
    Cancel a pending/processing job, or delete a finished job from history.
    """
    try:
        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Handle cancellation of active jobs
        if job.status in ["pending", "processing"]:
            if job.status == "processing":
                if job_queue.current_job_id == job_id:
                    cancelled = await job_queue.cancel_current_job()
                    if not cancelled:
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to send cancel signal to running process",
                        )
                # The worker will handle the status update
            else:  # "pending"
                job_queue.removed_job_ids.add(job_id)
                await db.delete(job)
                await db.commit()

            logger.info(f"Cancelled job {job_id}")
            return {"success": True, "message": f"Job {job_id} cancelled"}

        # Handle deletion of finished jobs
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
