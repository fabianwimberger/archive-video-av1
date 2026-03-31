"""Grain and denoise estimator for video files."""

import asyncio
import logging
import re
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


async def estimate_grain(file_path: str) -> Dict[str, Any]:
    """
    Estimate film grain and denoise requirements for a video file.

    Samples frames at multiple timestamps and analyzes luma/chroma
    standard deviation using ffmpeg's showinfo filter.

    Returns:
        Dict with film_grain (int), denoise (int), confidence (str),
        and diagnostic values.
    """
    # Get duration
    duration_proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await duration_proc.communicate()
    try:
        duration = float(stdout.decode().strip())
    except ValueError:
        duration = 0.0

    if duration <= 0:
        return _fallback("Could not determine video duration")

    # Get resolution and bitrate
    res_proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0", file_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await res_proc.communicate()
    width, height = 1920, 1080
    try:
        res_line = stdout.decode().strip()
        if "," in res_line:
            w_str, h_str = res_line.split(",", 1)
            width = int(w_str)
            height = int(h_str)
    except (ValueError, IndexError):
        pass

    bitrate_proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=bit_rate",
        "-of", "default=noprint_wrappers=1:nokey=1", file_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await bitrate_proc.communicate()
    try:
        bitrate = int(stdout.decode().strip())
    except ValueError:
        bitrate = 0

    # Sample at 15%, 35%, 55%, 75% of duration
    samples = [duration * p / 100 for p in [15, 35, 55, 75]]
    y_values = []
    u_values = []
    v_values = []

    stdev_pattern = re.compile(r"stdev:\[([0-9.]+)\s+([0-9.]+)\s+([0-9.]+)\]")

    for time_pos in samples:
        cmd = [
            "ffmpeg", "-hide_banner", "-ss", str(time_pos),
            "-i", file_path, "-frames:v", "1", "-vf", "showinfo",
            "-an", "-f", "null", "-"
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        output = stderr.decode()
        for line in output.splitlines():
            match = stdev_pattern.search(line)
            if match:
                try:
                    y_values.append(float(match.group(1)))
                    u_values.append(float(match.group(2)))
                    v_values.append(float(match.group(3)))
                except ValueError:
                    continue

    if not y_values:
        return _fallback("Could not analyze video frames")

    avg_y = sum(y_values) / len(y_values)
    avg_u = sum(u_values) / len(u_values) if u_values else 0
    avg_v = sum(v_values) / len(v_values) if v_values else 0

    # Resolution normalization factor
    megapixels = (width * height) / 1_000_000
    if megapixels >= 7.0:       # 4K+
        norm_factor = 3.0
    elif megapixels >= 1.8:     # 1080p
        norm_factor = 1.5
    else:                       # 720p or lower
        norm_factor = 1.0

    y_norm = avg_y / norm_factor
    bitrate_per_mp = bitrate / megapixels / 1000 if megapixels > 0 and bitrate > 0 else 0

    # Estimation logic
    # Animation detection: high chroma variation relative to luma
    if avg_u > 8.0 and avg_v > 8.0:
        return {
            "film_grain": 0,
            "denoise": 0,
            "confidence": "high",
            "raw_y": round(avg_y, 2),
            "raw_u": round(avg_u, 2),
            "raw_v": round(avg_v, 2),
            "y_norm": round(y_norm, 2),
            "bitrate_per_mp": round(bitrate_per_mp, 1),
            "reason": "High chroma variation (animation-like content)",
        }

    # Grainy film detection: moderate luma, low chroma
    if avg_u < 8.0 and avg_v < 8.0 and 20.0 < y_norm < 60.0:
        return {
            "film_grain": 16,
            "denoise": 1,
            "confidence": "medium",
            "raw_y": round(avg_y, 2),
            "raw_u": round(avg_u, 2),
            "raw_v": round(avg_v, 2),
            "y_norm": round(y_norm, 2),
            "bitrate_per_mp": round(bitrate_per_mp, 1),
            "reason": "Low chroma with moderate luma texture (grainy film)",
        }

    # High detail content
    if y_norm > 50.0:
        if bitrate_per_mp > 0 and bitrate_per_mp < 3000:
            return {
                "film_grain": 16,
                "denoise": 1,
                "confidence": "medium",
                "raw_y": round(avg_y, 2),
                "raw_u": round(avg_u, 2),
                "raw_v": round(avg_v, 2),
                "y_norm": round(y_norm, 2),
                "bitrate_per_mp": round(bitrate_per_mp, 1),
                "reason": f"High detail, low bitrate ({bitrate_per_mp:.0f} kbps/MP)",
            }
        else:
            return {
                "film_grain": 12,
                "denoise": 0,
                "confidence": "medium",
                "raw_y": round(avg_y, 2),
                "raw_u": round(avg_u, 2),
                "raw_v": round(avg_v, 2),
                "y_norm": round(y_norm, 2),
                "bitrate_per_mp": round(bitrate_per_mp, 1),
                "reason": f"High detail, good bitrate ({bitrate_per_mp:.0f} kbps/MP)",
            }

    # Default mapping
    if y_norm < 15.0:
        film_grain = 4
    elif y_norm < 30.0:
        film_grain = 8
    else:
        film_grain = 12

    return {
        "film_grain": film_grain,
        "denoise": 0,
        "confidence": "medium",
        "raw_y": round(avg_y, 2),
        "raw_u": round(avg_u, 2),
        "raw_v": round(avg_v, 2),
        "y_norm": round(y_norm, 2),
        "bitrate_per_mp": round(bitrate_per_mp, 1),
        "reason": f"Y_norm={y_norm:.1f}, moderate texture",
    }


def _fallback(reason: str) -> Dict[str, Any]:
    return {
        "film_grain": 8,
        "denoise": 0,
        "confidence": "low",
        "raw_y": 0.0,
        "raw_u": 0.0,
        "raw_v": 0.0,
        "y_norm": 0.0,
        "bitrate_per_mp": 0.0,
        "reason": reason,
    }
