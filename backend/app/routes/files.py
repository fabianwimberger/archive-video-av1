"""File management API endpoints."""
import logging
from fastapi import APIRouter, HTTPException, Query
from app.services.file_service import file_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("")
async def browse_files(path: str = Query(None, description="Relative path from source mount")):
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


@router.delete("/converted")
async def delete_converted_file(path: str = Query(..., description="Path to converted file")):
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
