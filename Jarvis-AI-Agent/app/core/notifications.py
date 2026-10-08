"""Keeps track of open dashboard connections so the server can push to them
(used for reminders that come due)."""

import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self._connections.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._connections.discard(ws)

    @property
    def count(self) -> int:
        return len(self._connections)

    async def broadcast(self, message: dict) -> int:
        """Send a JSON message to every open dashboard. Returns how many received it."""
        data = json.dumps(message)
        delivered = 0
        for ws in list(self._connections):
            try:
                await ws.send_text(data)
                delivered += 1
            except Exception:
                self.remove(ws)
        return delivered


manager = ConnectionManager()
