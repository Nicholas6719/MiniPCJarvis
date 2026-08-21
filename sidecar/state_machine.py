"""Canonical JARVIS state machine. The sidecar owns this; UI mirrors it."""
from __future__ import annotations

import asyncio
import enum
import time
from typing import Awaitable, Callable


class State(str, enum.Enum):
    OFFLINE = "offline"
    STARTING = "starting"
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"      # STT finalization / intent
    THINKING = "thinking"          # LLM generating
    SEARCHING = "searching"        # web search tool active
    EXECUTING = "executing"        # non-search tool active
    WAITING = "waiting"            # blocked on user confirmation
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    SLEEPING = "sleeping"


_ALLOWED: dict[State, set[State]] = {
    State.OFFLINE: {State.STARTING},
    State.STARTING: {State.IDLE, State.ERROR},
    State.IDLE: {State.LISTENING, State.SLEEPING, State.ERROR, State.THINKING},
    State.LISTENING: {State.PROCESSING, State.IDLE, State.ERROR},
    State.PROCESSING: {State.THINKING, State.IDLE, State.ERROR},
    State.THINKING: {State.SEARCHING, State.EXECUTING, State.WAITING,
                     State.SPEAKING, State.IDLE, State.INTERRUPTED, State.ERROR},
    State.SEARCHING: {State.THINKING, State.ERROR, State.INTERRUPTED},
    State.EXECUTING: {State.THINKING, State.ERROR, State.INTERRUPTED},
    State.WAITING: {State.THINKING, State.EXECUTING, State.IDLE, State.INTERRUPTED, State.ERROR},
    State.SPEAKING: {State.IDLE, State.INTERRUPTED, State.LISTENING, State.ERROR},
    State.INTERRUPTED: {State.LISTENING, State.IDLE, State.PROCESSING, State.ERROR},
    State.ERROR: {State.IDLE, State.STARTING},
    State.SLEEPING: {State.IDLE, State.LISTENING},
}


class StateMachine:
    def __init__(self) -> None:
        self._state = State.OFFLINE
        self._listeners: list[Callable[[State, State], Awaitable[None]]] = []
        self._lock = asyncio.Lock()
        self.changed_at = time.time()

    @property
    def state(self) -> State:
        return self._state

    def on_change(self, cb: Callable[[State, State], Awaitable[None]]) -> None:
        self._listeners.append(cb)

    async def to(self, new: State, force: bool = False) -> bool:
        async with self._lock:
            old = self._state
            if new == old:
                return True
            if not force and new not in _ALLOWED.get(old, set()):
                return False
            self._state = new
            self.changed_at = time.time()
        for cb in list(self._listeners):
            try:
                await cb(old, new)
            except Exception:
                pass
        return True
