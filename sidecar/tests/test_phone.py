"""His phone: calendar and Reminders pushed over Telegram, and the watch
answered as a reflex (2026-09-15).

Hermetic: a temp database, no network, no app. The routing block loads the
brain the way test_brain does.

Run: python tests/test_phone.py
"""
import asyncio
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "phone.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from tools import phone as P
    fixed = dt.datetime(2026, 9, 15, 9, 0)
    P._now = lambda: fixed

    print("\n-- what is telemetry and what is speech --")
    check("a calendar document is recognised",
          P.kind_of('{"type":"calendar","events":[]}') == "calendar")
    check("...by its shape when it has no type", P.kind_of('{"events":[{"title":"x"}]}') == "calendar")
    check("a reminders document is recognised",
          P.kind_of('{"type":"reminders","items":[]}') == "reminders")
    check("a health document is NOT ours", P.kind_of('{"type":"health","metrics":{"steps":3}}') is None)
    check("a sentence is speech", P.kind_of("what's on my calendar today") is None)
    check("a JSON that is not an object is speech", P.kind_of("[1,2,3]") is None)
    check("an oversized document is refused before it is parsed",
          P.kind_of("{" + '"a":' + '"' + "x" * 70000 + '"}') is None)

    print("\n-- the calendar, ingested --")
    doc = {"type": "calendar", "events": [
        {"title": "Dentist", "start": "2026-09-15T15:00", "end": "2026-09-15T16:00", "location": "Natick"},
        {"title": "Standup", "start": "2026-09-15T10:00"},
        {"title": "Sync", "start": "2026-09-15T14:30:00Z"},           # a UTC stamp, into his clock
        {"title": "Lab", "start": "9/16/2026, 1:30 PM"},
        {"title": "Holiday", "start": "2026-09-19T00:00", "all_day": True},
        {"title": "", "start": "2026-09-15T12:00"},                       # no title: dropped
        {"title": "Bad", "start": "not a date"},                             # no time: dropped
        {"title": "x" * 500, "start": "2026-09-17T09:00", "evil": {"a": 1}},  # capped, unknown key ignored
        "not a dict",
    ]}
    r = P.ingest_payload(json.dumps(doc))
    check("the good events are stored and the bad ones counted",
          r.get("type") == "calendar" and r.get("stored") == 6 and r.get("ignored") == 3, r)
    ag = asyncio.run(P.get_agenda("today"))
    check("today's events, in order", [e["title"] for e in ag["events"]] == ["Standup", "Sync", "Dentist"],
          [e["title"] for e in ag["events"]])
    check("...spoken as clock times", ag["events"][0]["when"] == "10 AM" and ag["events"][2]["when"] == "3 PM",
          [e["when"] for e in ag["events"]])
    sync = ag["events"][1]
    check("...a UTC time is turned into his clock, not read as its digits",
          sync["when"] != "2:30 PM" and sync["when"].endswith("M"), sync["when"])
    check("...and the copy says how old it is", "as_of" in ag and ag["stale"] is False, ag.get("as_of"))
    tm = asyncio.run(P.get_agenda("tomorrow"))
    check("tomorrow, from the Shortcuts default date format",
          [e["title"] for e in tm["events"]] == ["Lab"] and tm["events"][0]["when"] == "1:30 PM", tm)
    wk = asyncio.run(P.get_agenda("week"))
    check("the week carries the day with each time",
          any(e["title"] == "Holiday" and e["when"] == "all day Saturday" for e in wk["events"]),
          [(e["title"], e["when"]) for e in wk["events"]])
    check("a long title is capped", max(len(e["title"]) for e in wk["events"]) <= 120)
    nx = asyncio.run(P.get_agenda("next"))
    check("the next appointment is the first one still ahead",
          nx["events"] and nx["events"][0]["title"] == "Standup", nx)
    check("today's lines for the brief",
          P.today_lines()[0] == ("Standup at 10 AM", "10 AM - Standup") if P.today_lines() else False,
          P.today_lines())

    print("\n-- the reminders, ingested --")
    doc = {"type": "reminders", "items": [
        {"title": "Buy filament", "due": "2026-09-16T18:00", "list": "Errands", "priority": 1},
        {"title": "Call the dentist", "due": "2026-09-14T09:00", "list": "Errands"},
        {"title": "Done already", "due": "2026-09-15T09:00", "completed": True},
        {"title": "Read chapter 3", "list": "School"},
        {"title": "Milk", "list": "Groceries"},
    ]}
    r = P.ingest_payload(json.dumps(doc))
    check("open items are stored, completed ones are not", r.get("stored") == 4 and r.get("ignored") == 1, r)
    rm = asyncio.run(P.get_phone_reminders())
    check("dated items first, oldest due first",
          [x["title"] for x in rm["reminders"]][:2] == ["Call the dentist", "Buy filament"],
          [x["title"] for x in rm["reminders"]])
    check("an overdue item says so", rm["reminders"][0]["overdue"] is True)
    check("...and is dated in words", rm["reminders"][1]["when"] == "tomorrow at 6 PM", rm["reminders"][1]["when"])
    gr = asyncio.run(P.get_phone_reminders("groceries"))
    check("one list by name", [x["title"] for x in gr["reminders"]] == ["Milk"], gr)

    print("\n-- nothing here can raise into the poller --")
    for bad in ("{", '{"type":"calendar","events":"nope"}', '{"type":"calendar"}', "null", "x" * 70000):
        out = P.ingest_payload(bad)
        check(f"{bad[:24]!r} is answered, not thrown", isinstance(out, dict) and "stored" in out, out)

    print("\n-- the poller: a Shortcut's payload gets no receipt --")
    rt = (ROOT / "remote_telegram.py").read_text(encoding="utf-8")
    blk = rt[rt.index("kind = _phone_payload(text) if text else None"):][:1800]
    check("calendar and reminders take the same road as health", "_phone.ingest_payload(text)" in blk)
    check("an automated payload is stored in silence", "elif _automated(text):" in blk and "pass" in blk)
    check("...but an unreadable one is still answered",
          blk.index("if res.get(\"error\"):") < blk.index("elif _automated(text):"))
    check("/setup phone hands him his chat id and the templates",
          "/setup phone" in rt and "setup_text(chat_id)" in rt)
    st = P.setup_text(123456789)
    check("...with the id, the endpoint and all three shapes",
          "123456789" in st and "sendMessage" in st and all(k in st for k in ('"health"', '"calendar"', '"reminders"')))

    print("\n-- the tools are registered and shaped for the model --")
    P.register_all()
    from tools.registry import Risk, registry
    for name in ("get_agenda", "get_phone_reminders"):
        t = registry.get(name)
        check(f"{name} is registered at SAFE", t is not None and t.risk is Risk.SAFE)
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    check("...and main.py registers the module", "phone.register_all()" in main_py)
    br = (ROOT / "briefing.py").read_text(encoding="utf-8")
    check("the morning brief carries today's calendar", "_phone.today_lines()" in br)

    print("\n-- the questions route as reflexes --")
    from brain.skills import say_agenda, say_health, say_phone_reminders, slots_agenda, slots_health
    check("a heart-rate question picks the metric", slots_health("how's my heart rate") == {"metric": "heart_rate"})
    check("a sleep question picks sleep", slots_health("how did i sleep") == {"metric": "sleep_hours"})
    check("a general watch question asks for everything", slots_health("what does my watch say") == {})
    check("tomorrow is tomorrow", slots_agenda("what do i have tomorrow") == {"day": "tomorrow"})
    check("the next meeting is next", slots_agenda("when's my next meeting") == {"day": "next"})
    check("a heart rate is spoken with its age",
          say_health({"metric": "heart_rate"}, {"metrics": [{"metric": "heart_rate", "spoken": "heart rate",
                     "value": 62.0, "unit": "bpm", "as_of": "4 minutes ago", "stale": False}]})
          == "Your heart rate is 62 bpm, as of 4 minutes ago.",
          say_health({"metric": "heart_rate"}, {"metrics": [{"metric": "heart_rate", "spoken": "heart rate",
                     "value": 62.0, "unit": "bpm", "as_of": "4 minutes ago", "stale": False}]}))
    check("an empty day is a sentence", say_agenda({"day": "today"}, {"day": "today", "events": []})
          == "Nothing on your calendar today, sir.")
    check("two events are listed", say_agenda({}, {"day": "today", "events": [
        {"title": "Standup", "when": "10 AM"}, {"title": "Dentist", "when": "3 PM"}]})
          == "2 things today: Standup at 10 AM, Dentist at 3 PM.")
    check("the to-do list is spoken", say_phone_reminders({}, {"list": "", "reminders": [{"title": "Milk", "when": ""}]})
          == "One thing on your list: Milk.")

    from brain.router import brain

    async def route():
        await brain.load()
        got = {}
        for text in ("how's my heart rate", "how many steps have i taken today",
                     "what's on my calendar today", "when's my next meeting",
                     "what do i have tomorrow", "what's on my to do list",
                     "what reminders do i have", "set a reminder for 7 pm to take my supplements"):
            d = await brain.decide(text, context={})
            got[text] = d[0].name if d else None
        return got
    got = asyncio.run(route())
    for text, want in (("how's my heart rate", "health"), ("how many steps have i taken today", "health"),
                       ("what's on my calendar today", "agenda"), ("when's my next meeting", "agenda"),
                       ("what do i have tomorrow", "agenda"), ("what's on my to do list", "phone_reminders"),
                       ("what reminders do i have", "reminders"),
                       ("set a reminder for 7 pm to take my supplements", "reminder")):
        check(f"{text!r} -> {want}", got.get(text) == want, got.get(text))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
