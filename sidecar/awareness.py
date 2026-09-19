"""Situational awareness: notice change, hold it, speak only when it matters.

The film JARVIS knows what is on the bench without being asked. His senses
were already here (the front window, the downloads folder, the disk, the
phone's calendar, and now the phone's battery); what was missing was a
watcher that runs every ninety seconds, notices CHANGE, keeps a short note,
and decides whether the note is worth his attention (2026-09-18).

Three tiers, decided per note:
  CUT_IN      interrupts, once, in one sentence          (delivery URGENT)
  NEXT_PAUSE  waits for a silence, then one sentence     (delivery ALERT, when idle)
  HOLD        context only: the turn note and the brief  (nothing spoken)

Every spoken note goes through delivery.py, so it inherits presence (away =
phone, or dropped for phone-only notes), the dedup and the hourly ceiling
from the 2,600-message night. The research is blunt: proactive assistants
that speak more than a few times a day get switched off. Holding is the
default; speaking is the exception.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time
from collections import deque

from config import config

log = logging.getLogger("jarvis.awareness")

CUT_IN, NEXT_PAUSE, HOLD = "cut_in", "next_pause", "hold"
EVERY = 90.0
LONG_LOOK_S = 20 * 60          # a window in front this long is "what he is doing"
CALENDAR_WARN_MIN = 10          # "in ten minutes, sir"
LOW_BATTERY = 15                # spoken once, at the desk only
DEAD_BATTERY = 5                # cut in, once
LOW_DISK_GB = 10
_IGNORED_EXES = {"jarvis.exe", "explorer.exe", "searchhost.exe", "lockapp.exe", ""}


class Note:
    __slots__ = ("ts", "kind", "text", "tier", "key")

    def __init__(self, kind: str, text: str, tier: str, key: str) -> None:
        self.ts, self.kind, self.text, self.tier, self.key = time.time(), kind, text, tier, key


class Awareness:
    def __init__(self) -> None:
        self.notes: deque[Note] = deque(maxlen=40)
        self.front: tuple[str, str] | None = None     # (title, exe)
        self.front_since = 0.0
        self.front_noted = False
        self.seen_downloads: set[str] = set()
        self.first_scan = True
        self.said: dict[str, float] = {}               # key -> when
        self.speak = None                              # async (text, tier, key) -> None; set by main
        self.calendar_warned: set[str] = set()

    # ---------------------------------------------------------------- senses
    def _front_window(self) -> Note | None:
        try:
            from tools.windows_tools import foreground_app
            fa = foreground_app() or {}
        except Exception:
            return None
        title, exe = (fa.get("title") or "").strip(), (fa.get("exe") or "").lower()
        if not title or exe in _IGNORED_EXES or title == "JARVIS":
            return None
        cur = (title, exe)
        if cur != self.front:
            self.front, self.front_since, self.front_noted = cur, time.time(), False
            return None
        if not self.front_noted and time.time() - self.front_since >= LONG_LOOK_S:
            self.front_noted = True
            mins = int((time.time() - self.front_since) / 60)
            return Note("screen", f"on his screen for {mins} minutes: {title[:80]}", HOLD, f"screen:{title[:40]}")
        return None

    def _downloads(self) -> list[Note]:
        out: list[Note] = []
        try:
            folder = str(config.get("folders", "downloads", default="") or "")
            if not folder or not os.path.isdir(folder):
                return out
            now = time.time()
            for name in os.listdir(folder):
                p = os.path.join(folder, name)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if not os.path.isfile(p) or name.endswith((".crdownload", ".part", ".tmp")):
                    continue
                if name in self.seen_downloads:
                    continue
                self.seen_downloads.add(name)
                if self.first_scan or now - st.st_mtime > 600:
                    continue
                size_mb = st.st_size / 1e6
                out.append(Note("download", f"a download landed: {name[:60]} ({size_mb:.0f} MB)", HOLD, f"dl:{name[:40]}"))
        except Exception:
            log.debug("downloads scan failed", exc_info=True)
        finally:
            self.first_scan = False
        return out

    def _disk(self) -> Note | None:
        try:
            import shutil
            free_gb = shutil.disk_usage("C:\\").free / 1e9
            if free_gb < LOW_DISK_GB:
                return Note("disk", f"the C drive is down to {free_gb:.0f} gigabytes free", NEXT_PAUSE, "disk:low")
        except Exception:
            pass
        return None

    def _phone(self) -> list[Note]:
        out: list[Note] = []
        try:
            from tools import phone
            st = phone.status()
        except Exception:
            return out
        if not st:
            return out
        batt, charging = st.get("battery"), st.get("charging")
        if isinstance(batt, int) and not charging:
            if batt <= DEAD_BATTERY:
                out.append(Note("phone", f"your phone is about to die, sir - {batt} percent and unplugged", CUT_IN, "phone:dead"))
            elif batt <= LOW_BATTERY:
                out.append(Note("phone", f"your phone is down to {batt} percent, sir, and it isn't charging", NEXT_PAUSE, "phone:low"))
        place = st.get("place")
        if place:
            out.append(Note("phone", f"phone: {place}", HOLD, f"phone:place:{place[:30]}"))
        return out

    def _calendar(self) -> list[Note]:
        out: list[Note] = []
        try:
            from tools import phone
            for ev in phone.upcoming(minutes=CALENDAR_WARN_MIN + 2):
                key = f"cal:{ev.get('start')}:{ev.get('title', '')[:30]}"
                if key in self.calendar_warned:
                    continue
                mins = ev.get("minutes")
                if mins is None or mins > CALENDAR_WARN_MIN:
                    continue
                self.calendar_warned.add(key)
                when = "now" if mins <= 0 else f"in {int(mins)} minute{'s' if int(mins) != 1 else ''}"
                where = f", {ev['location']}" if ev.get("location") else ""
                out.append(Note("calendar", f"{ev.get('title', 'your next event')} {when}{where}, sir", NEXT_PAUSE, key))
        except Exception:
            log.debug("calendar look failed", exc_info=True)
        return out

    # ---------------------------------------------------------------- loop
    def observe(self) -> list[Note]:
        """One pass over the senses. Pure: nothing spoken here."""
        new: list[Note] = []
        n = self._front_window()
        if n:
            new.append(n)
        new += self._downloads()
        d = self._disk()
        if d:
            new.append(d)
        new += self._phone()
        new += self._calendar()
        for n in new:
            self.notes.append(n)
        return new

    def _worth_saying(self, n: Note) -> bool:
        if n.tier == HOLD:
            return False
        last = self.said.get(n.key, 0.0)
        cool = 6 * 3600 if n.kind in ("disk",) else 3600
        if time.time() - last < cool:
            return False
        return True

    async def _say(self, n: Note) -> None:
        self.said[n.key] = time.time()
        if self.speak is None:
            return
        try:
            await self.speak(n.text[0].upper() + n.text[1:] + ("" if n.text.endswith((".", "!", "?")) else "."),
                             n.tier, n.key)
        except Exception:
            log.debug("could not speak a note", exc_info=True)

    async def tick(self) -> None:
        for n in await asyncio.to_thread(self.observe):
            log.info("noticed [%s]: %s", n.tier, n.text)
            if self._worth_saying(n):
                await self._say(n)

    async def loop(self) -> None:
        if not config.get("awareness", "enabled", default=True):
            return
        await asyncio.sleep(120)
        while True:
            try:
                await self.tick()
            except Exception:
                log.debug("awareness tick failed", exc_info=True)
            await asyncio.sleep(float(config.get("awareness", "every_s", default=EVERY)))

    # ---------------------------------------------------------------- context
    def context_line(self) -> str:
        """For the turn note: what he is doing and what has happened lately."""
        bits: list[str] = []
        if self.front and time.time() - self.front_since >= 5 * 60:
            mins = int((time.time() - self.front_since) / 60)
            bits.append(f"He has had \"{self.front[0][:60]}\" in front for {mins} minutes.")
        recent = [n for n in self.notes if time.time() - n.ts < 3600 and n.kind not in ("screen", "phone")]
        for n in recent[-3:]:
            bits.append(n.text[0].upper() + n.text[1:] + ".")
        try:
            from tools import phone
            st = phone.status()
            if st and isinstance(st.get("battery"), int):
                bits.append(f"His phone: {st['battery']}%{' charging' if st.get('charging') else ''}"
                            + (f", {st['place']}" if st.get("place") else "") + ".")
        except Exception:
            pass
        return ("\nAround him: " + " ".join(bits)) if bits else ""


awareness = Awareness()


async def speak_via_delivery(text: str, tier: str, key: str) -> None:
    """CUT_IN interrupts now; NEXT_PAUSE waits for a silence. Phone-battery
    notes are for the desk only: away, he is holding the phone."""
    from delivery import ALERT, URGENT, delivery, is_present
    if key.startswith("phone:") and not is_present():
        return
    if tier == CUT_IN:
        await delivery.deliver(text, URGENT, key=key, subject="awareness")
        return
    orch = delivery.orchestrator
    if orch is not None:
        from state_machine import State
        for _ in range(240):                     # up to two minutes for a pause
            if orch.sm.state in (State.IDLE, State.SLEEPING):
                break
            await asyncio.sleep(0.5)
    await delivery.deliver(text, ALERT, key=key, subject="awareness")
