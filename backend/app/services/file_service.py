"""File system operations for browsing and file management."""

import logging
from pathlib import Path
from typing import Dict, Any
from app.config import settings
from app.utils.ffprobe import get_video_info, has_converted_file

logger = logging.getLogger(__name__)


def _directory_has_videos(directory: Path) -> bool:
    """
    Check if directory contains any .mkv files (recursively).

    Args:
        directory: Directory path to check

    Returns:
        True if any .mkv files found, False otherwise
    """
    try:
        return any(
            f.suffix.lower() == ".mkv" for f in directory.rglob("*") if f.is_file()
        )
    except (OSError, PermissionError):
        return False


class FileService:
    """Service for file system operations."""

    VIDEO_EXTENSIONS = {".mkv"}

    def __init__(self):
        self.source_mount = Path(settings.SOURCE_MOUNT)

    def _is_safe_path(self, path: Path) -> bool:
        """
        Check if path is within source mount (security check).

        Args:
            path: Path to check

        Returns:
            True if safe, False otherwise
        """
        try:
            resolved = path.resolve()
            return resolved.is_relative_to(self.source_mount)
        except (ValueError, RuntimeError):
            return False

    async def browse_directory(self, path: str = None) -> Dict[str, Any]:
        """
        Browse directory and return files and subdirectories.

        Args:
            path: Relative path from source mount, or None for root

        Returns:
            Dictionary with directories and files lists
        """
        try:
            if path:
                target_path = (self.source_mount / path).resolve()
            else:
                target_path = self.source_mount

            # Security check
            if not self._is_safe_path(target_path):
                raise ValueError("Invalid path")

            if not target_path.exists() or not target_path.is_dir():
                raise ValueError("Path does not exist or is not a directory")

            directories = []
            files = []

            # Scan directory
            for item in sorted(target_path.iterdir()):
                if item.is_dir():
                    # Only show directories that contain .mkv files
                    if _directory_has_videos(item):
                        directories.append(
                            {
                                "name": item.name,
                                "path": str(item.relative_to(self.source_mount)),
                            }
                        )
                elif item.is_file() and item.suffix.lower() in self.VIDEO_EXTENSIONS:
                    # Check if this is a converted file or a source file
                    is_conv_file = item.stem.endswith("_conv")

                    # For source files, check if converted version exists
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
        """
        Get detailed information about a video file.

        Args:
            file_path: Absolute path to file

        Returns:
            Dictionary with file metadata
        """
        try:
            path = Path(file_path)

            # Security check
            if not self._is_safe_path(path):
                raise ValueError("Invalid path")

            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            # Get video info from ffprobe
            video_info = await get_video_info(str(path))

            # Check for converted file
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

    async def delete_converted_file(self, converted_path: str) -> bool:
        """
        Delete a converted video file.

        Args:
            converted_path: Absolute path to converted file

        Returns:
            True if deleted successfully
        """
        try:
            path = Path(converted_path)

            # Security check
            if not self._is_safe_path(path):
                raise ValueError("Invalid path (outside source mount)")

            # Check file exists
            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            # Delete the file
            path.unlink()
            logger.info(f"Deleted converted file: {converted_path}")
            return True

        except Exception as e:
            logger.error(f"Error deleting file {converted_path}: {e}")
            raise

    async def delete_file(self, file_path: str) -> bool:
        """
        Delete a file (source or other).

        Args:
            file_path: Absolute path to file

        Returns:
            True if deleted successfully
        """
        try:
            path = Path(file_path)

            # Security check
            if not self._is_safe_path(path):
                raise ValueError("Invalid path (outside source mount)")

            # Check file exists
            if not path.exists() or not path.is_file():
                raise ValueError("File does not exist")

            # Safety check: Only allow deleting if converted file exists
            has_conv, _ = await has_converted_file(str(path))
            if not has_conv:
                raise ValueError(
                    "Cannot delete source file: No converted version found"
                )

            # Delete the file
            path.unlink()
            logger.info(f"Deleted file: {file_path}")
            return True

        except Exception as e:
            logger.error(f"Error deleting file {file_path}: {e}")
            raise


# Global file service instance
file_service = FileService()
