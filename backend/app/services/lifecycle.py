"""Lifecycle helpers for startup/shutdown."""

import logging
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, delete, func
from app.database import AsyncSessionLocal
from app.models.preset import Preset
from app.models.job import Job
from app.config import settings

logger = logging.getLogger(__name__)

BUILTIN_PRESETS = [
    {
        "name": "Default",
        "description": "General purpose preset",
        "is_builtin": True,
        "crf": 26,
        "encoder_preset": 4,
        "svt_params": "tune=0:film-grain=8",
        "audio_bitrate": "96k",
        "skip_crop_detect": False,
        "max_resolution": 1080,
    },
    {
        "name": "Animated",
        "description": "Optimized for animated content",
        "is_builtin": True,
        "crf": 35,
        "encoder_preset": 4,
        "svt_params": "tune=0:enable-qm=1:max-tx-size=32",
        "audio_bitrate": "96k",
        "skip_crop_detect": False,
        "max_resolution": 1080,
    },
    {
        "name": "Grainy",
        "description": "Optimized for grainy film content",
        "is_builtin": True,
        "crf": 26,
        "encoder_preset": 4,
        "svt_params": "tune=0:film-grain=16:film-grain-denoise=1",
        "audio_bitrate": "96k",
        "skip_crop_detect": False,
        "max_resolution": 1080,
    },
]


async def sync_builtin_presets():
    """Sync built-in presets to ensure they match code defaults."""
    async with AsyncSessionLocal() as db:
        for builtin in BUILTIN_PRESETS:
            result = await db.execute(
                select(Preset).where(Preset.name == builtin["name"])
            )
            preset = result.scalar_one_or_none()
            if preset is None:
                preset = Preset(**builtin)
                db.add(preset)
                logger.info(f"Created built-in preset: {builtin['name']}")
            elif preset.is_builtin:
                # Update fields to match code
                preset.crf = builtin["crf"]
                preset.encoder_preset = builtin["encoder_preset"]
                preset.svt_params = builtin["svt_params"]
                preset.audio_bitrate = builtin["audio_bitrate"]
                preset.skip_crop_detect = builtin["skip_crop_detect"]
                preset.max_resolution = builtin["max_resolution"]
                preset.description = builtin["description"]
                logger.info(f"Synced built-in preset: {builtin['name']}")
        await db.commit()


async def recover_interrupted_jobs():
    """Mark any processing jobs as failed due to restart."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Job)
            .where(Job.status == "processing")
            .values(
                status="failed",
                error_message="Interrupted by service restart",
                completed_at=datetime.now(timezone.utc),
            )
        )
        if result.rowcount:
            logger.info(f"Marked {result.rowcount} interrupted job(s) as failed")
        await db.commit()


async def prune_history():
    """Prune old finished jobs based on retention settings."""
    retention_days = settings.JOB_HISTORY_RETENTION_DAYS
    max_rows = settings.JOB_HISTORY_MAX_ROWS

    if retention_days <= 0 and max_rows <= 0:
        return

    async with AsyncSessionLocal() as db:
        deleted_total = 0

        if retention_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            result = await db.execute(
                delete(Job).where(
                    Job.status.in_(["completed", "failed", "cancelled"]),
                    Job.completed_at < cutoff,
                )
            )
            deleted_total += result.rowcount

        if max_rows > 0:
            # Count finished rows
            count_result = await db.execute(
                select(func.count()).select_from(
                    select(Job)
                    .where(Job.status.in_(["completed", "failed", "cancelled"]))
                    .subquery()
                )
            )
            finished_count = count_result.scalar() or 0

            excess = finished_count - max_rows
            if excess > 0:
                # Find IDs of oldest excess rows
                subq = (
                    select(Job.id)
                    .where(Job.status.in_(["completed", "failed", "cancelled"]))
                    .order_by(Job.completed_at.asc())
                    .limit(excess)
                    .subquery()
                )
                result = await db.execute(
                    delete(Job).where(Job.id.in_(select(subq.c.id)))
                )
                deleted_total += result.rowcount

        if deleted_total > 0:
            logger.info(f"Pruned {deleted_total} finished job(s) from history")

        await db.commit()
