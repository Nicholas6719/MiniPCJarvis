"""Where he is, from his phone.

The fix arrives as a Telegram location on the ALREADY PAIRED chat and rides the
existing poller: no new endpoint, no second entry point, the same allowed-chat
check as everything else inbound.

TWO THINGS THAT WOULD HAVE MADE THIS SILENTLY NOT WORK, both found by reading the
poller before writing this:

  1. A LIVE location arrives as `edited_message`, not `message` — Telegram edits
     the original message each time the position moves. The handler only read
     `message`, so live sharing would have delivered exactly one fix and then
     gone quiet forever.
  2. `getUpdates` was called with `allowed_updates=["message","callback_query"]`.
     Telegram does not merely ignore other kinds, it does not SEND them, so
     `edited_message` would never have arrived at all no matter what the handler
     did. Both are fixed in remote_telegram.py.

Distance is straight-line (haversine) only — no routing, no OSRM, by instruction.
Place names resolve through weather._geocode(), which already talks to
Open-Meteo's free geocoder, so this adds no second network dependency and no
usage policy to honour. Nominatim stays available as a fallback if a place ever
fails to resolve; it is not the first reach.

Every answer carries the age of the fix. A position from four hours ago is not
where he is.
"""
from __future__ import annotations

import logging
import math

import volatile
from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.location")

KEY = "location"
EARTH_MILES = 3958.7613


def _window() -> float:
    return float(config.get("location", "stale_after_minutes", default=120))


def ingest(loc: dict, label: str = "") -> bool:
    """Called by the Telegram poller, never by the model. Never raises."""
    try:
        lat, lon = float(loc["latitude"]), float(loc["longitude"])
    except Exception:
        log.warning("telegram location had no usable coordinates: %r", loc)
        return False
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        log.warning("telegram location out of range: %s,%s", lat, lon)
        return False
    value = {"lat": lat, "lon": lon, "label": label or ""}
    acc = loc.get("horizontal_accuracy")
    if isinstance(acc, (int, float)):
        value["accuracy_m"] = round(float(acc))
    return volatile.put(KEY, value, source="telegram")


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance. Straight line — not a driving distance, and the
    tools that use it say so, because "twelve miles" that turns out to mean
    twenty-five minutes of back roads is a worse answer than no answer."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_MILES * math.asin(min(1.0, math.sqrt(a)))


async def where_am_i() -> dict:
    got = volatile.get(KEY)
    if not got:
        return {"error": "I don't have a position from your phone yet, sir — share "
                         "your location with me on Telegram and I will."}
    v = got["value"]
    stale = got["age_minutes"] > _window()
    out = {"lat": v["lat"], "lon": v["lon"],
           "as_of": volatile.spoken_age(got["age_minutes"]),
           "age_minutes": got["age_minutes"], "stale": stale}
    if v.get("label"):
        out["label"] = v["label"]
    if v.get("accuracy_m"):
        out["accuracy_m"] = v["accuracy_m"]
    return out


async def distance_to(place: str) -> dict:
    place = (place or "").strip()
    if not place:
        return {"error": "where to, sir?"}
    got = volatile.get(KEY)
    if not got:
        return {"error": "I don't know where you are, sir — share your location on "
                         "Telegram first."}
    from tools.weather import _geocode
    target = await _geocode(place)
    if not target:
        return {"error": f"I couldn't find a place called {place}"}
    lat, lon, label = target
    v = got["value"]
    miles = haversine_miles(v["lat"], v["lon"], lat, lon)
    return {"place": label, "miles": round(miles, 1),
            "km": round(miles * 1.609344, 1),
            "straight_line": True,          # never presented as a driving distance
            "as_of": volatile.spoken_age(got["age_minutes"]),
            "stale": got["age_minutes"] > _window()}


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
                    "place. NOT driving directions and not a driving distance — there is no "
                    "routing; say 'as the crow flies' when reporting it.",
        parameters={"type": "object", "properties": {
            "place": {"type": "string"}}, "required": ["place"]},
        risk=Risk.SAFE, handler=distance_to, timeout=30))
