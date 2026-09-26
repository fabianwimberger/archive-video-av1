"""Conversion service for executing video conversions with progress tracking."""

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional, TypedDict
from app.config import settings
from app.utils.validation import validate_conversion_settings
from app.utils.file_safety import OutputInUse, output_lock, validate_source_path


class ProgressData(TypedDict):
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
        """Execute video conversion with real-time progress tracking."""
        validate_conversion_settings(conversion_settings)
        source_file = str(validate_source_path(source_file))
        expected_output = Path(self.get_output_path(source_file))
        if Path(output_file) != expected_output or expected_output.is_symlink():
            raise ValueError("Invalid conversion output path")

        # Support both 'encoder_preset' (new) and 'preset' (legacy) keys
        preset_value = conversion_settings.get(
            "encoder_preset", conversion_settings.get("preset", 4)
        )

        cmd = [
            self.wrapper_script,
            source_file,
            output_file,
            str(conversion_settings["crf"]),
            str(preset_value),
            conversion_settings.get("svt_params", ""),
            conversion_settings["audio_bitrate"],
            "1" if conversion_settings.get("skip_crop_detect", False) else "0",
            str(conversion_settings.get("max_resolution", 1080)),
        ]

        logger.info(f"Starting conversion job {job_id}: {source_file} -> {output_file}")

        # Held until the wrapper's whole process group is gone, so the lock
        # file is removed on success, failure and cancellation alike.
        try:
            with output_lock(expected_output):
                return await self._run_wrapper(
                    job_id, cmd, progress_callback, process_callback
                )
        except OutputInUse as exc:
            logger.error(f"Job {job_id} error: {exc}")
            return False, f"ERROR:{exc}"

    async def _run_wrapper(
        self,
        job_id: int,
        cmd: List[str],
        progress_callback: Callable,
        process_callback: Optional[Callable],
    ) -> tuple[bool, str]:
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
        process = None

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                env={
                    "TEMP_DIR": settings.TEMP_DIR,
                    "PATH": "/usr/bin:/bin:/usr/local/bin",
                    "LC_ALL": "C.UTF-8",
                    "LANG": "C.UTF-8",
                    **{
                        k: os.environ[k]
                        for k in (
                            "AUDIO_TRACK_MODE",
                            "SUBTITLE_TRACK_MODE",
                            "PREFERRED_AUDIO_LANGUAGES",
                            "PREFERRED_SUBTITLE_LANGUAGES",
                        )
                        if k in os.environ
                    },
                },
            )

            if process_callback:
                await process_callback(process)

            last_progress_emit = 0.0
            last_log_append = 0.0
            progress_buffer = {}

            if process.stdout is None:
                raise RuntimeError("Process stdout is None")
            async for line in process.stdout:
                line_str = line.decode().strip()

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
                        total_frames = progress_data["total_frames"]
                        frame = progress_data["frame"]
                        if total_frames > 0 and frame > 0:
                            progress_data["percent"] = min(
                                (frame / total_frames) * 100,
                                100.0,
                            )

                            fps = progress_data["fps"]
                            if fps > 0:
                                remaining_frames = total_frames - frame
                                progress_data["eta_seconds"] = int(
                                    remaining_frames / fps
                                )

                        # Throttled so the database and websocket clients see one update per second.
                        current_time = asyncio.get_running_loop().time()
                        if current_time - last_progress_emit >= 1.0:
                            progress_data["current_log"] = "\n".join(log_lines)
                            await progress_callback(job_id, progress_data.copy())
                            last_progress_emit = current_time

                        # A summary line every 5 seconds keeps the stored log small.
                        if current_time - last_log_append >= 5.0:
                            summary = f"Frame: {progress_buffer.get('frame', 'N/A')} | FPS: {progress_buffer.get('fps', 'N/A')} | Size: {progress_buffer.get('total_size', 'N/A')} | Bitrate: {progress_buffer.get('bitrate', 'N/A')}"
                            log_lines.append(summary)
                            last_log_append = current_time

                elif line_str.startswith("STAGE:"):
                    log_lines.append(line_str)
                    stage = line_str.split(":", 1)[1]
                    progress_data["stage"] = stage
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())
                    logger.info(f"Job {job_id} stage: {stage}")

                elif line_str.startswith("STATUS:"):
                    log_lines.append(line_str)
                    status = line_str.split(":", 1)[1]
                    progress_data["status"] = status
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())

                elif line_str.startswith("ERROR:"):
                    log_lines.append(line_str)
                    error = line_str.split(":", 1)[1]
                    logger.error(f"Job {job_id} error: {error}")

                # Shown right away so the command is visible before the first progress line.
                elif line_str.startswith("CMD:"):
                    cmd_line = line_str.split(":", 1)[1]
                    log_lines.append(line_str)
                    progress_data["current_log"] = "\n".join(log_lines)
                    await progress_callback(job_id, progress_data.copy())
                    logger.info(f"Job {job_id} executing: {cmd_line}")

                else:
                    log_lines.append(line_str)

            await process.wait()

            success = process.returncode == 0

            if success:
                logger.info(f"Job {job_id} completed successfully")
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
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.communicate(), timeout=2.0)
                except asyncio.TimeoutError:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.communicate()

    def get_output_path(self, source_file: str) -> str:
        """Output is always Matroska (.mkv), regardless of source container."""
        source_path = Path(source_file)
        stem = source_path.stem
        parent = source_path.parent

        return str(parent / f"{stem}_conv.mkv")


conversion_service = ConversionService()
