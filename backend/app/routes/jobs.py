import json
import logging
import asyncio
import httpx
from pathlib import Path
from uuid import uuid4
from typing import Optional, cast
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy import select, delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.config import settings as app_settings
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
from app.utils.file_safety import validate_source_path

logger = logging.getLogger(__name__)

router = APIRouter()
_submission_lock = asyncio.Lock()


async def _serialize_submissions():
    async with _submission_lock:
        yield


async def _validate_destination(db: AsyncSession, source_file: str) -> str:
    output = Path(conversion_service.get_output_path(source_file))
    if output.exists() or output.is_symlink():
        raise HTTPException(status_code=409, detail="Conversion output already exists")
    result = await db.execute(
        select(Job.id)
        .where(
            Job.output_file == str(output),
            Job.status.in_(["pending", "processing"]),
        )
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409, detail="Conversion output is already queued"
        )
    return str(output)


async def _resolve_job_settings(
    db: AsyncSession,
    preset_id: Optional[int],
    settings_override: Optional[dict],
    source_file: str,
):
    preset = None
    if preset_id is not None:
        result = await db.execute(select(Preset).where(Preset.id == preset_id))
        preset = result.scalar_one_or_none()
        if not preset:
            raise HTTPException(status_code=404, detail="Preset not found")

    if preset and not settings_override:
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
        settings = settings_override
        validate_conversion_settings(settings)
        return None, "Custom", settings

    if preset and settings_override:
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
    result = await db.execute(
        select(func.max(Job.queue_position)).where(Job.status == "pending")
    )
    max_pos = result.scalar() or 0
    return max_pos + 1


def _assign_cluster_identity(job: Job) -> None:
    if job.id is None or job.cluster_job_id:
        return
    job.cluster_origin_node_id = app_settings.DISTRIBUTED_NODE_ID  # type: ignore[assignment]
    job.cluster_origin_job_id = job.id  # type: ignore[assignment]
    job.cluster_job_id = str(uuid4())  # type: ignore[assignment]


async def _leader_request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, object]] = None,
    json_body: Optional[dict] = None,
) -> dict:
    from app.services.distributed import LeaderRequestError, distributed_service

    try:
        return await distributed_service.request_leader(
            method, path, params=params, json_body=json_body
        )
    except LeaderRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _should_forward_to_leader(cluster: bool = True, mutation: bool = False) -> bool:
    if not cluster:
        return False
    from app.services.distributed import distributed_service

    if distributed_service.should_use_leader():
        return True
    if (
        mutation
        and app_settings.DISTRIBUTED_ENABLED
        and not distributed_service.holds_queue
    ):
        # Changes made before the takeover would be replaced by the shared queue.
        raise HTTPException(
            status_code=503,
            detail="The cluster leader is still taking over the queue; try again shortly",
        )
    return False


async def _node_request(node_id: str, method: str, path: str, json_body=None):
    from app.services.distributed import distributed_service

    peer = next(
        (peer for peer in distributed_service.peers() if peer.node_id == node_id), None
    )
    if peer is None:
        raise HTTPException(status_code=503, detail="Worker is unavailable")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.request(
                method,
                f"{peer.base_url}{path}",
                params={"cluster": "false", "node_id": node_id},
                json=json_body,
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=exc.response.status_code, detail="Worker request failed"
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Worker is unavailable") from exc


def _is_active_status_filter(status: Optional[str]) -> bool:
    if not status:
        return False
    statuses = {s.strip() for s in status.split(",") if s.strip()}
    return bool(statuses) and statuses.issubset({"pending", "processing"})


def _cluster_job_key(job: dict) -> tuple[str, str, str, str]:
    if job.get("cluster_job_id"):
        return (str(job["cluster_job_id"]), "", "", "")
    return (
        str(job.get("source_file") or ""),
        str(job.get("output_file") or ""),
        str(job.get("assigned_worker_id") or ""),
        str(job.get("status") or ""),
    )


def _cluster_job_rank(job: dict) -> tuple[int, int]:
    return (
        1 if job.get("remote_job_id") is not None else 0,
        1 if job.get("cluster_node_url") == app_settings.DISTRIBUTED_PUBLIC_URL else 0,
    )


def _dedupe_cluster_jobs(jobs: list[dict]) -> list[dict]:
    by_key: dict[tuple[str, str, str, str], dict] = {}
    for job in jobs:
        key = _cluster_job_key(job)
        existing = by_key.get(key)
        if existing is None or _cluster_job_rank(job) > _cluster_job_rank(existing):
            by_key[key] = job
    return list(by_key.values())


def _sort_job_dicts(jobs: list[dict], sort: str, order: str) -> list[dict]:
    reverse = order.lower() == "desc"

    def sort_value(job: dict):
        value = job.get(sort)
        if value is None:
            return ""
        return value

    return sorted(jobs, key=sort_value, reverse=reverse)


@router.post(
    "", response_model=JobCreateResponse, dependencies=[Depends(_serialize_submissions)]
)
async def create_job(job_data: JobCreate, db: AsyncSession = Depends(get_db)):
    """Create a single conversion job."""
    try:
        settings_override = (
            job_data.settings.model_dump() if job_data.settings else None
        )
        preset_id, preset_name_snapshot, settings = await _resolve_job_settings(
            db, job_data.preset_id, settings_override, job_data.source_file
        )

        if not job_data.local_only and _should_forward_to_leader(mutation=True):
            return JobCreateResponse(
                **await _leader_request(
                    "POST",
                    "/api/jobs",
                    json_body={
                        "source_file": job_data.source_file,
                        "settings": settings,
                        "notes": job_data.notes,
                    },
                )
            )

        if job_data.cluster_job_id:
            if not job_data.local_only:
                raise HTTPException(
                    status_code=422, detail="Cluster identity requires local_only"
                )
            existing = (
                await db.execute(
                    select(Job).where(Job.cluster_job_id == job_data.cluster_job_id)
                )
            ).scalar_one_or_none()
            # A failed or cancelled copy is from an earlier attempt that the
            # leader has since requeued; only live or completed work is reused.
            if existing and existing.status not in ("failed", "cancelled"):
                if (
                    existing.source_file != job_data.source_file
                    or json.loads(cast(str, existing.settings)) != settings
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="Cluster identity belongs to a different conversion",
                    )
                return JobCreateResponse(job_ids=[cast(int, existing.id)])
            if existing:
                await db.delete(existing)
                await db.flush()

        job_data.source_file = str(validate_source_path(job_data.source_file))
        output_file = await _validate_destination(db, job_data.source_file)
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
            cluster_job_id=job_data.cluster_job_id,
            cluster_origin_node_id=job_data.cluster_origin_node_id,
            cluster_origin_job_id=job_data.cluster_origin_job_id,
            assigned_worker_id=(
                app_settings.DISTRIBUTED_NODE_ID if job_data.local_only else None
            ),
            assigned_worker_name=(
                app_settings.DISTRIBUTED_NODE_NAME if job_data.local_only else None
            ),
        )

        db.add(job)
        await db.flush()
        _assign_cluster_identity(job)
        await db.commit()
        await db.refresh(job)

        await job_queue.add_job(job.id)  # type: ignore

        logger.info(f"Created job {job.id} for {job_data.source_file}")
        return JobCreateResponse(job_ids=[job.id])  # type: ignore

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating job: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/batch",
    response_model=JobCreateResponse,
    dependencies=[Depends(_serialize_submissions)],
)
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

        if not batch_data.local_only and _should_forward_to_leader(mutation=True):
            files_payload = []
            for source_file in sorted_files:
                (
                    _preset_id,
                    _preset_name_snapshot,
                    settings,
                ) = await _resolve_job_settings(
                    db, batch_data.preset_id, settings_override, source_file
                )
                files_payload.append((source_file, settings))

            leader_job_ids = []
            for source_file, settings in files_payload:
                data = await _leader_request(
                    "POST",
                    "/api/jobs",
                    json_body={
                        "source_file": source_file,
                        "settings": settings,
                        "notes": batch_data.notes,
                    },
                )
                leader_job_ids.extend(data.get("job_ids", []))
            return JobCreateResponse(job_ids=leader_job_ids)

        for source_file in sorted_files:
            source_file = str(validate_source_path(source_file))
            preset_id, preset_name_snapshot, settings = await _resolve_job_settings(
                db, batch_data.preset_id, settings_override, source_file
            )
            output_file = await _validate_destination(db, source_file)
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
                assigned_worker_id=(
                    app_settings.DISTRIBUTED_NODE_ID if batch_data.local_only else None
                ),
                assigned_worker_name=(
                    app_settings.DISTRIBUTED_NODE_NAME
                    if batch_data.local_only
                    else None
                ),
            )
            db.add(job)
            await db.flush()
            _assign_cluster_identity(job)
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
        if isinstance(e, ValueError):
            raise HTTPException(status_code=422, detail=str(e)) from e
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
    cluster: bool = Query(True, description="Include peer active jobs"),
    db: AsyncSession = Depends(get_db),
):
    """List jobs with optional filtering."""
    try:
        if _should_forward_to_leader(cluster):
            params: dict[str, object] = {
                "sort": sort,
                "order": order,
                "limit": limit,
                "offset": offset,
                "cluster": "true",
            }
            if status:
                params["status"] = status
            if q:
                params["q"] = q
            if preset_id is not None:
                params["preset_id"] = preset_id
            if date_from:
                params["date_from"] = date_from
            if date_to:
                params["date_to"] = date_to
            return JobListResponse(
                **await _leader_request("GET", "/api/jobs", params=params)
            )

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

        sort_col = getattr(Job, sort, Job.created_at)
        if order.lower() == "desc":
            query = query.order_by(sort_col.desc())
        else:
            query = query.order_by(sort_col.asc())

        aggregate_cluster = (
            cluster
            and app_settings.DISTRIBUTED_ENABLED
            and _is_active_status_filter(status)
        )

        if aggregate_cluster:
            from app.services.distributed import distributed_service

            result = await db.execute(query)
            local_jobs = []
            for job in result.scalars().all():
                response = JobResponse.model_validate(job)
                response.cluster_node_id = app_settings.DISTRIBUTED_NODE_ID
                response.cluster_node_name = app_settings.DISTRIBUTED_NODE_NAME
                response.cluster_node_url = distributed_service.public_url
                local_jobs.append(response.model_dump(mode="json"))

            peer_params: dict[str, object] = {
                "status": status,
                "sort": sort,
                "order": order,
                "limit": 1000,
                "offset": 0,
                "cluster": "false",
            }
            if q:
                peer_params["q"] = q
            if preset_id is not None:
                peer_params["preset_id"] = preset_id
            if date_from:
                peer_params["date_from"] = date_from
            if date_to:
                peer_params["date_to"] = date_to

            cluster_jobs = local_jobs + await distributed_service.list_peer_jobs(
                peer_params
            )
            merged_jobs = _sort_job_dicts(
                _dedupe_cluster_jobs(cluster_jobs), sort, order
            )
            total = len(merged_jobs)
            sliced_jobs = merged_jobs[offset : offset + limit]

            return JobListResponse(
                jobs=[JobResponse(**job) for job in sliced_jobs],
                total=total,
            )

        count_query = select(func.count()).select_from(query.subquery())
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        query = query.limit(limit).offset(offset)
        result = await db.execute(query)
        jobs = result.scalars().all()

        if not cluster:
            from app.services.distributed import distributed_service

            responses = []
            for job in jobs:
                response = JobResponse.model_validate(job)
                response.cluster_node_id = app_settings.DISTRIBUTED_NODE_ID
                response.cluster_node_name = app_settings.DISTRIBUTED_NODE_NAME
                response.cluster_node_url = distributed_service.public_url
                responses.append(response)
            return JobListResponse(jobs=responses, total=total)  # type: ignore

        return JobListResponse(
            jobs=[JobResponse.model_validate(job) for job in jobs],
            total=total,  # type: ignore
        )

    except Exception as e:
        logger.error(f"Error listing jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: int,
    node_id: Optional[str] = Query(None),
    cluster: bool = Query(True, description="Read from the selected leader"),
    include_log: bool = Query(True, description="Include the conversion log"),
    db: AsyncSession = Depends(get_db),
):
    """Get job details by ID."""
    try:
        if node_id and node_id != app_settings.DISTRIBUTED_NODE_ID:
            return JobResponse(
                **await _node_request(node_id, "GET", f"/api/jobs/{job_id}")
            )
        if not node_id and _should_forward_to_leader(cluster):
            return JobResponse(**await _leader_request("GET", f"/api/jobs/{job_id}"))

        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        response = JobResponse.model_validate(job)
        if not include_log:
            response.log = ""
        return response

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
        if _should_forward_to_leader(mutation=True):
            return await _leader_request(
                "PATCH", f"/api/jobs/{job_id}", json_body=data.model_dump()
            )

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
    job_id: int,
    data: JobPositionPatchRequest,
    node_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Reorder a pending job."""
    try:
        if node_id and node_id != app_settings.DISTRIBUTED_NODE_ID:
            return await _node_request(
                node_id, "PATCH", f"/api/jobs/{job_id}/position", data.model_dump()
            )
        if not node_id and _should_forward_to_leader(mutation=True):
            return await _leader_request(
                "PATCH", f"/api/jobs/{job_id}/position", json_body=data.model_dump()
            )

        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != "pending":
            raise HTTPException(
                status_code=409, detail="Cannot reorder non-pending job"
            )

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
        job_queue.wake()
        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reordering job {job_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{job_id}/retry",
    response_model=JobCreateResponse,
    dependencies=[Depends(_serialize_submissions)],
)
async def retry_job(job_id: int, db: AsyncSession = Depends(get_db)):
    """Retry a finished job with the same settings."""
    try:
        if _should_forward_to_leader(mutation=True):
            return JobCreateResponse(
                **await _leader_request("POST", f"/api/jobs/{job_id}/retry")
            )

        result = await db.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        settings = json.loads(job.settings) if job.settings else {}  # type: ignore
        validate_conversion_settings(settings)

        preset_id = job.preset_id
        if preset_id is not None:
            preset_result = await db.execute(
                select(Preset).where(Preset.id == preset_id)
            )
            if preset_result.scalar_one_or_none() is None:
                preset_id = None  # type: ignore[assignment]

        source_file = str(validate_source_path(cast(str, job.source_file)))
        output_file = await _validate_destination(db, source_file)
        queue_position = await _assign_queue_position(db)

        new_job = Job(
            source_file=job.source_file,
            output_file=output_file,
            preset_id=preset_id,
            preset_name_snapshot=job.preset_name_snapshot or "Custom",
            settings=json.dumps(settings),
            queue_position=queue_position,
            status="pending",
        )
        db.add(new_job)
        await db.flush()
        _assign_cluster_identity(new_job)
        await db.commit()
        await db.refresh(new_job)

        await job_queue.add_job(new_job.id)  # type: ignore
        return JobCreateResponse(job_ids=[new_job.id])  # type: ignore

    except HTTPException:
        raise
    except Exception as e:
        if isinstance(e, ValueError):
            raise HTTPException(status_code=422, detail=str(e)) from e
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
        if _should_forward_to_leader():
            params: dict[str, object] = {"name": name}
            if description is not None:
                params["description"] = description
            return await _leader_request(
                "POST", f"/api/jobs/{job_id}/save-as-preset", params=params
            )

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
async def clear_queued_jobs(
    cluster: bool = Query(True, description="Clear queued jobs across the cluster"),
    db: AsyncSession = Depends(get_db),
):
    """Clear all pending jobs."""
    try:
        if _should_forward_to_leader(cluster, mutation=True):
            return await _leader_request("DELETE", "/api/jobs/queued")

        result = await db.execute(delete(Job).where(Job.status == "pending"))
        deleted_count = result.rowcount or 0  # type: ignore
        await db.commit()

        if cluster and app_settings.DISTRIBUTED_ENABLED:
            from app.services.distributed import distributed_service

            deleted_count += await distributed_service.clear_peer_jobs(
                "/api/jobs/queued"
            )

        job_queue.wake()

        logger.info(f"Cleared {deleted_count} queued jobs")
        return {"deleted_count": deleted_count}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing queued jobs: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/completed")
async def clear_completed_jobs(db: AsyncSession = Depends(get_db)):
    """Clear all completed, failed, and cancelled jobs."""
    try:
        if _should_forward_to_leader():
            return await _leader_request("DELETE", "/api/jobs/completed")

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
async def clear_all_jobs(
    cluster: bool = Query(True, description="Clear active jobs across the cluster"),
    db: AsyncSession = Depends(get_db),
):
    """Clear ALL jobs (including pending and processing)."""
    try:
        if _should_forward_to_leader(cluster, mutation=True):
            return await _leader_request("DELETE", "/api/jobs/all")

        peer_deleted = 0
        if cluster and app_settings.DISTRIBUTED_ENABLED:
            from app.services.distributed import distributed_service

            peer_deleted = await distributed_service.clear_peer_jobs("/api/jobs/all")

        if job_queue.current_job_id:
            await job_queue.cancel_current_job()

        result = await db.execute(delete(Job))
        deleted_count = (result.rowcount or 0) + peer_deleted  # type: ignore
        await db.commit()

        job_queue.wake()

        logger.info(f"Cleared all {deleted_count} jobs (force clear)")
        return {"deleted_count": deleted_count}

    except HTTPException:
        raise
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
        if _should_forward_to_leader():
            return await _leader_request(
                "DELETE", "/api/jobs/history", params={"older_than": older_than}
            )

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
async def delete_or_cancel_job(
    job_id: int,
    node_id: Optional[str] = Query(None),
    cluster: bool = Query(True, description="Cancel/delete through the leader"),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending/processing job, or delete a finished job from history."""
    try:
        if node_id and node_id != app_settings.DISTRIBUTED_NODE_ID:
            return await _node_request(node_id, "DELETE", f"/api/jobs/{job_id}")
        if not node_id and _should_forward_to_leader(cluster, mutation=True):
            return await _leader_request("DELETE", f"/api/jobs/{job_id}")

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
                elif job.remote_job_id:
                    from app.services.distributed import distributed_service

                    # An unreachable worker stops the encode once it reconciles
                    # with the leader and finds the job cancelled.
                    await distributed_service.cancel_remote_job(job)
                    job.status = "cancelled"  # type: ignore[assignment]
                    job.error_message = "Cancelled by user"  # type: ignore[assignment]
                    await db.commit()
                    job_queue.wake()
                else:
                    raise HTTPException(
                        status_code=409,
                        detail="Worker state is unknown; cancellation cannot be confirmed",
                    )
                # Worker will handle the status update
            else:  # pending
                await db.delete(job)
                await db.commit()
                job_queue.wake()

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
