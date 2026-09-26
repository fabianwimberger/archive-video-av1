"""WebSocket endpoint for real-time updates."""

import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Pushes job_progress, job_status and queue_update messages."""
    await websocket_manager.connect(websocket)

    try:
        await websocket_manager.send_to(
            websocket,
            {
                "type": "system",
                "message": "Connected to conversion service",
            },
        )

        while True:
            try:
                data = await websocket.receive_json()

                if data.get("type") == "ping":
                    await websocket_manager.send_to(
                        websocket,
                        {
                            "type": "pong",
                        },
                    )

            except WebSocketDisconnect:
                logger.info("WebSocket client disconnected normally")
                break
            except Exception as e:
                logger.error(f"Error receiving WebSocket message: {e}")
                break

    except Exception as e:
        logger.error(f"WebSocket error: {e}")

    finally:
        websocket_manager.disconnect(websocket)
