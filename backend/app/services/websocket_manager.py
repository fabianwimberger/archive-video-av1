import asyncio
import logging
from typing import Set
from fastapi import WebSocket
from app.config import settings

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self.connections: Set[WebSocket] = set()
        self.send_timeout = 2.0

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.connections)}")

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)
        logger.info(
            f"WebSocket disconnected. Total connections: {len(self.connections)}"
        )

    async def broadcast(self, message: dict) -> None:
        if not self.connections:
            return
        message = {"node_id": settings.DISTRIBUTED_NODE_ID, **message}

        dead_connections = set()

        async def _send(connection: WebSocket) -> None:
            await asyncio.wait_for(connection.send_json(message), self.send_timeout)

        connections = list(self.connections)
        results = await asyncio.gather(
            *[_send(c) for c in connections],
            return_exceptions=True,
        )
        for connection, result in zip(connections, results):
            if isinstance(result, Exception):
                logger.error(f"Error sending message to WebSocket: {result}")
                dead_connections.add(connection)

        for connection in dead_connections:
            self.connections.discard(connection)

        if dead_connections:
            logger.info(f"Removed {len(dead_connections)} dead connections")

    async def send_to(self, websocket: WebSocket, message: dict):
        try:
            await asyncio.wait_for(websocket.send_json(message), self.send_timeout)
        except Exception as e:
            logger.error(f"Error sending message to WebSocket: {e}")
            self.connections.discard(websocket)

    def get_connection_count(self) -> int:
        return len(self.connections)


websocket_manager = WebSocketManager()
