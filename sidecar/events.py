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
        # in-process listeners (the Telegram bridge): sync callables taking the
        # event dict; they schedule their own async work and must never raise
        self._listeners: list[Any] = []

    def add_listener(self, fn: Any) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Any) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

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
                # bounded: a UI client that stops reading must not stall the voice
                # pipeline (emit is inline in the turn path, once per token/sentence)
                await asyncio.wait_for(ws.send_text(payload), timeout=2.0)
            except Exception:
                await self.detach(ws)
        for fn in list(self._listeners):
            try:
                fn(evt)
            except Exception:
                log.exception("event listener failed")
        log.debug("event %s %s", kind, data.get("summary", ""))
        return evt


bus = EventBus()
