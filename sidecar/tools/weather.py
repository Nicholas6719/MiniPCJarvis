"""Weather without API keys: Open-Meteo (geocoding + forecast). Fast (<1 s), reliable,
and a brain reflex speaks it directly - no LLM, no web search loops."""
from __future__ import annotations

import logging
import time

import httpx

from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.weather")

_CODES = {
    0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains", 80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "snow showers", 86: "heavy snow showers", 95: "thunderstorms", 96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}
_geo_cache: dict[str, tuple[float, float, str]] = {}
_home: tuple[float, float, str] | None = None
_home_ts = 0.0


async def _one_geocode(name: str) -> list:
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": name, "count": 1, "language": "en",
                                "format": "json"})
        return (r.json() or {}).get("results") or []


def _variants(place: str) -> list[str]:
    """Ways to ask for the same place, commonest first.

    Open-Meteo's geocoder wants "Framingham, MA" and returns NOTHING for
    "Framingham MA" — and speech recognition never inserts a comma. So asking
    aloud for the weather in his own town failed outright, and phase 2's
    distance_to inherited it the moment it reused this. Retry with the comma,
    then with the trailing region dropped, before giving up.
    """
    p = " ".join((place or "").split())
    out = [p]
    if "," not in p:
        parts = p.split()
        if len(parts) >= 2:
            # "Framingham MA" -> "Framingham, MA"; "New York NY" -> "New York, NY"
            out.append(" ".join(parts[:-1]) + ", " + parts[-1])
            # ...and finally the bare town, which resolves on its own
            out.append(" ".join(parts[:-1]))
    return out


async def _geocode(place: str) -> tuple[float, float, str] | None:
    key = (place or "").strip().lower()
    if key in _geo_cache:
        return _geo_cache[key]
    res: list = []
    for attempt in _variants(place):
        res = await _one_geocode(attempt)
        if res:
            break
    if not res:
        return None
    g = res[0]
    label = g["name"] + (f", {g['admin1']}" if g.get("admin1") and g.get("country_code") == "US" else
                         f", {g['country']}" if g.get("country") else "")
    _geo_cache[key] = (float(g["latitude"]), float(g["longitude"]), label)
    return _geo_cache[key]


async def _home_location() -> tuple[float, float, str] | None:
    """config weather.home ("Framingham, MA") or, failing that, the PC's public IP location."""
    global _home, _home_ts
    home = config.get("weather", "home", default="")
    if home:
        return await _geocode(home)
    if _home and time.time() - _home_ts < 6 * 3600:
        return _home
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            j = (await c.get("http://ip-api.com/json/?fields=status,city,regionName,lat,lon")).json()
        if j.get("status") == "success":
            _home = (float(j["lat"]), float(j["lon"]), f"{j['city']}, {j['regionName']}")
            _home_ts = time.time()
            return _home
    except Exception as e:
        log.warning("ip geolocation failed: %s", e)
    return None


def _phone_location() -> tuple[float, float, str] | None:
    """Where his PHONE last said he was, if that is still worth believing.

    Beats the configured home when it exists: "what's the weather" asked from
    somewhere else should answer about somewhere else. It is deliberately
    subject to the same staleness window as every other volatile fact — an
    eight-hour-old fix is not where he is, and quietly using it would be the
    same class of mistake as the camera claiming to see him after he left.
    """
    try:
        from config import config as _c
        import volatile
        window = float(_c.get("location", "stale_after_minutes", default=120))
        got = volatile.fresh("location", window)
        if not got:
            return None
        v = got["value"]
        lat, lon = float(v["lat"]), float(v["lon"])
        return lat, lon, v.get("label") or "your current position"
    except Exception:
        log.debug("no usable phone fix", exc_info=True)
        return None


async def get_weather(location: str = "", when: str = "now") -> dict:
    """Current conditions and today's/tomorrow's outlook for a place (default: home)."""
    loc = None
    if location:
        loc = await _geocode(location)
    else:
        loc = _phone_location() or await _home_location()
    if not loc:
        return {"error": f"I couldn't find a place called {location}" if location else "I don't know where you are yet"}
    lat, lon, label = loc
    unit = config.get("weather", "units", default="fahrenheit")
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto",
            "temperature_unit": unit, "wind_speed_unit": "mph" if unit == "fahrenheit" else "kmh",
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,sunrise,sunset",
            "forecast_days": 3})
        j = r.json()
    cur, day = j.get("current", {}), j.get("daily", {})
    idx = 1 if when == "tomorrow" else 0
    # Live data is spoken with its age, the same rule the market tools follow:
    # never cached, and never presented as timeless. Open-Meteo stamps the
    # observation itself, which can trail the request by up to an hour, so the
    # honest number is the OBSERVATION's age and not the time we asked.
    obs_age_min = None
    try:
        import datetime as _dt
        t = cur.get("time")
        if t:
            obs = _dt.datetime.fromisoformat(t)
            now_local = _dt.datetime.now(obs.tzinfo) if obs.tzinfo else _dt.datetime.now()
            obs_age_min = max(0.0, round((now_local - obs).total_seconds() / 60.0, 1))
    except Exception:
        log.debug("could not read the observation time", exc_info=True)
    out = {
        "location": label, "units": "F" if unit == "fahrenheit" else "C",
        "as_of_minutes": obs_age_min,
        "now": {"temp": round(cur.get("temperature_2m", 0)), "feels_like": round(cur.get("apparent_temperature", 0)),
                "conditions": _CODES.get(cur.get("weather_code", -1), "unsettled"),
                "humidity": cur.get("relative_humidity_2m"), "wind": round(cur.get("wind_speed_10m", 0)),
                "wind_unit": "mph" if unit == "fahrenheit" else "km/h"},
        "today" if idx == 0 else "tomorrow": {
            "high": round(day["temperature_2m_max"][idx]), "low": round(day["temperature_2m_min"][idx]),
            "conditions": _CODES.get(day["weather_code"][idx], "unsettled"),
            "rain_chance": day.get("precipitation_probability_max", [None] * 3)[idx]},
    }
    return out


def register_all() -> None:
    registry.register(Tool(
        name="get_weather",
        description="Current weather and today's or tomorrow's outlook for a city (or the user's "
                    "home location if none given). Use this for any weather question instead of searching.",
        parameters={"type": "object", "properties": {
            "location": {"type": "string", "description": "city, optionally with state/country; empty = home"},
            "when": {"type": "string", "enum": ["now", "today", "tomorrow"]}},
            "required": []},
        risk=Risk.SAFE, handler=get_weather, timeout=20))
