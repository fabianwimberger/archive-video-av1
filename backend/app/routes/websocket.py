"""WebSocket endpoint for real-time updates."""
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.services.websocket_manager import websocket_manager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time job updates.

    Clients can connect to receive:
    - job_progress: Real-time progress updates during encoding
    - job_status: Status changes (pending -> processing -> completed/failed)
    - queue_update: Changes to job queue size
    """
    await websocket_manager.connect(websocket)

    try:
        # Send initial connection message
        await websocket_manager.send_to(websocket, {
            "type": "system",
            "message": "Connected to conversion service",
        })

        # Keep connection alive and handle incoming messages
        while True:
            try:
                data = await websocket.receive_json()

                # Handle ping/pong for keep-alive
                if data.get("type") == "ping":
                    await websocket_manager.send_to(websocket, {
                        "type": "pong",
                    })

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
