"""His calendar and his Reminders, pushed from the phone.

The same road as health.py (2026-09-01): iOS Shortcuts posts a small JSON
document into the already-paired Telegram chat, the poller recognises it, and
nothing new listens on the network. The same discipline too - THIS PARSES
UNTRUSTED EXTERNAL JSON: size-capped before parsing, never evaluated, every
value type-checked and length-capped, unknown keys ignored, and nothing here
raises into the poller. A bad payload gets a plain sentence; a good one gets
silence, because it arrives every quarter of an hour and he does not want
ninety receipts a day on his phone.

Two documents, replaced whole each time (the phone is the source of truth):

    {"type": "calendar", "events": [{"title", "start", "end", "location",
                                     "all_day", "calendar"}, ...]}
    {"type": "reminders", "items": [{"title", "due", "list", "priority",
                                     "notes"}, ...]}

Times are ISO-8601 as Shortcuts formats them; a trailing Z or an offset is
honoured, a naive time is his local time. Readings expire like the watch's:
an agenda from three hours ago is labelled as such.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re

import volatile
from config import config
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.phone")

KEY_CAL = "phone:calendar"
KEY_REM = "phone:reminders"
KEY_STATUS = "phone:status"      # battery, charging, place, focus - from Shortcuts automations
MAX_EVENTS = 60
MAX_REMINDERS = 80
_TEXT = 120


def _max_bytes() -> int:
    return int(config.get("phone", "max_payload_bytes", default=65536))


def _window() -> float:
    return float(config.get("phone", "stale_after_minutes", default=60))


def _now() -> dt.datetime:
    return dt.datetime.now()


def _s(v, n: int = _TEXT) -> str:
    return str(v).strip()[:n] if isinstance(v, (str, int, float)) else ""


def _parse_dt(v) -> dt.datetime | None:
    """ISO-8601 from Shortcuts, in any of the shapes it produces; None if not."""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        # "9/15/2026, 3:00 PM" - the Shortcuts default when nobody sets a format
        for fmt in ("%m/%d/%Y, %I:%M %p", "%m/%d/%Y %I:%M %p", "%m/%d/%Y, %I:%M:%S %p",
                    "%Y-%m-%d %H:%M", "%m/%d/%Y"):
            try:
                d = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)      # into his local clock
    return d


def kind_of(text: str) -> str | None:
    """'calendar' or 'reminders' if this is one of ours; None if it is speech."""
    t = (text or "").lstrip()
    if not t.startswith("{") or len(t) > _max_bytes():
        return None
    try:
        obj = json.loads(t)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    kind = str(obj.get("type", "")).lower()
    if kind in ("calendar", "events", "agenda"):
        return "calendar"
    if kind in ("reminders", "todo", "todos", "tasks"):
        return "reminders"
    if kind in ("status", "battery", "phone"):
        return "status"
    if isinstance(obj.get("events"), list):
        return "calendar"
    if isinstance(obj.get("items"), list) or isinstance(obj.get("reminders"), list):
        return "reminders"
    return None


def looks_like_payload(text: str) -> bool:
    return kind_of(text) is not None


def _clean_event(e) -> dict | None:
    if not isinstance(e, dict):
        return None
    title = _s(e.get("title") or e.get("name"))
    start = _parse_dt(e.get("start") or e.get("start_date"))
    if not title or start is None:
        return None
    end = _parse_dt(e.get("end") or e.get("end_date"))
    all_day = bool(e.get("all_day") or e.get("is_all_day"))
    return {"title": title, "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes") if end else "",
            "location": _s(e.get("location")), "all_day": all_day,
            "calendar": _s(e.get("calendar"), 40)}


def _clean_reminder(r) -> dict | None:
    if not isinstance(r, dict):
        return None
    title = _s(r.get("title") or r.get("name"))
    if not title:
        return None
    if r.get("completed") is True or r.get("is_completed") is True:
        return None
    due = _parse_dt(r.get("due") or r.get("due_date"))
    pri = r.get("priority")
    try:
        pri = int(pri) if pri is not None else 0
    except (TypeError, ValueError):
        pri = 0
    return {"title": title, "due": due.isoformat(timespec="minutes") if due else "",
            "list": _s(r.get("list"), 40), "priority": max(0, min(9, pri)),
            "notes": _s(r.get("notes"), 200)}


def ingest_payload(raw: str) -> dict:
    """Store the document whole. Never raises - called from the poller."""
    try:
        if not isinstance(raw, str) or len(raw) > _max_bytes():
            return {"error": "that payload was too large to read", "stored": 0}
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return {"error": "that payload was not an object", "stored": 0}
        kind = kind_of(raw)
        if kind == "calendar":
            src = obj.get("events") if isinstance(obj.get("events"), list) else []
            kept = [x for x in (_clean_event(e) for e in src[:MAX_EVENTS]) if x]
            kept.sort(key=lambda x: x["start"])
            ok = volatile.put(KEY_CAL, {"events": kept, "count": len(kept)}, source="phone")
            return {"type": "calendar", "stored": len(kept) if ok else 0,
                    "ignored": max(0, len(src) - len(kept))}
        if kind == "reminders":
            src = obj.get("items") if isinstance(obj.get("items"), list) else obj.get("reminders")
            src = src if isinstance(src, list) else []
            kept = [x for x in (_clean_reminder(r) for r in src[:MAX_REMINDERS]) if x]
            kept.sort(key=lambda x: (x["due"] == "", x["due"], -x["priority"]))
            ok = volatile.put(KEY_REM, {"items": kept, "count": len(kept)}, source="phone")
            return {"type": "reminders", "stored": len(kept) if ok else 0,
                    "ignored": max(0, len(src) - len(kept))}
        if kind == "status":
            st = _clean_status(obj)
            ok = volatile.put(KEY_STATUS, st, source="phone")
            return {"type": "status", "stored": 1 if ok else 0}
        return {"error": "that payload was not a calendar, a reminders list or a status", "stored": 0}
    except Exception as e:
        log.warning("phone payload unreadable: %s", e)   # his input, not a bug: no traceback
        return {"error": f"that payload could not be read: {e}", "stored": 0}


# ----------------------------------------------------------------- speaking
def _clock(d: dt.datetime) -> str:
    h = d.hour % 12 or 12
    ampm = "AM" if d.hour < 12 else "PM"
    return f"{h} {ampm}" if d.minute == 0 else f"{h}:{d.minute:02d} {ampm}"


def _day_word(d: dt.datetime, now: dt.datetime) -> str:
    delta = (d.date() - now.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if 1 < delta < 7:
        return d.strftime("%A")
    return d.strftime("%B %d").replace(" 0", " ")


def _when(ev: dict, now: dt.datetime, with_day: bool) -> str:
    start = _parse_dt(ev.get("start"))
    if start is None:
        return ""
    if ev.get("all_day"):
        return f"all day {_day_word(start, now)}" if with_day else "all day"
    t = _clock(start)
    return f"{_day_word(start, now)} at {t}" if with_day else t


def _events() -> tuple[list[dict], dict | None]:
    got = volatile.get(KEY_CAL)
    if not got:
        return [], None
    return list((got.get("value") or {}).get("events") or []), got


async def get_agenda(day: str = "today") -> dict:
    """His calendar from the phone: today, tomorrow, the week, or the next thing."""
    events, got = _events()
    if got is None:
        return {"error": "I don't have your calendar from your phone yet, sir."}
    now = _now()
    want = (day or "today").strip().lower()
    span_days, label = 1, "today"
    base = now.date()
    if want == "tomorrow":
        base, label = base + dt.timedelta(days=1), "tomorrow"
    elif want in ("week", "this week", "coming up"):
        span_days, label = 7, "this week"
    elif want == "next":
        span_days, label = 14, "next"
    picked = []
    for ev in events:
        s = _parse_dt(ev.get("start"))
        if s is None:
            continue
        if want == "next":
            if s < now:
                continue
        elif not (base <= s.date() < base + dt.timedelta(days=span_days)):
            continue
        picked.append({**ev, "when": _when(ev, now, with_day=span_days > 1 or want == "next")})
    if want == "next":
        picked = picked[:1]
    return {"day": label, "events": picked, "count": len(picked),
            "as_of": volatile.spoken_age(got["age_minutes"]),
            "age_minutes": got["age_minutes"], "stale": got["age_minutes"] > _window()}


async def get_phone_reminders(list_name: str = "") -> dict:
    """What is on his Reminders lists, from the phone."""
    got = volatile.get(KEY_REM)
    if not got:
        return {"error": "I don't have your reminders from your phone yet, sir."}
    items = list((got.get("value") or {}).get("items") or [])
    want = (list_name or "").strip().lower()
    if want:
        items = [r for r in items if want in str(r.get("list") or "").lower()]
    now = _now()
    out = []
    for r in items:
        due = _parse_dt(r.get("due"))
        out.append({**r, "when": (_day_word(due, now) + (" at " + _clock(due) if due.time() != dt.time(0, 0) else ""))
                    if due else "", "overdue": bool(due and due < now)})
    return {"list": want, "reminders": out, "count": len(out),
            "as_of": volatile.spoken_age(got["age_minutes"]),
            "age_minutes": got["age_minutes"], "stale": got["age_minutes"] > _window()}


def _clean_status(obj: dict) -> dict:
    """{'type':'status','battery':18,'charging':false,'place':'left campus','focus':'Work'}
    from an iOS Shortcuts automation (Battery Level / Charger / Arrive-Leave / Focus triggers)."""
    out: dict = {}
    b = obj.get("battery", obj.get("battery_level"))
    try:
        if b is not None:
            b = float(str(b).rstrip("% "))
            out["battery"] = int(round(b * 100 if 0 < b <= 1 else b))
    except (TypeError, ValueError):
        pass
    ch = obj.get("charging", obj.get("charger"))
    if isinstance(ch, str):
        ch = ch.strip().lower() in ("true", "yes", "connected", "charging", "1")
    if ch is not None:
        out["charging"] = bool(ch)
    for k in ("place", "focus", "note"):
        v = _s(obj.get(k), 60)
        if v:
            out[k] = v
    return out


def status() -> dict | None:
    """The phone's last status while it is fresh (an hour), else None."""
    got = volatile.get(KEY_STATUS)
    if not got or got["age_minutes"] > max(_window(), 60):
        return None
    v = dict(got["value"] or {})
    v["age_minutes"] = got["age_minutes"]
    return v


def upcoming(minutes: float = 12) -> list[dict]:
    """Events starting within `minutes` (or just started, up to 2 min ago),
    each with 'minutes' until start. Empty when the calendar is stale."""
    got = volatile.get(KEY_CAL)
    if not got or got["age_minutes"] > 24 * 60:
        return []
    now = _now()
    out = []
    for ev in (got["value"] or {}).get("events") or []:
        if ev.get("all_day"):
            continue
        start = _parse_dt(ev.get("start"))
        if start is None:
            continue
        if start.tzinfo is not None:
            start = start.astimezone().replace(tzinfo=None)
        delta = (start - now).total_seconds() / 60.0
        if -2 <= delta <= minutes:
            out.append({**ev, "minutes": round(delta, 1)})
    return out


def today_lines() -> list[tuple[str, str]]:
    """Today's calendar for the morning brief, as (spoken, written) - beside the
    reminders JARVIS keeps himself. Nothing when the phone has not sent any."""
    events, got = _events()
    if got is None or got["age_minutes"] > 24 * 60:
        return []
    now = _now()
    out = []
    for ev in events:
        s = _parse_dt(ev.get("start"))
        if s is None or s.date() != now.date():
            continue
        when = _when(ev, now, with_day=False)
        where = f" at {ev['location']}" if ev.get("location") else ""
        out.append((f"{ev['title']} {when}" if ev.get("all_day") else f"{ev['title']} at {when}",
                    f"{when} - {ev['title']}{where}"))
    return out[:5]


def setup_text(chat_id) -> str:
    """The pieces he needs on the phone, in one message: his chat id and the
    three payloads, ready to paste into a Shortcut."""
    return (
        f"Your chat id is {chat_id}.\n\n"
        "Each Shortcut posts JSON to https://api.telegram.org/bot<TOKEN>/sendMessage "
        "with chat_id and text (the JSON below as the text). No reply comes back for "
        "an automated payload; I only answer if I could not read it.\n\n"
        "Health (every hour, plus Sync):\n"
        '{"type":"health","metrics":{"heart_rate":62,"resting_heart_rate":55,"hrv":48,'
        '"steps":4120,"active_energy":310,"exercise_minutes":22,"sleep_hours":7.2,'
        '"blood_oxygen":97}}\n\n'
        "Calendar (next 7 days):\n"
        '{"type":"calendar","events":[{"title":"Dentist","start":"2026-09-16T15:00",'
        '"end":"2026-09-16T16:00","location":"Natick","all_day":false}]}\n\n'
        "Reminders (not completed):\n"
        '{"type":"reminders","items":[{"title":"Buy printer filament","due":"2026-09-16T18:00",'
        '"list":"Errands","priority":1}]}'
    )


async def sync_icloud() -> dict:
    """Pull the calendar and Reminders from iCloud now - his 'force it'."""
    import icloud
    return await icloud.sync()


def register_all() -> None:
    registry.register(Tool(
        name="sync_icloud",
        description="Refresh the user's calendar and Reminders from iCloud right now. "
                    "Use when he asks to sync, refresh or update his calendar or reminders.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=sync_icloud, timeout=45))
    registry.register(Tool(
        name="get_agenda",
        description="The user's calendar as pushed from his phone: today, tomorrow, this "
                    "week, or the next appointment. Always answered with how old the copy "
                    "is. Use for 'what do I have today', 'when is my next meeting'.",
        parameters={"type": "object", "properties": {
            "day": {"type": "string", "description": "today | tomorrow | week | next"}},
            "required": []},
        risk=Risk.SAFE, handler=get_agenda, timeout=10))
    registry.register(Tool(
        name="get_phone_reminders",
        description="The user's to-do items from the Reminders app on his phone (not the "
                    "reminders JARVIS sets - those are list_reminders). Optionally one "
                    "list by name, e.g. 'Groceries'.",
        parameters={"type": "object", "properties": {
            "list_name": {"type": "string", "description": "a list name, or empty for all"}},
            "required": []},
        risk=Risk.SAFE, handler=get_phone_reminders, timeout=10))
