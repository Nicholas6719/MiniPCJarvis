"""Event bus: broadcasts structured events to all connected UI websockets."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("jarvis.events")


class EventBus:
    def __init__(self) -> None:
        self._clients: set[Any] = set()  # fastapi WebSocket objects
        self._lock = asyncio.Lock()

    async def attach(self, ws: Any) -> None:
        async with self._lock:
            self._clients.add(ws)

    async def detach(self, ws: Any) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def emit(self, kind: str, **data: Any) -> dict:
        evt = {
            "id": uuid.uuid4().hex[:12],
            "ts": time.time(),
            "kind": kind,
            **data,
        }
        payload = json.dumps(evt, default=str)
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(payload)
            except Exception:
                await self.detach(ws)
        log.debug("event %s %s", kind, data.get("summary", ""))
        return evt


bus = EventBus()
