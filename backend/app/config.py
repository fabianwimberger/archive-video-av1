"""Configuration management for the conversion service."""

import os
from pathlib import Path


class Settings:
    """Application settings loaded from environment variables."""

    # Paths
    SOURCE_MOUNT: str = os.getenv("SOURCE_MOUNT", "/videos")
    TEMP_DIR: str = os.getenv("TEMP_DIR", "/app/temp")
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "/app/data/app.db")

    # Logging
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Database
    DATABASE_URL: str = f"sqlite+aiosqlite:///{DATABASE_PATH}"

    # Scripts
    CONVERSION_WRAPPER_SCRIPT: str = "/app/scripts/conversion_wrapper.sh"

    # History retention
    JOB_HISTORY_RETENTION_DAYS: int = int(os.getenv("JOB_HISTORY_RETENTION_DAYS", "0"))
    JOB_HISTORY_MAX_ROWS: int = int(os.getenv("JOB_HISTORY_MAX_ROWS", "0"))

    # CORS
    CORS_ORIGINS: list = os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")

    @classmethod
    def ensure_directories(cls):
        """Ensure required directories exist and clean temp directory."""
        import shutil
        import logging

        logger = logging.getLogger(__name__)

        # Clean temp directory on startup (remove orphaned files from previous runs)
        temp_path = Path(cls.TEMP_DIR)
        if temp_path.exists():
            try:
                # Remove all files in temp directory
                for item in temp_path.iterdir():
                    if item.is_file():
                        item.unlink()
                        logger.info(f"Cleaned orphaned temp file: {item.name}")
                    elif item.is_dir():
                        shutil.rmtree(item)
                        logger.info(f"Cleaned orphaned temp directory: {item.name}")
                logger.info("Temp directory cleaned on startup")
            except Exception as e:
                logger.error(f"Error cleaning temp directory: {e}")

        # Ensure directories exist
        temp_path.mkdir(parents=True, exist_ok=True)
        Path(cls.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
