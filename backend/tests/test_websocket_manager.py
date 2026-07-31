"""Tests for the WebSocket connection manager."""

import pytest

from app.services.websocket_manager import WebSocketManager


class FakeWebSocket:
    def __init__(self, name="ws", fail=False):
        self.name = name
        self.fail = fail
        self.accepted = False
        self.sent = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_connect_accepts_and_registers():
    manager = WebSocketManager()
    ws = FakeWebSocket()

    await manager.connect(ws)

    assert ws.accepted is True
    assert manager.get_connection_count() == 1


def test_disconnect_removes_known_connection():
    manager = WebSocketManager()
    ws = FakeWebSocket()
    manager.connections.add(ws)

    manager.disconnect(ws)

    assert manager.get_connection_count() == 0


def test_disconnect_is_noop_for_unknown_connection():
    manager = WebSocketManager()

    manager.disconnect(FakeWebSocket())  # should not raise

    assert manager.get_connection_count() == 0


@pytest.mark.asyncio
async def test_broadcast_with_no_connections_is_noop():
    manager = WebSocketManager()

    await manager.broadcast({"type": "ping"})  # should not raise


@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connections():
    manager = WebSocketManager()
    ws1, ws2 = FakeWebSocket("a"), FakeWebSocket("b")
    manager.connections.update({ws1, ws2})

    await manager.broadcast({"type": "job_progress", "job_id": 1})

    assert {"type": "job_progress", "job_id": 1} in ws1.sent
    assert {"type": "job_progress", "job_id": 1} in ws2.sent
    assert manager.get_connection_count() == 2


@pytest.mark.asyncio
async def test_broadcast_prunes_dead_connections():
    manager = WebSocketManager()
    healthy, dead = FakeWebSocket("healthy"), FakeWebSocket("dead", fail=True)
    manager.connections.update({healthy, dead})

    await manager.broadcast({"type": "queue_update"})

    assert manager.get_connection_count() == 1
    assert healthy in manager.connections
    assert dead not in manager.connections


@pytest.mark.asyncio
async def test_send_to_delivers_message():
    manager = WebSocketManager()
    ws = FakeWebSocket()

    await manager.send_to(ws, {"type": "job_status"})

    assert {"type": "job_status"} in ws.sent


@pytest.mark.asyncio
async def test_send_to_removes_connection_on_failure():
    manager = WebSocketManager()
    ws = FakeWebSocket(fail=True)
    manager.connections.add(ws)

    await manager.send_to(ws, {"type": "job_status"})

    assert manager.get_connection_count() == 0
