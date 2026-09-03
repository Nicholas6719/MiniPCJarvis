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
        # Set by emit(); read by the stuck-state watchdog.
        self.last_event_at = time.time()
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
        # WHEN ANYTHING LAST HAPPENED. The stuck-state watchdog needs to tell a
        # slow turn from a wedged one, and time-in-state cannot: both look
        # identical. A working turn emits constantly; a wedged one is silent.
        self.last_event_at = time.time()
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


# Background tasks must be kept referenced. asyncio only holds a WEAK reference to
# a running task, so a fire-and-forget create_task() can be garbage collected
# mid-flight and the work simply vanishes — the hardest class of bug to see,
# because nothing errors. Anything spawned outside a request's own lifetime goes
# through here.
_background: set = set()


def spawn(coro, name: str | None = None):
    """create_task that cannot be collected before it finishes, and that SAYS
    so when it fails.

    The reference-keeping above solved work vanishing silently. The exception
    did not: `add_done_callback(_background.discard)` drops the task without
    ever reading `task.exception()`, so a background job that raised was
    reported only by asyncio's own late "exception was never retrieved" notice
    at garbage-collection time, if at all — under a frozen build, usually not.
    Every one of the failures that has cost him a working assistant was
    invisible before it was expensive. A crashed background task should be one
    line in the log, immediately, with its name on it.
    """
    import asyncio
    task = asyncio.create_task(coro, name=name)
    _background.add(task)

    def _done(t: "asyncio.Task") -> None:
        _background.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            import logging
            logging.getLogger("jarvis.events").error(
                "background task %r failed", t.get_name(), exc_info=exc)

    task.add_done_callback(_done)
    return task
