"""WebSocket connection manager for broadcasting updates."""
import logging
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """Manages WebSocket connections and broadcasting."""

    def __init__(self):
        self.connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        """
        Accept and register new WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        await websocket.accept()
        self.connections.add(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.connections)}")

    def disconnect(self, websocket: WebSocket):
        """
        Remove WebSocket connection.

        Args:
            websocket: WebSocket connection
        """
        self.connections.discard(websocket)
        logger.info(f"WebSocket disconnected. Total connections: {len(self.connections)}")

    async def broadcast(self, message: dict):
        """
        Broadcast message to all connected clients.

        Args:
            message: Message dictionary to broadcast
        """
        if not self.connections:
            return

        dead_connections = set()

        for connection in list(self.connections):
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to WebSocket: {e}")
                dead_connections.add(connection)

        # Remove dead connections
        for connection in dead_connections:
            self.connections.discard(connection)

        if dead_connections:
            logger.info(f"Removed {len(dead_connections)} dead connections")

    async def send_to(self, websocket: WebSocket, message: dict):
        """
        Send message to specific client.

        Args:
            websocket: WebSocket connection
            message: Message dictionary to send
        """
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"Error sending message to WebSocket: {e}")
            self.connections.discard(websocket)

    def get_connection_count(self) -> int:
        """Get number of active connections."""
        return len(self.connections)


# Global WebSocket manager instance
websocket_manager = WebSocketManager()
