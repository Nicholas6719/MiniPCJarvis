"""His iCloud calendar and Reminders, pulled from the PC over CalDAV.

The Shortcuts route (tools/phone.py) asked him to build three automations on
the phone, and he could not: "I can't do it." (2026-09-15). This needs nothing
on the phone. Apple exposes both the calendar (VEVENT) and the Reminders app
(VTODO) at caldav.icloud.com to an Apple ID with an app-specific password, so
JARVIS asks every quarter of an hour and writes what he gets into the same two
documents the phone would have sent - `get_agenda`, `get_phone_reminders` and
the morning brief do not know the difference.

The password is an APP-SPECIFIC one (account.apple.com -> Sign-In and Security),
revocable on its own, and it is kept the way the bot token is: DPAPI-encrypted
on disk, never in config.json, never in the log. `/icloud <email> <password>`
on Telegram stores it and deletes his message from the chat.

No iCalendar library is bundled, so the parser here is small and deliberate:
the server EXPANDS recurrences for us (RFC 4791 `expand`), which leaves DTSTART
/ DTEND / DUE / SUMMARY / LOCATION / STATUS / PRIORITY with their TZID, VALUE
and Z variants. Everything is converted to his clock.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from config import APP_DIR, config

log = logging.getLogger("jarvis.icloud")

CRED_PATH = APP_DIR / "icloud.bin"
BASE = "https://caldav.icloud.com"
DAV = "{DAV:}"
CAL = "{urn:ietf:params:xml:ns:caldav}"
TIMEOUT = 25.0


# ------------------------------------------------------------ credentials
def save_credentials(email: str, password: str) -> None:
    import win32crypt
    blob = win32crypt.CryptProtectData(
        json.dumps({"email": email.strip(), "password": password.strip()}).encode(),
        "jarvis-icloud", None, None, None, 0)
    CRED_PATH.write_bytes(blob)


def load_credentials() -> tuple[str, str] | None:
    try:
        if not CRED_PATH.exists():
            return None
        import win32crypt
        _, data = win32crypt.CryptUnprotectData(CRED_PATH.read_bytes(), None, None, None, 0)
        d = json.loads(data.decode())
        return str(d.get("email") or ""), str(d.get("password") or "")
    except Exception:
        log.exception("icloud credentials unreadable")
        return None


def forget_credentials() -> bool:
    try:
        if CRED_PATH.exists():
            CRED_PATH.unlink()
        return True
    except Exception:
        log.exception("could not remove the icloud credentials")
        return False


def connected() -> bool:
    return CRED_PATH.exists()


# --------------------------------------------------------- iCalendar, small
def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(s: str) -> str:
    return (s.replace("\\n", "\n").replace("\\N", "\n").replace("\\,", ",")
             .replace("\\;", ";").replace("\\\\", "\\")).strip()


def _prop(line: str) -> tuple[str, dict, str] | None:
    """'DTSTART;TZID=America/New_York:20260916T150000' -> (name, params, value)."""
    if ":" not in line:
        return None
    head, _, value = line.partition(":")
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name, params, value


def components(text: str, kind: str) -> list[dict]:
    """Every VEVENT (or VTODO) in an iCalendar text, as {name: (params, value)}."""
    out: list[dict] = []
    cur: dict | None = None
    depth = 0
    for line in _unfold(text):
        if line.upper() == f"BEGIN:{kind}":
            cur, depth = {}, 1
            continue
        if cur is None:
            continue
        if line.upper().startswith("BEGIN:"):
            depth += 1                      # VALARM inside: skip its lines
            continue
        if line.upper().startswith("END:"):
            depth -= 1
            if depth == 0:
                out.append(cur)
                cur = None
            continue
        if depth != 1:
            continue
        p = _prop(line)
        if p:
            name, params, value = p
            cur.setdefault(name, (params, value))
    return out


def to_local(value: str, params: dict) -> tuple[dt.datetime | None, bool]:
    """(local naive datetime, all_day). Handles DATE, floating, TZID and Z."""
    v = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or (len(v) == 8 and v.isdigit()):
            return dt.datetime.strptime(v, "%Y%m%d"), True
        if v.endswith("Z"):
            d = dt.datetime.strptime(v[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=dt.timezone.utc)
            return d.astimezone().replace(tzinfo=None), False
        d = dt.datetime.strptime(v[:15], "%Y%m%dT%H%M%S")
        tzid = params.get("TZID")
        if tzid:
            try:
                d = d.replace(tzinfo=ZoneInfo(tzid)).astimezone().replace(tzinfo=None)
            except Exception:
                pass                        # an unknown zone: treat as his clock
        return d, False
    except ValueError:
        return None, False


def _text(comp: dict, name: str) -> str:
    got = comp.get(name)
    return _unescape(got[1]) if got else ""


def event_dict(comp: dict, calendar: str = "") -> dict | None:
    """A VEVENT as the phone would have sent it (see tools/phone.py)."""
    st = comp.get("DTSTART")
    if not st:
        return None
    start, all_day = to_local(st[1], st[0])
    if start is None:
        return None
    end = None
    en = comp.get("DTEND")
    if en:
        end, _ = to_local(en[1], en[0])
    title = _text(comp, "SUMMARY") or "(untitled)"
    if _text(comp, "STATUS").upper() == "CANCELLED":
        return None
    return {"title": title, "start": start.isoformat(timespec="minutes"),
            "end": end.isoformat(timespec="minutes") if end else "",
            "location": _text(comp, "LOCATION"), "all_day": all_day, "calendar": calendar}


def todo_dict(comp: dict, list_name: str = "") -> dict | None:
    """A VTODO as the phone would have sent it. Completed ones are not his list."""
    if comp.get("COMPLETED") or _text(comp, "STATUS").upper() == "COMPLETED":
        return None
    title = _text(comp, "SUMMARY")
    if not title:
        return None
    due = ""
    d = comp.get("DUE")
    if d:
        when, all_day = to_local(d[1], d[0])
        if when:
            due = when.isoformat(timespec="minutes")
    pri = 0
    try:
        pri = int((comp.get("PRIORITY") or ({}, "0"))[1] or 0)
    except ValueError:
        pri = 0
    return {"title": title, "due": due, "list": list_name, "priority": max(0, min(9, pri)),
            "notes": _text(comp, "DESCRIPTION")[:200]}


# ----------------------------------------------------------------- CalDAV
def _ical_dt(d: dt.datetime) -> str:
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class Client:
    """The four CalDAV calls, over httpx with Basic auth. `request` is the one
    seam the tests replace."""

    def __init__(self, email: str, password: str) -> None:
        self.email, self.password = email, password
        self._http = None

    async def request(self, method: str, url: str, body: str = "", depth: str = "0") -> tuple[int, str]:
        import httpx
        if self._http is None:
            self._http = httpx.AsyncClient(auth=(self.email, self.password), timeout=TIMEOUT,
                                           follow_redirects=True,
                                           headers={"User-Agent": "JARVIS/1 (CalDAV)"})
        r = await self._http.request(method, url, content=body.encode("utf-8"),
                                     headers={"Depth": depth, "Content-Type": "application/xml; charset=utf-8"})
        return r.status_code, r.text

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @staticmethod
    def _abs(href: str, against: str) -> str:
        """A relative href is relative to the HOST THAT ANSWERED. iCloud puts the
        principal on caldav.icloud.com and the calendars on pNN-caldav.icloud.com,
        and every href after the move is relative to the new host."""
        return urljoin(against, href.strip())

    async def principal(self) -> str:
        code, xml = await self.request("PROPFIND", BASE + "/",
                                       '<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
                                       '<d:prop><d:current-user-principal/></d:prop></d:propfind>')
        if code == 401:
            raise PermissionError(
                "Apple refused that sign-in. Two things to check: the email must be the one you sign in "
                "to iCloud with, and the password must be a freshly generated app-specific one, typed "
                "with its dashes. Send /icloud again when you have them.")
        if code >= 400:
            raise RuntimeError(f"iCloud answered {code} to the principal lookup")
        href = ET.fromstring(xml).find(f".//{DAV}current-user-principal/{DAV}href")
        if href is None or not (href.text or "").strip():
            raise RuntimeError("iCloud did not say who you are")
        return self._abs(href.text.strip(), BASE + "/")

    async def home(self, principal: str) -> str:
        code, xml = await self.request("PROPFIND", principal,
                                       '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" '
                                       'xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop>'
                                       '<c:calendar-home-set/></d:prop></d:propfind>')
        if code >= 400:
            raise RuntimeError(f"iCloud answered {code} to the calendar-home lookup")
        href = ET.fromstring(xml).find(f".//{CAL}calendar-home-set/{DAV}href")
        if href is None:
            raise RuntimeError("iCloud did not say where your calendars are")
        return self._abs(href.text.strip(), principal)

    async def calendars(self, home: str) -> list[dict]:
        """[{url, name, kinds}] - kinds is {'VEVENT'} / {'VTODO'} / both."""
        code, xml = await self.request(
            "PROPFIND", home,
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/>'
            '</d:prop></d:propfind>', depth="1")
        if code >= 400:
            raise RuntimeError(f"iCloud answered {code} to the calendar list")
        out = []
        for resp in ET.fromstring(xml).findall(f"{DAV}response"):
            href = resp.find(f"{DAV}href")
            rtype = resp.find(f".//{DAV}resourcetype")
            if href is None or rtype is None or rtype.find(f"{CAL}calendar") is None:
                continue
            name_el = resp.find(f".//{DAV}displayname")
            kinds = {c.get("name", "").upper()
                     for c in resp.findall(f".//{CAL}supported-calendar-component-set/{CAL}comp")}
            out.append({"url": self._abs(href.text.strip(), home),
                        "name": (name_el.text or "").strip() if name_el is not None else "",
                        "kinds": kinds or {"VEVENT"}})
        return out

    async def events(self, cal_url: str, start: dt.datetime, end: dt.datetime) -> str:
        s, e = _ical_dt(start), _ical_dt(end)
        body = (
            '<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            f'<d:prop><c:calendar-data><c:expand start="{s}" end="{e}"/></c:calendar-data></d:prop>'
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">'
            f'<c:time-range start="{s}" end="{e}"/></c:comp-filter></c:comp-filter></c:filter>'
            '</c:calendar-query>')
        code, xml = await self.request("REPORT", cal_url, body, depth="1")
        if code >= 400:
            raise RuntimeError(f"iCloud answered {code} to the events query")
        return _calendar_data(xml)

    async def todos(self, cal_url: str) -> str:
        body = (
            '<?xml version="1.0"?><c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
            '<d:prop><c:calendar-data/></d:prop>'
            '<c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VTODO">'
            '<c:prop-filter name="COMPLETED"><c:is-not-defined/></c:prop-filter>'
            '</c:comp-filter></c:comp-filter></c:filter></c:calendar-query>')
        code, xml = await self.request("REPORT", cal_url, body, depth="1")
        if code >= 400:
            raise RuntimeError(f"iCloud answered {code} to the reminders query")
        return _calendar_data(xml)


def _calendar_data(xml: str) -> str:
    """Every calendar-data blob in a multistatus, joined."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return ""
    return "\n".join((el.text or "") for el in root.iter(f"{CAL}calendar-data"))


# -------------------------------------------------------------------- sync
_state = {"last_ok": 0.0, "last_error": "", "last_error_at": 0.0, "calendars": None,
          "events": 0, "reminders": 0}


def _window_days() -> int:
    return int(config.get("phone", "icloud_days_ahead", default=7))


async def sync(client: Client | None = None) -> dict:
    """Pull the week's events and the open reminders into the phone documents.
    Never raises; the loop and the tool both read the dict."""
    from tools import phone
    import volatile

    creds = load_credentials()
    own = False
    if client is None:
        if not creds:
            return {"error": "iCloud is not connected. Send /icloud <email> <app-specific password> on Telegram.",
                    "connected": False}
        client = Client(*creds)
        own = True                      # made here, closed here; one handed in is the caller's
    try:
        if _state["calendars"] is None:
            principal = await client.principal()
            home = await client.home(principal)
            _state["calendars"] = await client.calendars(home)
        cals = _state["calendars"]
        now = dt.datetime.now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + dt.timedelta(days=_window_days() + 1)
        events: list[dict] = []
        todos: list[dict] = []
        for cal in cals:
            if "VEVENT" in cal["kinds"]:
                for comp in components(await client.events(cal["url"], start, end), "VEVENT"):
                    ev = event_dict(comp, cal["name"])
                    if ev:
                        events.append(ev)
            if "VTODO" in cal["kinds"]:
                for comp in components(await client.todos(cal["url"]), "VTODO"):
                    td = todo_dict(comp, cal["name"])
                    if td:
                        todos.append(td)
        # de-duplicate what the expand can hand back twice (a master and its
        # instance on the same day), then the same shape the phone sends
        seen = set()
        uniq = []
        for ev in sorted(events, key=lambda x: x["start"]):
            k = (ev["title"], ev["start"])
            if k not in seen:
                seen.add(k)
                uniq.append(ev)
        todos.sort(key=lambda x: (x["due"] == "", x["due"], -x["priority"]))
        volatile.put(phone.KEY_CAL, {"events": uniq[:phone.MAX_EVENTS], "count": len(uniq)}, source="icloud")
        volatile.put(phone.KEY_REM, {"items": todos[:phone.MAX_REMINDERS], "count": len(todos)}, source="icloud")
        _state.update({"last_ok": time.time(), "last_error": "", "events": len(uniq), "reminders": len(todos)})
        log.info("icloud: %d events in the next %d days, %d open reminders, from %d calendars",
                 len(uniq), _window_days(), len(todos), len(cals))
        return {"ok": True, "events": len(uniq), "reminders": len(todos), "calendars": len(cals)}
    except PermissionError as e:
        _state.update({"last_error": str(e), "last_error_at": time.time(), "calendars": None})
        return {"error": str(e), "connected": True}
    except Exception as e:
        _state.update({"last_error": f"{type(e).__name__}: {e}", "last_error_at": time.time(),
                       "calendars": None})
        log.warning("icloud sync failed: %s", e)
        return {"error": f"I couldn't reach iCloud: {e}", "connected": True}
    finally:
        if own:
            await client.close()


async def sync_loop() -> None:
    """Every quarter of an hour, while credentials exist. Quiet when they do not."""
    await asyncio.sleep(45)                 # after the boot, not during it
    while True:
        minutes = float(config.get("phone", "icloud_sync_minutes", default=15))
        try:
            if connected():
                await sync()
        except Exception:
            log.exception("icloud sync loop")
        await asyncio.sleep(max(60.0, minutes * 60))


def status() -> dict:
    return {"connected": connected(), "last_ok": _state["last_ok"], "last_error": _state["last_error"],
            "events": _state["events"], "reminders": _state["reminders"]}
