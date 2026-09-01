"""Apple Watch / HealthKit metrics, pushed from his phone. STUB — Phase 2.

iOS Shortcuts calls the Telegram Bot API directly into the already-paired chat.
Accepted trust boundary: the data transits Telegram in flight, exactly as remote
control already does — not a regression to relitigate.

The payload is EXTERNAL JSON and is treated as untrusted: size-capped, type-
checked, unknown keys ignored, never evaluated. A malformed payload gets a plain
sentence, never an exception into the poller.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.health")


async def get_health(metric: str = "") -> dict:
    raise NotImplementedError("health: Phase 2")


def ingest_payload(raw: str) -> dict:
    """Called by the Telegram poller, not by the model. Never raises."""
    raise NotImplementedError("health: Phase 2")


def register_all() -> None:
    registry.register(Tool(
        name="get_health",
        description="The user's most recent health metrics from his watch — heart rate, "
                    "steps, sleep, and so on. Always answered with how old the reading is.",
        parameters={"type": "object", "properties": {
            "metric": {"type": "string", "description": "empty = everything recent"}},
            "required": []},
        risk=Risk.SAFE, handler=get_health, timeout=20))
