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
        global _main_loop
        _main_loop = asyncio.get_running_loop()
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
                # AND CLOSE IT, or the HUD never finds out. Detached, the socket
                # was skipped by every later emit while the client still sat in
                # receive() with the connection open — no `onclose`, so no
                # reconnect — and the orb showed the last state it had heard
                # until the page was reloaded, while the sidecar was healthy.
                # A best-effort close makes the HUD's reconnect path run.
                try:
                    await asyncio.wait_for(ws.close(code=1011), timeout=1.0)
                except Exception:
                    pass
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
# The loop the app runs on, recorded by every emit(), so a sync tool in the
# executor can still hand a coroutine home (see spawn).
_main_loop: "asyncio.AbstractEventLoop | None" = None


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
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # FROM A WORKER THREAD. Sync tools (focus_window, close_application)
        # run in the tool executor, and a create_task there raises - so
        # "focus on the helmet" handed to the hologram from focus_window
        # went nowhere and said "no window" (release 50, 2026-09-06). The
        # coroutine goes to the main loop, which every emit() records.
        if _main_loop is None or _main_loop.is_closed():
            coro.close()
            raise RuntimeError("no event loop to hand this to")
        return asyncio.run_coroutine_threadsafe(coro, _main_loop)
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
