"""Reminder / scheduled-task tools."""
from __future__ import annotations

import datetime as dt
import re

from tasks.scheduler import scheduler
from tools.registry import Risk, Tool, registry


def set_reminder(text: str, minutes_from_now: int | None = None,
                 at_time: str | None = None, date: str | None = None,
                 recurrence: str = "none") -> dict:
    now = dt.datetime.now()
    if minutes_from_now is not None:
        due = now + dt.timedelta(minutes=max(1, int(minutes_from_now)))
    elif at_time:
        try:
            h, m = (int(x) for x in at_time.split(":")[:2])
        except ValueError:
            return {"error": f"bad at_time '{at_time}', expected HH:MM"}
        if date:
            try:
                y, mo, d = (int(x) for x in date.split("-"))
                due = dt.datetime(y, mo, d, h, m)
            except ValueError:
                return {"error": f"bad date '{date}', expected YYYY-MM-DD"}
        else:
            due = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if due <= now:
                due += dt.timedelta(days=1)
    else:
        return {"error": "need minutes_from_now or at_time"}
    if recurrence not in ("none", "daily", "weekdays", "weekly"):
        recurrence = "none"

    # Asking for the same thing again REPLACES it. Correcting a reminder ("not
    # just Sunday — every night") otherwise left the first one in place and he
    # ended up with two, one of them wrong, with no way to see either.
    replaced = 0
    key = _normalise(text)
    for t in scheduler.list_pending():
        if _normalise(t["text"]) == key:
            if scheduler.cancel(t["id"]):
                replaced += 1

    tid = scheduler.add(text, due.timestamp(), recurrence)
    out = {"id": tid, "text": text,
           "due": due.strftime("%A %H:%M"), "recurrence": recurrence,
           "spoken": _confirm(text, due, recurrence)}
    if replaced:
        out["replaced"] = replaced
    return out


def _normalise(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _confirm(text: str, due: dt.datetime, recurrence: str) -> str:
    """The sentence he hears, built from what was actually stored.

    He was once told "9:00 PM daily" for a reminder sitting at 3:46 PM. Being
    told the wrong time is worse than being set the wrong time, because there is
    nothing to notice.
    """
    when = due.strftime("%I:%M %p").lstrip("0")
    if recurrence == "daily":
        return f"Reminder set for {when}, every day."
    if recurrence == "weekdays":
        return f"Reminder set for {when}, every weekday."
    if recurrence == "weekly":
        return f"Reminder set for {when}, every {due.strftime('%A')}."
    today = dt.datetime.now().date()
    if due.date() == today:
        return f"Reminder set for {when} today."
    if due.date() == today + dt.timedelta(days=1):
        return f"Reminder set for {when} tomorrow."
    return f"Reminder set for {when} {due.strftime('%A')}."


def list_reminders() -> dict:
    return {"reminders": scheduler.list_pending()}


def cancel_reminder(task_id: int) -> dict:
    ok = scheduler.cancel(int(task_id))
    return {"cancelled": task_id} if ok else {"error": f"no pending task {task_id}"}


def cancel_reminders_matching(query: str = "") -> dict:
    """Cancel reminders by what they're ABOUT ("stop reminding me to stretch"), or
    all of them when query is empty. Nobody knows their reminders' id numbers —
    without this, "don't remind me to stretch anymore" had nowhere to go and fell
    to the model, which had no way to act on it either."""
    pending = scheduler.list_pending()
    if not pending:
        return {"cancelled": 0, "none_pending": True}
    words = [w for w in re.split(r"\s+", (query or "").lower().strip()) if len(w) > 2]
    if words:
        hits = [t for t in pending if all(w in t["text"].lower() for w in words)]
        if not hits:   # looser: any word matches
            hits = [t for t in pending if any(w in t["text"].lower() for w in words)]
    else:
        hits = pending
    for t in hits:
        scheduler.cancel(t["id"])
    return {"cancelled": len(hits), "texts": [t["text"] for t in hits][:5],
            "query": query, "remaining": len(pending) - len(hits)}


def register_all() -> None:
    registry.register(Tool(
        name="set_reminder",
        description="Set a reminder or recurring routine. Provide minutes_from_now "
                    "OR at_time (HH:MM 24h, optionally with date YYYY-MM-DD). "
                    "recurrence: none, daily, weekdays, or weekly.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "description": "What to remind about"},
            "minutes_from_now": {"type": "integer", "minimum": 1},
            "at_time": {"type": "string", "description": "HH:MM 24-hour"},
            "date": {"type": "string", "description": "YYYY-MM-DD (optional)"},
            "recurrence": {"type": "string",
                           "enum": ["none", "daily", "weekdays", "weekly"]}},
            "required": ["text"]},
        risk=Risk.LOW, handler=set_reminder))
    registry.register(Tool(
        name="list_reminders",
        description="List pending reminders and routines.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=list_reminders))
    registry.register(Tool(
        name="cancel_reminder",
        description="Cancel a pending reminder by its id.",
        parameters={"type": "object", "properties": {
            "task_id": {"type": "integer"}}, "required": ["task_id"]},
        risk=Risk.LOW, handler=cancel_reminder))
    registry.register(Tool(
        name="cancel_reminders_matching",
        description="Cancel reminders by what they are about — 'stop reminding me to "
                    "stretch', 'cancel my reminders' (empty query cancels all pending).",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "words from the reminder, or empty for all"}},
            "required": []},
        risk=Risk.LOW, handler=cancel_reminders_matching))
