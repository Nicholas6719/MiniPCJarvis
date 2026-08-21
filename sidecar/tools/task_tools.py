"""Reminder / scheduled-task tools."""
from __future__ import annotations

import datetime as dt

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
    tid = scheduler.add(text, due.timestamp(), recurrence)
    return {"id": tid, "text": text,
            "due": due.strftime("%A %H:%M"), "recurrence": recurrence}


def list_reminders() -> dict:
    return {"reminders": scheduler.list_pending()}


def cancel_reminder(task_id: int) -> dict:
    ok = scheduler.cancel(int(task_id))
    return {"cancelled": task_id} if ok else {"error": f"no pending task {task_id}"}


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
