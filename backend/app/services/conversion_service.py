"""Conversion service for executing video conversions with progress tracking."""

import asyncio
import logging
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional, TypedDict
from app.config import settings


class ProgressData(TypedDict):
    """Progress data structure for conversion jobs."""

    frame: int
    total_frames: int
    fps: float
    percent: float
    eta_seconds: int
    stage: str
    status: str
    current_log: str


logger = logging.getLogger(__name__)


class ConversionService:
    """Service for managing video conversions."""

    def __init__(self):
        self.wrapper_script = settings.CONVERSION_WRAPPER_SCRIPT

    async def convert_file(
        self,
        job_id: int,
        source_file: str,
        output_file: str,
        conversion_settings: Dict[str, Any],
        progress_callback: Callable,
        process_callback: Optional[Callable] = None,
    ) -> tuple[bool, str]:
        """
        Execute video conversion with real-time progress tracking.

        Args:
            job_id: Database job ID
            source_file: Absolute path to source file
            output_file: Absolute path to output file
            conversion_settings: Dict with CRF, preset, etc.
            progress_callback: Async function called with progress updates
            process_callback: Optional async function called with process object

        Returns:
            Tuple of (success, log)
        """
        # Build command arguments
        cmd = [
            self.wrapper_script,
            source_file,
            output_file,
            str(conversion_settings["crf"]),
            str(conversion_settings["preset"]),
            conversion_settings["svt_params"],
            conversion_settings["audio_bitrate"],
            "1" if conversion_settings["skip_crop_detect"] else "0",
        ]

        logger.info(f"Starting conversion job {job_id}: {source_file} -> {output_file}")

        # Initialize progress data
        progress_data: ProgressData = {
            "frame": 0,
            "total_frames": 0,
            "fps": 0.0,
            "percent": 0.0,
            "eta_seconds": 0,
            "stage": "initializing",
            "status": "Starting conversion...",
            "current_log": "",
        }

        log_lines: List[str] = []

        try:
            # Execute process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env={
                    "TEMP_DIR": settings.TEMP_DIR,
                    "PATH": "/usr/bin:/bin:/usr/local/bin",
                },
            )

            # Store process reference for cancellation
            if process_callback:
                await process_callback(process)

            # Parse stdout line-by-line
            last_progress_emit = 0.0
            last_log_append = 0.0
            progress_buffer = {}

            assert process.stdout is not None
            async for line in process.stdout:
                line_str = line.decode().strip()

                # Check if this is a progress line (key=value)
                is_progress_line = (
                    "=" in line_str
                    and not line_str.startswith("STAGE:")
                    and not line_str.startswith("STATUS:")
                    and not line_str.startswith("ERROR:")
                    and not line_str.startswith("CMD:")
                )

                if is_progress_line:
                    key, value = line_str.split("=", 1)
                    progress_buffer[key] = value

                    if key == "frame":
                        try:
                            progress_data["frame"] = int(value)
                        except ValueError:
                            pass
                    elif key == "fps":
                        try:
                            progress_data["fps"] = float(value)
                        except ValueError:
                            pass
                    elif key == "total_frames":
                        try:
                            progress_data["total_frames"] = int(value)
                        except ValueError:
                            pass
                    elif key == "progress":
                        # Calculate percentage and ETA
                        total_frames = progress_data["total_frames"]
                        frame = progress_data["frame"]
                        if total_frames > 0 and frame > 0:
                            progress_data["percent"] = min(
                                (frame / total_frames) * 100,
                                100.0,
                            )

                            # Calculate ETA
                            fps = progress_data["fps"]
                            if fps > 0:
                                remaining_frames = total_frames - frame
                                progress_data["eta_seconds"] = int(
                                    remaining_frames / fps
                                )

                        # Emit progress update (throttle to every 1 second)
                        current_time = asyncio.get_event_loop().time()
                        if current_time - last_progress_emit >= 1.0:
                            # Add current log to progress data
                            progress_data["current_log"] = "\n".join(log_lines)
                            await progress_callback(job_id, progress_data.copy())
                            last_progress_emit = current_time

                        # Compact log entry every 5 seconds
                        if current_time - last_log_append >= 5.0:
                            summary = f"Frame: {progress_buffer.get('frame', 'N/A')} | FPS: {progress_buffer.get('fps', 'N/A')} | Size: {progress_buffer.get('total_size', 'N/A')} | Bitrate: {progress_buffer.get('bitrate', 'N/A')}"
                            log_lines.append(summary)
                            last_log_append = current_time

                # Parse stage markers
                elif line_str.startswith("STAGE:"):
                    log_lines.append(line_str)
                    stage = line_str.split(":", 1)[1]
                    progress_data["stage"] = stage
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())
                    logger.info(f"Job {job_id} stage: {stage}")

                # Parse status messages
                elif line_str.startswith("STATUS:"):
                    log_lines.append(line_str)
                    status = line_str.split(":", 1)[1]
                    progress_data["status"] = status
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())

                # Parse error messages
                elif line_str.startswith("ERROR:"):
                    log_lines.append(line_str)
                    error = line_str.split(":", 1)[1]
                    logger.error(f"Job {job_id} error: {error}")

                # Parse ffmpeg command - display immediately
                elif line_str.startswith("CMD:"):
                    cmd_line = line_str.split(":", 1)[1]
                    log_lines.append(line_str)
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())
                    logger.info(f"Job {job_id} executing: {cmd_line}")

                # Other lines (ffmpeg init logs etc)
                else:
                    log_lines.append(line_str)

            # Wait for process to complete
            await process.wait()

            # Capture stderr
            assert process.stderr is not None
            stderr_output = await process.stderr.read()
            if stderr_output:
                stderr_str = stderr_output.decode().strip()
                log_lines.append(f"STDERR: {stderr_str}")

            # Check exit code
            success = process.returncode == 0

            if success:
                logger.info(f"Job {job_id} completed successfully")
                # Emit final 100% progress
                progress_data["percent"] = 100.0
                progress_data["stage"] = "complete"
                progress_data["status"] = "Conversion complete"
                await progress_callback(job_id, progress_data.copy())
            else:
                logger.error(f"Job {job_id} failed with exit code {process.returncode}")

            return success, "\n".join(log_lines)

        except Exception as e:
            logger.error(f"Exception in conversion job {job_id}: {e}")
            log_lines.append(f"EXCEPTION: {str(e)}")
            return False, "\n".join(log_lines)

    def get_output_path(self, source_file: str) -> str:
        """
        Calculate output file path based on source file.

        Args:
            source_file: Path to source file

        Returns:
            Path to output file
        """
        source_path = Path(source_file)
        stem = source_path.stem
        parent = source_path.parent
        ext = source_path.suffix

        return str(parent / f"{stem}_conv{ext}")


# Global conversion service instance
conversion_service = ConversionService()
