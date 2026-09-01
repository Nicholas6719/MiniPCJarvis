"""Apple Watch / HealthKit metrics, pushed from his phone.

iOS Shortcuts calls the Telegram Bot API directly into the already-paired chat,
as a message or a small JSON document. It rides the existing poller: no new
listening endpoint, no second entry point, the same allowed-chat check as
everything else inbound. Accepted trust boundary — the data transits Telegram in
flight exactly as remote control already does, which is not a regression to
relitigate.

THIS PARSES UNTRUSTED EXTERNAL JSON, so it is written like it. Size-capped before
parsing, never evaluated, every value type-checked, unknown keys ignored rather
than stored, and nothing in here raises into the poller — the poller carries
reminders, alerts and remote turns, and a malformed health payload must never be
able to take that down. A bad payload gets a plain sentence.

Readings expire. "Your heart rate is 58" from this morning is not his heart rate,
so every answer carries the age and a stale reading is labelled stale.
"""
from __future__ import annotations

import json
import logging

import volatile
from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.health")

KEY_PREFIX = "health:"

# What we accept, and how it is said aloud. Anything not on this list is ignored
# rather than stored: an allow-list, because the sender is outside this program.
METRICS: dict[str, tuple[str, str, float, float]] = {
    # key: (spoken name, unit, sane min, sane max)
    "heart_rate":        ("heart rate", "bpm", 20, 240),
    "resting_heart_rate": ("resting heart rate", "bpm", 25, 150),
    "hrv":               ("heart rate variability", "ms", 1, 400),
    "steps":             ("steps", "", 0, 200_000),
    "active_energy":     ("active energy", "calories", 0, 20_000),
    "exercise_minutes":  ("exercise", "minutes", 0, 1440),
    "stand_hours":       ("stand hours", "hours", 0, 24),
    "sleep_hours":       ("sleep", "hours", 0, 24),
    "blood_oxygen":      ("blood oxygen", "%", 50, 100),
    "respiratory_rate":  ("respiratory rate", "breaths a minute", 4, 60),
    "body_weight":       ("weight", "lb", 30, 800),
    "vo2_max":           ("VO2 max", "", 5, 100),
}

_ALIASES = {
    "heartrate": "heart_rate", "hr": "heart_rate", "bpm": "heart_rate",
    "restingheartrate": "resting_heart_rate", "resting_hr": "resting_heart_rate",
    "heart_rate_variability": "hrv", "step_count": "steps",
    "activeenergy": "active_energy", "active_calories": "active_energy",
    "exercise": "exercise_minutes", "sleep": "sleep_hours",
    "spo2": "blood_oxygen", "oxygen_saturation": "blood_oxygen",
    "weight": "body_weight", "respiration_rate": "respiratory_rate",
}


def _max_bytes() -> int:
    return int(config.get("health", "max_payload_bytes", default=65536))


def _window() -> float:
    return float(config.get("health", "stale_after_minutes", default=180))


def looks_like_payload(text: str) -> bool:
    """Cheap enough to run on every inbound message, strict enough not to hijack
    one. A sentence he actually said must never be swallowed as telemetry."""
    t = (text or "").lstrip()
    if not t.startswith("{") or len(t) > _max_bytes():
        return False
    try:
        obj = json.loads(t)
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    if str(obj.get("type", "")).lower() in ("health", "healthkit"):
        return True
    return any(_canon(k) in METRICS for k in obj)


def _canon(key: str) -> str:
    k = str(key).strip().lower().replace(" ", "_").replace("-", "_")
    return _ALIASES.get(k, k)


def ingest_payload(raw: str) -> dict:
    """Store what is recognisable. Never raises — called from the poller.

    Returns a report rather than throwing, so the caller can tell him what
    landed and what was ignored instead of failing silently.
    """
    try:
        if not isinstance(raw, str) or len(raw) > _max_bytes():
            return {"error": "that payload was too large to read", "stored": 0}
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"error": "that payload was not an object", "stored": 0}
        body = obj.get("metrics") if isinstance(obj.get("metrics"), dict) else obj
        stored, ignored = [], []
        for key, value in list(body.items())[:64]:      # bounded, whatever arrives
            name = _canon(key)
            spec = METRICS.get(name)
            if not spec:
                ignored.append(str(key)[:32])
                continue
            try:
                num = float(value)
            except (TypeError, ValueError):
                ignored.append(str(key)[:32])
                continue
            if num != num or num in (float("inf"), float("-inf")):
                ignored.append(str(key)[:32])       # NaN/inf from a bad sensor read
                continue
            _spoken, unit, lo, hi = spec
            if not (lo <= num <= hi):
                # Out of physiological range is far more likely a unit mix-up or a
                # glitch than a medical emergency, and storing it would have JARVIS
                # calmly reporting a heart rate of 4,000.
                log.warning("health: %s=%s outside %s-%s, ignored", name, num, lo, hi)
                ignored.append(str(key)[:32])
                continue
            if volatile.put(KEY_PREFIX + name, {"value": num, "unit": unit},
                            source="telegram"):
                stored.append(name)
        return {"stored": len(stored), "metrics": stored, "ignored": ignored}
    except Exception as e:
        log.exception("health payload ingest failed")
        return {"error": f"that payload could not be read: {e}", "stored": 0}


async def get_health(metric: str = "") -> dict:
    want = _canon(metric) if metric else ""
    if want and want not in METRICS:
        return {"error": f"I don't track {metric}, sir"}
    names = [want] if want else list(METRICS)
    window, out = _window(), []
    for name in names:
        got = volatile.get(KEY_PREFIX + name)
        if not got:
            continue
        spoken, unit, _lo, _hi = METRICS[name]
        v = got["value"]
        out.append({"metric": name, "spoken": spoken,
                    "value": v.get("value"), "unit": v.get("unit", unit),
                    "as_of": volatile.spoken_age(got["age_minutes"]),
                    "age_minutes": got["age_minutes"],
                    "stale": got["age_minutes"] > window})
    if not out:
        return {"error": "I don't have anything from your watch yet, sir."
                if not want else f"I don't have a recent {metric} reading, sir."}
    out.sort(key=lambda m: m["age_minutes"])
    return {"metrics": out, "count": len(out)}


def register_all() -> None:
    registry.register(Tool(
        name="get_health",
        description="The user's most recent health metrics from his watch — heart rate, "
                    "steps, sleep, blood oxygen and so on. Always answered with how old "
                    "the reading is. Never diagnose from these; report them.",
        parameters={"type": "object", "properties": {
            "metric": {"type": "string", "description": "empty = everything recent"}},
            "required": []},
        risk=Risk.SAFE, handler=get_health, timeout=20))
