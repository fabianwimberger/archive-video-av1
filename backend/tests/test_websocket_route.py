"""Tests for the /ws real-time updates endpoint."""

from app.services.websocket_manager import websocket_manager


def test_websocket_sends_initial_system_message(client):
    with client.websocket_connect("/ws") as ws:
        message = ws.receive_json()

    assert message == {"type": "system", "message": "Connected to conversion service"}


def test_websocket_responds_to_ping_with_pong(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # initial system message
        ws.send_json({"type": "ping"})
        message = ws.receive_json()

    assert message == {"type": "pong"}


def test_websocket_ignores_unknown_message_types(client):
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # initial system message
        ws.send_json({"type": "unknown"})
        ws.send_json({"type": "ping"})
        message = ws.receive_json()

    assert message == {"type": "pong"}


def test_websocket_disconnect_removes_connection(client):
    assert websocket_manager.get_connection_count() == 0

    with client.websocket_connect("/ws") as ws:
        ws.receive_json()
        assert websocket_manager.get_connection_count() == 1

    assert websocket_manager.get_connection_count() == 0
