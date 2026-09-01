"""Where he is, from his phone. STUB — Phase 2 fills these in.

The fix arrives as a Telegram live-location update on the ALREADY PAIRED chat and
rides the existing poller: no new endpoint, no second entry point, the same
allowed-chat check as everything else inbound.

Distance is straight-line (haversine) only. No routing, no OSRM — out of scope by
instruction. Place names resolve through weather._geocode(), which already talks
to Open-Meteo's free geocoder, so this adds no new network dependency and no
usage policy to honour.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.location")


async def where_am_i() -> dict:
    raise NotImplementedError("location: Phase 2")


async def distance_to(place: str) -> dict:
    raise NotImplementedError("location: Phase 2")


def register_all() -> None:
    registry.register(Tool(
        name="where_am_i",
        description="Where the user is, from the most recent location his phone shared. "
                    "Always answered with how old the fix is.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=where_am_i, timeout=20))
    registry.register(Tool(
        name="distance_to",
        description="Straight-line distance from the user's last known position to a named "
                    "place. Not driving directions — there is no routing.",
        parameters={"type": "object", "properties": {
            "place": {"type": "string"}}, "required": ["place"]},
        risk=Risk.SAFE, handler=distance_to, timeout=30))
