"""File system operations for browsing and file management."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.config import settings
from app.utils.ffprobe import get_video_info, has_converted_file
from app.models.job import Job
from app.utils.file_safety import output_lock

logger = logging.getLogger(__name__)


def _natural_sort_key(path: Path) -> list:
    """Split name into text/number chunks so 'Season 2' sorts before 'Season 10'."""
    return [
        int(chunk) if chunk.isdigit() else chunk.lower()
        for chunk in re.split(r"(\d+)", path.name)
    ]


def _directory_has_videos(directory: Path) -> bool:
    """Check if directory contains any video files (recursively)."""
    try:
        return any(
            any(directory.rglob(f"*{ext}")) for ext in FileService.VIDEO_EXTENSIONS
        )
    except (OSError, PermissionError):
        return False


class FileService:
    VIDEO_EXTENSIONS = {".mkv", ".mp4"}

    def __init__(self):
        self.source_mount = Path(settings.SOURCE_MOUNT)

    def _is_safe_path(self, path: Path) -> bool:
        """Check if path is within source mount (security check)."""
        try:
            resolved = path.resolve()
            return resolved.is_relative_to(self.source_mount.resolve())
        except (ValueError, RuntimeError):
            return False

    async def browse_directory(self, path: Optional[str] = None) -> Dict[str, Any]:
        """Browse directory and return files and subdirectories."""
        try:
            if path:
                target_path = (self.source_mount / path).resolve()
            else:
                target_path = self.source_mount

            if not self._is_safe_path(target_path):
                raise ValueError("Invalid path")

            if not target_path.exists() or not target_path.is_dir():
                raise ValueError("Path does not exist or is not a directory")

            directories = []
            files = []

            # One query for the whole directory instead of one per file.
            file_paths = []
            for item in sorted(target_path.iterdir(), key=_natural_sort_key):
                if item.is_file() and item.suffix.lower() in self.VIDEO_EXTENSIONS:
                    file_paths.append(str(item))

            last_jobs: dict[str, dict[str, Any]] = {}
            if file_paths:
                async with AsyncSessionLocal() as db:
                    from sqlalchemy import func

                    from sqlalchemy.orm import aliased

                    subq = (
                        select(
                            Job,
                            func.row_number()
                            .over(
                                partition_by=Job.source_file,
                                order_by=(
                                    Job.completed_at.desc().nullslast(),
                                    Job.created_at.desc(),
                                ),
                            )
                            .label("rn"),
                        )
                        .where(Job.source_file.in_(file_paths))
                        .subquery()
                    )
                    JobAlias = aliased(Job, subq)
                    result = await db.execute(select(JobAlias).where(subq.c.rn == 1))
                    for job in result.scalars().all():
                        fp = str(job.source_file)
                        last_jobs[fp] = {
                            "job_id": job.id,
                            "status": job.status,
                            "preset_name_snapshot": job.preset_name_snapshot,
                            "completed_at": job.completed_at.isoformat()
                            if job.completed_at
                            else None,
                            "source_size_bytes": job.source_size_bytes,
                            "output_size_bytes": job.output_size_bytes,
                        }

            for item in sorted(target_path.iterdir(), key=_natural_sort_key):
                if item.is_dir():
                    if _directory_has_videos(item):
                        directories.append(
                            {
                                "name": item.name,
                                "path": str(item.relative_to(self.source_mount)),
                            }
                        )
                elif item.is_file() and item.suffix.lower() in self.VIDEO_EXTENSIONS:
                    is_conv_file = item.stem.endswith("_conv")

                    has_conv = False
                    conv_path = None
                    if not is_conv_file:
                        has_conv, conv_path = await has_converted_file(str(item))

                    files.append(
                        {
                            "name": item.name,
                            "path": str(item),
                            "size": item.stat().st_size,
                            "mtime": item.stat().st_mtime,
                            "has_converted": has_conv,
                            "converted_path": conv_path,
                            "is_converted_file": is_conv_file,
                            "last_job": last_jobs.get(str(item)),
                        }
                    )

            return {
                "current_path": str(target_path.relative_to(self.source_mount))
                if path
                else "",
                "directories": directories,
                "files": files,
            }

        except Exception as e:
            logger.error(f"Error browsing directory {path}: {e}")
            raise

    async def get_file_info(self, file_path: str) -> Dict[str, Any]:
        """Get detailed information about a video file."""
        try:
            path = Path(file_path)

            if not self._is_safe_path(path):
                raise ValueError("Invalid path")

            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            video_info = await get_video_info(str(path))

            has_conv, conv_path = await has_converted_file(str(path))

            return {
                "path": str(path),
                "name": path.name,
                "size": path.stat().st_size,
                "has_converted": has_conv,
                "converted_file": conv_path,
                **(video_info or {}),
            }

        except Exception as e:
            logger.error(f"Error getting file info for {file_path}: {e}")
            raise

    async def suggest_preset(self, film_grain: float) -> tuple:
        """Suggest a preset based on film grain estimate."""
        from app.models.preset import Preset

        async with AsyncSessionLocal() as db:
            if film_grain >= 18:
                name = "Very Grainy"
                reason = f"Very high film grain detected ({film_grain}), using Very Grainy preset"
            elif film_grain >= 12:
                name = "Grainy"
                reason = f"High film grain detected ({film_grain}), using Grainy preset"
            else:
                name = "Default"
                reason = f"Film grain level ({film_grain}) within normal range, using Default preset"

            result = await db.execute(select(Preset).where(Preset.name == name))
            preset = result.scalar_one_or_none()
            return (preset.id if preset else None, reason)

    async def delete_converted_file(self, converted_path: str) -> bool:
        """Delete a converted video file."""
        try:
            path = Path(converted_path)

            if not self._is_safe_path(path):
                raise ValueError("Invalid path (outside source mount)")

            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            if (
                path.is_symlink()
                or path.suffix.lower() != ".mkv"
                or not path.stem.endswith("_conv")
            ):
                raise ValueError("Not a conversion output")
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Job.id)
                    .where(Job.output_file == str(path.resolve()))
                    .limit(1)
                )
                if result.scalar_one_or_none() is None:
                    raise ValueError("No conversion record found for this output")
            with output_lock(path):
                path.unlink()
            logger.info(f"Deleted converted file: {converted_path}")
            return True

        except Exception as e:
            logger.error(f"Error deleting file {converted_path}: {e}")
            raise

    async def delete_file(self, file_path: str) -> bool:
        """Delete a file (source or other)."""
        try:
            path = Path(file_path)

            if not self._is_safe_path(path):
                raise ValueError("Invalid path (outside source mount)")

            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            # Safety check: Only allow deleting if converted file exists and is valid
            has_conv, conv_path = await has_converted_file(str(path))
            if not has_conv:
                raise ValueError(
                    "Cannot delete source file: No converted version found"
                )
            conv = Path(conv_path) if conv_path else None
            if conv and (not conv.exists() or conv.stat().st_size == 0):
                raise ValueError(
                    "Cannot delete source file: Converted file is empty or missing"
                )

            assert conv is not None
            if conv.is_symlink() or not self._is_safe_path(conv):
                raise ValueError("Invalid converted file path")
            with output_lock(conv):
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Job.id)
                        .where(
                            Job.source_file == str(path.resolve()),
                            Job.output_file == str(conv.resolve()),
                            Job.status == "completed",
                            Job.source_size_bytes == path.stat().st_size,
                            Job.output_size_bytes == conv.stat().st_size,
                        )
                        .limit(1)
                    )
                    if result.scalar_one_or_none() is None:
                        raise ValueError(
                            "Cannot delete source file: No matching successful conversion"
                        )
                path.unlink()
            logger.info(f"Deleted file: {file_path}")
            return True

        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")
            raise


file_service = FileService()
