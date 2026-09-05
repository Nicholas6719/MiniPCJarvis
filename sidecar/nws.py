"""The National Weather Service, for the emergencies the news desks are slow to.

He wants local emergencies and nothing else by day. A tornado warning for
Southeast Middlesex is the purest case there is, and the RSS desks carry it
minutes late if at all. The NWS publishes every watch, warning and advisory
for a point, free, no key (api.weather.gov/alerts/active?point=lat,lon) -
verified 2026-09-05 from Framingham: 200, geo+json, a Rip Current Statement
active for the county that afternoon.

Tiers follow the news classifier's meaning, not the NWS's own words:

    URGENT   a WARNING for something that kills - tornado, flash flood,
             severe thunderstorm, hurricane, blizzard, ice storm, extreme
             cold/heat, or anything NWS itself marks Extreme and Immediate
    ALERT    any other warning, or a watch for the killing kinds
    NOTABLE  a watch, advisory or statement: waits for the brief
    NONE     tests, and anything already expired

Everything spoken names the NWS, says what, where and until when, and gives
the one sentence of instruction the alert carries. Nothing here guesses.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

log = logging.getLogger("jarvis.nws")

API = "https://api.weather.gov/alerts/active"
UA = {"User-Agent": "JARVIS-personal-assistant/0.3 (local desktop app)",
      "Accept": "application/geo+json"}

# the kinds that kill people: a WARNING for these wakes him wherever he is
DEADLY = re.compile(
    r"\b(?:tornado|flash flood|severe thunderstorm|hurricane|tropical storm|"
    r"blizzard|ice storm|extreme cold|extreme heat|excessive heat|"
    r"storm surge|tsunami|fire weather|red flag|dust storm)\b", re.I)

URGENT, ALERT, NOTABLE, NONE = "urgent", "alert", "notable", ""


def classify(alert: dict, now: dt.datetime | None = None) -> tuple[str, str]:
    """(tier, why) for one NWS alert's properties."""
    p = alert.get("properties") if "properties" in alert else alert
    event = str(p.get("event") or "")
    if not event or p.get("status", "Actual") != "Actual" or "test" in event.lower():
        return NONE, "a test, or not a real alert"
    exp = _when(p.get("expires") or p.get("ends"))
    now = now or dt.datetime.now(dt.timezone.utc)
    if exp and exp <= now:
        return NONE, "already expired"
    kind = ("warning" if re.search(r"\bwarning\b", event, re.I) else
            "watch" if re.search(r"\bwatch\b", event, re.I) else "advice")
    severity = str(p.get("severity") or "")
    urgency = str(p.get("urgency") or "")
    if kind == "warning" and (DEADLY.search(event)
                              or (severity == "Extreme" and urgency == "Immediate")):
        return URGENT, f"a {event.lower()} for his ground"
    if kind == "warning":
        return ALERT, f"a {event.lower()}, still a warning"
    if kind == "watch" and DEADLY.search(event):
        return ALERT, f"a {event.lower()}: it may turn into the real thing"
    return NOTABLE, f"a {event.lower()} - worth knowing, not worth stopping for"


def _when(s) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
    except ValueError:
        return None


def _until(p: dict) -> str:
    exp = _when(p.get("expires") or p.get("ends"))
    if not exp:
        return ""
    local = exp.astimezone()
    hour = local.strftime("%I:%M %p").lstrip("0")
    today = dt.datetime.now().astimezone().date()
    day = ("" if local.date() == today else
           " tomorrow" if local.date() == today + dt.timedelta(days=1) else
           " on " + local.strftime("%A"))
    return f" until {hour}{day}"


def _instruction(p: dict) -> str:
    text = str(p.get("instruction") or p.get("description") or "").strip()
    text = re.sub(r"\s+", " ", text)
    m = re.match(r"(.{20,220}?[.!])(?:\s|$)", text)
    return (m.group(1) if m else text[:160]).strip()


def spoken(alert: dict) -> str:
    """'The Weather Service has a Tornado Warning for Southeast Middlesex until
    4:15 PM. Take shelter now in a basement or an interior room.'"""
    p = alert.get("properties") if "properties" in alert else alert
    event = str(p.get("event") or "weather alert")
    area = str(p.get("areaDesc") or "").split(";")[0].strip()
    where = f" for {area}" if area else " for your area"
    line = f"The Weather Service has a {event}{where}{_until(p)}."
    inst = _instruction(p)
    if inst and inst.lower() not in line.lower():
        line += " " + inst
    return line


def key_of(alert: dict) -> str:
    p = alert.get("properties") if "properties" in alert else alert
    return "nws:" + str(p.get("id") or alert.get("id") or p.get("event") or "")[:120]


async def active(lat: float, lon: float) -> list[dict]:
    """Every active alert for the point, as the API's feature dicts."""
    import httpx
    async with httpx.AsyncClient(timeout=10, headers=UA) as c:
        r = await c.get(API, params={"point": f"{lat:.4f},{lon:.4f}"})
        r.raise_for_status()
        return list((r.json() or {}).get("features") or [])


async def scan(seen: set[str] | None = None) -> list[tuple[str, str, str]]:
    """(text, tier, key) for every alert at home worth saying, newest first."""
    try:
        from tools.weather import _home_location
        home = await _home_location()
    except Exception:
        log.debug("no home location for the weather service", exc_info=True)
        return []
    if not home:
        return []
    lat, lon, _name = home
    try:
        feats = await active(lat, lon)
    except Exception as e:
        log.info("weather service unreachable: %s", e)
        return []
    out: list[tuple[str, str, str]] = []
    for f in feats:
        tier, _why = classify(f)
        if tier == NONE:
            continue
        key = key_of(f)
        if seen is not None and key in seen:
            continue
        out.append((spoken(f), tier, key))
    return out
