"""His iCloud calendar and Reminders, pulled over CalDAV (2026-09-15).

Hermetic: the CalDAV client's one seam (`request`) is replaced with canned
multistatus answers shaped like iCloud's; no network, temp database.

Run: python tests/test_icloud.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "icloud.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


ICS_EVENTS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:20260916T150000
DTEND;TZID=America/New_York:20260916T160000
SUMMARY:Dentist\\, cleaning
LOCATION:Natick
BEGIN:VALARM
TRIGGER:-PT15M
SUMMARY:should not leak
END:VALARM
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20260919
DTEND;VALUE=DATE:20260920
SUMMARY:Holiday
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART:20260916T140000Z
SUMMARY:Standup
  (folded onto a second line)
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART:20260917T090000
SUMMARY:Cancelled thing
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""

ICS_TODOS = """BEGIN:VCALENDAR
BEGIN:VTODO
UID:t1
SUMMARY:Buy filament
DUE;TZID=America/New_York:20260916T180000
PRIORITY:1
END:VTODO
BEGIN:VTODO
UID:t2
SUMMARY:Done already
COMPLETED:20260914T120000Z
STATUS:COMPLETED
END:VTODO
BEGIN:VTODO
UID:t3
SUMMARY:Read chapter 3
DESCRIPTION:for Thursday
END:VTODO
END:VCALENDAR
"""


import icloud as I  # noqa: E402


def ms(*rows: str) -> str:
    return '<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">' + "".join(rows) + "</d:multistatus>"


class Fake(I.Client):
    """Answers like iCloud does, and records what was asked."""
    def __init__(self, fail_auth=False):
        super().__init__("him@icloud.com", "xxxx-xxxx-xxxx-xxxx")
        self.calls = []
        self.fail_auth = fail_auth
        self.closed = False

    async def request(self, method, url, body="", depth="0"):
        self.calls.append((method, url))
        if self.fail_auth:
            return 401, ""
        if method == "PROPFIND" and url.endswith("icloud.com/"):
            return 207, ms('<d:response><d:href>/</d:href><d:propstat><d:prop><d:current-user-principal>'
                           '<d:href>/123456/principal/</d:href></d:current-user-principal></d:prop></d:propstat></d:response>')
        if method == "PROPFIND" and url.endswith("/principal/"):
            return 207, ms('<d:response><d:href>/123456/principal/</d:href><d:propstat><d:prop>'
                           '<c:calendar-home-set><d:href>https://p01-caldav.icloud.com/123456/calendars/</d:href>'
                           '</c:calendar-home-set></d:prop></d:propstat></d:response>')
        if method == "PROPFIND" and url.endswith("/calendars/"):
            return 207, ms(
                '<d:response><d:href>/123456/calendars/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>',
                '<d:response><d:href>/123456/calendars/home/</d:href><d:propstat><d:prop><d:displayname>Home</d:displayname>'
                '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
                '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response>',
                '<d:response><d:href>/123456/calendars/tasks/</d:href><d:propstat><d:prop><d:displayname>Errands</d:displayname>'
                '<d:resourcetype><d:collection/><c:calendar/></d:resourcetype>'
                '<c:supported-calendar-component-set><c:comp name="VTODO"/></c:supported-calendar-component-set></d:prop></d:propstat></d:response>',
                '<d:response><d:href>/123456/calendars/inbox/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/><c:schedule-inbox/></d:resourcetype></d:prop></d:propstat></d:response>')
        if method == "REPORT" and url.endswith("/home/"):
            esc = ICS_EVENTS.replace("&", "&amp;").replace("<", "&lt;")
            return 207, ms(f'<d:response><d:href>/123456/calendars/home/1.ics</d:href><d:propstat><d:prop><c:calendar-data>{esc}</c:calendar-data></d:prop></d:propstat></d:response>')
        if method == "REPORT" and url.endswith("/tasks/"):
            esc = ICS_TODOS.replace("&", "&amp;").replace("<", "&lt;")
            return 207, ms(f'<d:response><d:href>/123456/calendars/tasks/t.ics</d:href><d:propstat><d:prop><c:calendar-data>{esc}</c:calendar-data></d:prop></d:propstat></d:response>')
        return 404, ""

    async def close(self):
        self.closed = True


def main() -> int:
    from tools import phone as P

    print("\n-- the small iCalendar parser --")
    evs = I.components(ICS_EVENTS, "VEVENT")
    check("four events, the alarm inside one of them not counted", len(evs) == 4, len(evs))
    d = I.event_dict(evs[0], "Home")
    check("a TZID time lands on his clock", d["start"] == "2026-09-16T15:00" and d["end"] == "2026-09-16T16:00", d)
    check("...with the escaped comma unescaped", d["title"] == "Dentist, cleaning", d["title"])
    check("...and the location", d["location"] == "Natick" and d["calendar"] == "Home")
    check("a DATE is an all-day event", I.event_dict(evs[1])["all_day"] is True and I.event_dict(evs[1])["start"] == "2026-09-19T00:00")
    z = I.event_dict(evs[2])
    check("a Z time is converted, and a folded line rejoined",
          z["title"] == "Standup (folded onto a second line)" and z["start"] != "2026-09-16T14:00", z)
    check("a cancelled event is not his day", I.event_dict(evs[3]) is None)
    tds = I.components(ICS_TODOS, "VTODO")
    got = [I.todo_dict(t, "Errands") for t in tds]
    check("a completed reminder is left out", [g["title"] for g in got if g] == ["Buy filament", "Read chapter 3"], got)
    check("...a due time lands on his clock with its priority", got[0]["due"] == "2026-09-16T18:00" and got[0]["priority"] == 1, got[0])
    check("...and one without a due date is fine", got[2]["due"] == "" and got[2]["notes"] == "for Thursday", got[2])

    print("\n-- the sync, end to end against a fake iCloud --")
    I._state["calendars"] = None
    fake = Fake()
    fixed = dt.datetime(2026, 9, 15, 9, 0)
    P._now = lambda: fixed
    res = asyncio.run(I.sync(client=fake))
    check("it discovers, lists, and queries only the calendars that hold each kind",
          res.get("ok") and res["calendars"] == 2 and
          [c for c in fake.calls if c[0] == "REPORT"] == [("REPORT", "https://p01-caldav.icloud.com/123456/calendars/home/"),
                                                          ("REPORT", "https://p01-caldav.icloud.com/123456/calendars/tasks/")],
          (res, fake.calls))
    check("three events survive (one cancelled)", res["events"] == 3, res)
    check("two reminders survive (one done)", res["reminders"] == 2, res)
    ag = asyncio.run(P.get_agenda("tomorrow"))
    check("...and the phone tools read them unchanged",
          [e["title"] for e in ag["events"]] == ["Standup (folded onto a second line)", "Dentist, cleaning"]
          or [e["title"] for e in ag["events"]] == ["Dentist, cleaning", "Standup (folded onto a second line)"], ag)
    rm = asyncio.run(P.get_phone_reminders("errands"))
    check("...reminders too, by list", [x["title"] for x in rm["reminders"]] == ["Buy filament", "Read chapter 3"], rm)
    check("the calendar list is remembered for the next sync", I._state["calendars"] is not None)
    check("a client handed in is not closed by the sync", fake.closed is False)

    print("\n-- what goes wrong is said plainly --")
    I._state["calendars"] = None
    bad = asyncio.run(I.sync(client=Fake(fail_auth=True)))
    check("a wrong password is named as such, with what to check", "Apple refused that sign-in" in str(bad.get("error", ""))
          and "app-specific" in str(bad.get("error", "")), bad)
    check("...and the calendar list is forgotten so the next try discovers again", I._state["calendars"] is None)
    real_load = I.load_credentials
    I.load_credentials = lambda: None
    try:
        none = asyncio.run(I.sync())
        check("no credentials: the sentence says how to connect", "/icloud" in str(none.get("error", "")), none)
    finally:
        I.load_credentials = real_load

    print("\n-- the wiring --")
    rt = (ROOT / "remote_telegram.py").read_text(encoding="utf-8")
    blk = rt[rt.index('startswith("/icloud")'):][:2400]
    check("/icloud stores the credentials off the loop", "to_thread(_ic.save_credentials" in blk)
    check("...deletes his message from the chat", '"deleteMessage"' in blk and "message_id=mid" in blk)
    check("...syncs at once and reports the counts", "await _ic.sync()" in blk and "Connected to iCloud" in blk)
    check("...and /icloud off forgets them", "forget_credentials()" in blk)
    check("the password never reaches config.json", "CRED_PATH" in (ROOT / "icloud.py").read_text(encoding="utf-8")
          and "CryptProtectData" in (ROOT / "icloud.py").read_text(encoding="utf-8"))
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    check("the loop is started with a held reference", "spawn(icloud.sync_loop())" in main_py)
    P.register_all()
    from tools.registry import Risk, registry
    t = registry.get("sync_icloud")
    check("the force-it tool is registered at SAFE", t is not None and t.risk is Risk.SAFE)
    from brain.skills import say_sync_phone
    check("the sync is spoken with its counts",
          say_sync_phone({}, {"ok": True, "events": 3, "reminders": 1}) == "Synced, sir: 3 events this week and 1 open reminder.")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
