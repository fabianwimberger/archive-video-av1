"""FFprobe wrapper utilities for extracting video metadata."""
import asyncio
import json
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


async def get_video_info(file_path: str) -> Optional[Dict[str, Any]]:
    """
    Get video metadata using ffprobe.

    Args:
        file_path: Path to video file

    Returns:
        Dictionary with video metadata or None if failed
    """
    try:
        # Run ffprobe to get JSON output
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"FFprobe failed for {file_path}: {stderr.decode()}")
            return None

        data = json.loads(stdout.decode())

        # Extract relevant info
        video_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
            None
        )

        if not video_stream:
            return None

        format_info = data.get("format", {})

        return {
            "codec": video_stream.get("codec_name", "unknown"),
            "width": video_stream.get("width", 0),
            "height": video_stream.get("height", 0),
            "duration": float(format_info.get("duration", 0)),
            "size": int(format_info.get("size", 0)),
            "bitrate": int(format_info.get("bit_rate", 0)),
            "fps": eval_fps(video_stream.get("r_frame_rate", "0/1")),
        }

    except Exception as e:
        logger.error(f"Error getting video info for {file_path}: {e}")
        return None


def eval_fps(fps_string: str) -> float:
    """
    Evaluate FPS from fraction string (e.g., "30000/1001").

    Args:
        fps_string: FPS as fraction string

    Returns:
        FPS as float
    """
    try:
        if "/" in fps_string:
            num, den = fps_string.split("/")
            return float(num) / float(den)
        return float(fps_string)
    except (ValueError, ZeroDivisionError, TypeError):
        return 0.0


async def has_converted_file(source_file: str) -> tuple[bool, Optional[str]]:
    """
    Check if a converted version of the file exists.

    Args:
        source_file: Path to source file

    Returns:
        Tuple of (exists, converted_file_path)
    """
    from pathlib import Path

    source_path = Path(source_file)
    stem = source_path.stem
    parent = source_path.parent
    ext = source_path.suffix

    converted_path = parent / f"{stem}_conv{ext}"

    return converted_path.exists(), str(converted_path) if converted_path.exists() else None
