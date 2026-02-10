"""
Unit tests for ConnectionManager class - standalone version to avoid import issues.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock
from fastapi import WebSocket
from starlette.websockets import WebSocketState


class ConnectionManager:
    """Manages WebSocket connections for real-time data distribution."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection."""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)

    async def disconnect(self, websocket: WebSocket):
        """Remove a disconnected WebSocket connection."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        """Broadcast a message to all connected WebSocket clients."""
        disconnected = []
        async with self._lock:
            for connection in self.active_connections:
                try:
                    if connection.client_state == WebSocketState.CONNECTED:
                        await connection.send_json(message)
                    else:
                        disconnected.append(connection)
                except Exception:
                    disconnected.append(connection)

            # Clean up disconnected clients
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)


class TestConnectionManagerStandalone:
    """Unit tests for ConnectionManager class - standalone version."""

    @pytest.fixture
    def connection_manager(self):
        """Create a fresh ConnectionManager instance."""
        return ConnectionManager()

    @pytest.fixture
    def mock_websocket(self):
        """Create a mock WebSocket connection."""
        ws = Mock(spec=WebSocket)
        ws.client_state = WebSocketState.CONNECTED
        ws.send_json = AsyncMock()
        ws.accept = AsyncMock()
        return ws

    @pytest.mark.asyncio
    async def test_connection_manager_initialization(self, connection_manager):
        """Test ConnectionManager initializes with empty connections."""
        assert len(connection_manager.active_connections) == 0
        assert connection_manager._lock is not None

    @pytest.mark.asyncio
    async def test_websocket_connect(self, connection_manager, mock_websocket):
        """Test accepting new WebSocket connections."""
        await connection_manager.connect(mock_websocket)

        assert mock_websocket in connection_manager.active_connections
        assert len(connection_manager.active_connections) == 1
        mock_websocket.accept.assert_called_once()

    @pytest.mark.asyncio
    async def test_websocket_disconnect(self, connection_manager, mock_websocket):
        """Test removing disconnected WebSocket connections."""
        # First connect
        await connection_manager.connect(mock_websocket)
        assert len(connection_manager.active_connections) == 1

        # Then disconnect
        await connection_manager.disconnect(mock_websocket)

        assert mock_websocket not in connection_manager.active_connections
        assert len(connection_manager.active_connections) == 0

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_connections(self, connection_manager):
        """Test broadcasting messages to multiple connected clients."""
        # Create multiple mock websockets
        websockets = []
        for i in range(3):
            ws = Mock(spec=WebSocket)
            ws.client_state = WebSocketState.CONNECTED
            ws.send_json = AsyncMock()
            ws.accept = AsyncMock()
            websockets.append(ws)

        # Connect all websockets
        for ws in websockets:
            await connection_manager.connect(ws)

        assert len(connection_manager.active_connections) == 3

        # Broadcast a message
        test_message = {"type": "test", "data": {"price": 100.0}}
        await connection_manager.broadcast(test_message)

        # Verify all websockets received the message
        for ws in websockets:
            ws.send_json.assert_called_once_with(test_message)

    @pytest.mark.asyncio
    async def test_broadcast_with_disconnected_clients(self, connection_manager):
        """Test broadcasting handles disconnected clients gracefully."""
        # Create websockets - some connected, some not
        connected_ws = Mock(spec=WebSocket)
        connected_ws.client_state = WebSocketState.CONNECTED
        connected_ws.send_json = AsyncMock()
        connected_ws.accept = AsyncMock()

        disconnected_ws = Mock(spec=WebSocket)
        disconnected_ws.client_state = WebSocketState.DISCONNECTED
        disconnected_ws.send_json = AsyncMock()
        disconnected_ws.accept = AsyncMock()

        # Connect both
        await connection_manager.connect(connected_ws)
        await connection_manager.connect(disconnected_ws)

        # Broadcast
        test_message = {"type": "price_update", "data": {"BTC": 50000}}
        await connection_manager.broadcast(test_message)

        # Connected websocket should receive message
        connected_ws.send_json.assert_called_once_with(test_message)

        # Disconnected websocket should not receive message (filtered out)
        disconnected_ws.send_json.assert_not_called()

        # Disconnected websocket should be removed from active connections
        assert disconnected_ws not in connection_manager.active_connections
        assert connected_ws in connection_manager.active_connections

    @pytest.mark.asyncio
    async def test_broadcast_with_send_exception(self, connection_manager):
        """Test broadcasting handles send exceptions gracefully."""
        ws = Mock(spec=WebSocket)
        ws.client_state = WebSocketState.CONNECTED
        ws.send_json = AsyncMock(side_effect=Exception("Connection lost"))
        ws.accept = AsyncMock()

        await connection_manager.connect(ws)

        # Broadcast should not raise exception even if send fails
        test_message = {"type": "error_test", "data": {}}
        await connection_manager.broadcast(test_message)

        # Websocket should be removed due to exception
        assert ws not in connection_manager.active_connections

    @pytest.mark.asyncio
    async def test_concurrent_connections(self, connection_manager):
        """Test handling multiple concurrent connections."""

        async def connect_client(client_id: int):
            ws = Mock(spec=WebSocket)
            ws.client_state = WebSocketState.CONNECTED
            ws.send_json = AsyncMock()
            ws.accept = AsyncMock()
            await connection_manager.connect(ws)
            return ws

        # Connect multiple clients concurrently
        tasks = [connect_client(i) for i in range(10)]
        connected_websockets = await asyncio.gather(*tasks)

        assert len(connection_manager.active_connections) == 10

        # Broadcast to all
        message = {"type": "concurrent_test", "data": {"clients": 10}}
        await connection_manager.broadcast(message)

        # All should receive
        for ws in connected_websockets:
            ws.send_json.assert_called_once_with(message)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
