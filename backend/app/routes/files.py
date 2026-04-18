"""File management API endpoints."""

import logging
from fastapi import APIRouter, HTTPException, Query
from app.services.file_service import file_service
from app.services.grain_estimator import estimate_grain

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def browse_files(
    path: str = Query(None, description="Relative path from source mount"),
):
    """
    Browse directory and list video files.

    Args:
        path: Relative path from source mount (optional)

    Returns:
        Dictionary with directories and files
    """
    try:
        result = await file_service.browse_directory(path)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error browsing files: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/info")
async def get_file_info(path: str = Query(..., description="Absolute path to file")):
    """
    Get detailed information about a video file.

    Args:
        path: Absolute path to file

    Returns:
        File metadata including codec, duration, size, etc.
    """
    try:
        result = await file_service.get_file_info(path)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error getting file info: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/analyze")
async def analyze_file(
    path: str = Query(..., description="Absolute path to file"),
    suggest_preset: bool = Query(False, description="Include preset suggestion"),
):
    """
    Analyze a video file to estimate optimal film grain and denoise settings.

    Args:
        path: Absolute path to file
        suggest_preset: Whether to include preset suggestion

    Returns:
        Estimated film_grain, denoise, and optional suggested_preset_id
    """
    try:
        from pathlib import Path

        resolved = Path(path).resolve()
        if not file_service._is_safe_path(resolved):
            raise ValueError("Invalid path")

        if not resolved.exists() or not resolved.is_file():
            raise ValueError("File does not exist")

        result = await estimate_grain(path)

        if suggest_preset:
            suggested_preset_id, reason = await file_service.suggest_preset(
                result.get("film_grain", 0)
            )
            result["suggested_preset_id"] = suggested_preset_id
            result["reason"] = reason

        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error analyzing file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/converted")
async def delete_converted_file(
    path: str = Query(..., description="Path to converted file"),
):
    """
    Delete a converted video file.

    Args:
        path: Absolute path to converted file

    Returns:
        Success status
    """
    try:
        await file_service.delete_converted_file(path)
        return {"success": True, "message": "File deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("")
async def delete_file(path: str = Query(..., description="Path to file")):
    """
    Delete a file.

    Args:
        path: Absolute path to file

    Returns:
        Success status
    """
    try:
        await file_service.delete_file(path)
        return {"success": True, "message": "File deleted successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
