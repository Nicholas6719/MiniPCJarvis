"""Where JARVIS reaches him, and whether it is worth interrupting for.

One decision, made in one place. Everything he says on his own initiative — a
brief, a market move, breaking news, a reminder — comes through here, and the
answer to "speak it or send it" is always the same question: **is he there?**

  at the PC   -> say it out loud
  away        -> Telegram
  no idea     -> Telegram, because a message waiting on a phone costs nothing and
                 a sentence spoken to an empty room is lost

Tiers decide whether it is worth interrupting for, not where it goes:

  BRIEF     the scheduled digest. Waits for a quiet moment; never interrupts.
  NOTABLE   worth knowing, not worth stopping for. Rolled into the next brief.
  ALERT     breaking news, a large move in something of his. Goes immediately,
            quiet hours included, and may interrupt him at the PC.
  URGENT    life-safety, or an extraordinary market event. Same as ALERT, plus
            it keeps asking until he acknowledges.

Explicitly agreed with Nicholas 2026-08-30: interrupting a spreadsheet to say
"there is breaking news" is welcome when it is urgent. That is what being
present means. What is never acceptable is waking a dark screen, or talking to a
room he left.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time

from config import config
from events import bus

log = logging.getLogger("jarvis.delivery")

BRIEF = "brief"
NOTABLE = "notable"
ALERT = "alert"
URGENT = "urgent"
_TIERS = (BRIEF, NOTABLE, ALERT, URGENT)


def user_idle_seconds() -> float:
    from proactive import user_idle_seconds as _idle
    try:
        return _idle()
    except Exception:
        return 1e9          # unknown means away, which routes to the phone


def _in_quiet_hours() -> bool:
    """His night, as proactive already defines it (default 22:00-08:00)."""
    try:
        from proactive import proactive
        return bool(proactive.in_quiet_hours())
    except Exception:
        return False        # unknown is treated as daytime: never silently mute him


def workstation_locked() -> bool:
    try:
        from tools.input_tools import _locked
        return bool(_locked())
    except Exception:
        return False


def _camera_sees_him() -> bool:
    """Does the webcam currently have a face in front of it? Never raises.

    Only ever consulted while the camera is already on — this does not open it,
    and with the camera off it is simply False and nothing changes.
    """
    try:
        from camera import camera
        if not camera.is_on:
            return False
        from vision_presence import presence
        return bool(presence.present)
    except Exception:
        return False


def is_present() -> bool:
    """Is he actually at the machine right now?

    Keyboard and mouse, plus the camera when it happens to be open.

    The camera is used in ONE DIRECTION only: it can say "he is here" when the
    idle clock had given up on him, and it can never say "he is not". Reading a
    long answer on screen without touching the mouse used to look identical to
    having left the room, and sent his own reply to his phone. A face in front
    of the lens settles that.

    It cannot vote the other way because it is not evidence of absence: he may
    be leaning out of frame, the room may be dark, the camera is usually off.
    And it is not authentication — a photograph would satisfy it, which is
    exactly why nothing dangerous hangs on it.
    """
    if workstation_locked():
        return False        # locked is away, whatever the camera sees
    away_after = float(config.get("presence", "away_after_seconds", default=180))
    if user_idle_seconds() < away_after:
        return True
    return _camera_sees_him()


def telegram_available() -> bool:
    return bool(config.get("remote", "telegram_chat_id", default=None))


def _remember_proactive(text: str) -> None:
    """Put what he was just told into the conversation, wherever it went.

    Without this a follow-up has nothing to attach to: "Any updates on that?"
    reached back past a Nepal alert to a weather question twenty minutes older,
    because only the weather had ever been part of the conversation.
    """
    if not (text or "").strip():
        return          # nothing was said, so there is nothing to remember
    try:
        from orchestrator import orchestrator
        orchestrator.note_proactive(text)
    except Exception:
        log.debug("could not record the proactive item", exc_info=True)


class Delivery:
    """Routes everything JARVIS says on his own initiative."""

    def __init__(self) -> None:
        self.orchestrator = None      # set at startup
        # key -> (when it was last sent, at what tier). The tier is kept so an
        # escalation on the same subject is never mistaken for a repeat.
        self._last: dict[str, tuple[float, str]] = {}
        # timestamps of everything actually sent, for the hourly ceiling
        self._sent: list[float] = []
        self._capped_at = 0.0

    def _key_for(self, key: str, text: str) -> str:
        """A cooldown key for every message, whether the caller supplied one or not.

        `if not key ... return False` used to mean "no key, no limit", which is
        exactly backwards: a caller that cannot name its message is the LEAST
        able to promise it will not repeat. `scheduler.announce` passes no key,
        so a stuck reminder had no rate limit whatsoever.
        """
        if key:
            return key
        norm = re.sub(r"\s+", " ", (text or "").lower()).strip()[:160]
        return "text:" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]

    def _too_soon(self, key: str, tier: str) -> bool:
        """Don't say the same thing twice in a row.

        URGENT still repeats — "it keeps asking until he acknowledges" is the
        point of the tier — but it is no longer EXEMPT. On 2026-08-31 a reminder
        whose schedule could not be written re-fired every ten seconds, and with
        no key and no cap that became about 2,600 Telegram messages overnight.
        Repeating every few minutes still gets his attention; repeating six
        times a minute is how he ends up shutting JARVIS off.
        """
        gap = float(config.get("proactive", "repeat_cooldown_minutes", default=45)) * 60
        if tier == URGENT:
            gap = float(config.get("proactive", "urgent_repeat_minutes", default=10)) * 60
        # Prune first. Before today the keys here were a handful of named ones
        # ("story-1", "market-NVDA"); now an unnamed caller gets a key derived
        # from its message text, so every distinct thing JARVIS ever says on his
        # own initiative would add an entry that never left. Anything older than
        # the longest window this function can enforce cannot affect a decision,
        # so it is simply gone.
        now = time.time()
        if len(self._last) > 512:
            cutoff = now - max(gap, 3600.0)
            self._last = {k: v for k, v in self._last.items() if v[0] >= cutoff}

        when, last_tier = self._last.get(key, (0.0, BRIEF))
        # An ESCALATION is not a repeat. A story he was told about as an alert
        # that has since become urgent must reach him now, not in ten minutes —
        # this is the difference between "the same message again" and "this got
        # worse". Only a message at the same tier or lower waits.
        escalating = _TIERS.index(tier) > _TIERS.index(last_tier)
        if not escalating and now - when < gap:
            return True
        self._last[key] = (now, tier)
        return False

    def note_sent(self) -> None:
        """Record a message that reached him by a route outside `deliver()`.

        The urgent chase in remote_telegram sends straight through the Telegram
        API, so until now its follow-ups were invisible to the ceiling below: a
        cap of 12 an hour really meant up to 48, because every alert could carry
        three chases behind it. They reach his phone, so they count.
        """
        self._sent.append(time.time())

    def has_budget(self, tier: str = ALERT) -> bool:
        """Whether another message may reach him right now. Never raises."""
        try:
            return not self._over_budget(tier)
        except Exception:
            return True         # unknown is treated as allowed: never mute him by accident

    def _over_budget(self, tier: str) -> bool:
        """The backstop: a hard ceiling on unprompted messages per hour.

        Every specific fix here addresses a specific bug. This one exists
        because there will be another bug. Whatever goes wrong upstream — a
        stuck scheduler, a retry loop, a feed that suddenly reports everything
        as an emergency — it cannot cost him more than this many messages before
        JARVIS stops and says so once.
        """
        now = time.time()
        self._sent = [t for t in self._sent if now - t < 3600.0]
        cap = int(config.get("proactive", "max_messages_per_hour", default=12))
        # At night the ceiling is much lower. The 2,600-message flood ran from
        # 23:59 to gone 07:00 — entirely inside quiet hours — and every one of
        # those messages arrived on a phone next to a sleeping man. An URGENT
        # still gets through; the tiers are unchanged. There is simply far less
        # room for a mistake to spend while he is asleep.
        if _in_quiet_hours():
            cap = min(cap, int(config.get("proactive", "quiet_max_messages_per_hour",
                                          default=3)))
        if len(self._sent) < cap:
            return False
        if now - self._capped_at > 3600.0:
            self._capped_at = now
            log.error("proactive budget spent: %d messages in the last hour "
                      "(cap %d) — holding everything else back", len(self._sent), cap)
        return True

    async def deliver(self, text: str, tier: str = NOTABLE, *, key: str = "",
                      subject: str = "", written: str = "") -> dict:
        """Say it, send it, or hold it. Returns what was actually done.

        `written` is the same message shaped for a screen. Speech wants flowing
        sentences; a phone wants short lines you can scan. He was sent a 300-word
        spoken paragraph and told us so: "way too cluttered and not easy to read".
        When a caller supplies both, the ear gets `text` and the eye gets
        `written`; when it does not, nothing changes.
        """
        text = (text or "").strip()
        written = (written or "").strip()
        if not text:
            return {"delivered": "nothing", "why": "empty"}
        if tier not in _TIERS:
            tier = NOTABLE
        if self._too_soon(self._key_for(key, text), tier):
            return {"delivered": "nothing", "why": "said recently", "tier": tier}
        # NOTABLE is only ever held for the brief, so it costs him nothing and
        # is not charged against the budget; anything that actually reaches him
        # is. Checked before we decide speech vs phone, so neither route leaks.
        if tier != NOTABLE and self._over_budget(tier):
            await bus.emit("proactive_held", text=text, tier=tier, subject=subject)
            return {"delivered": "nothing", "why": "hourly message budget spent",
                    "tier": tier}

        present = is_present()
        # NOTABLE never interrupts and is not worth a message on its own: it
        # waits for the next brief. Everything else goes now.
        if tier == NOTABLE:
            await bus.emit("proactive_held", text=text, tier=tier, subject=subject)
            return {"delivered": "held for the next brief", "tier": tier}

        if present:
            spoke = await self._speak(text, interrupt=tier in (ALERT, URGENT))
            if spoke:
                self._sent.append(time.time())
                _remember_proactive(text)
                await bus.emit("proactive", text=text, tier=tier, channel="voice",
                               subject=subject)
                return {"delivered": "spoken", "tier": tier}
            # he looked present but could not be spoken to (mid-turn, asleep,
            # audio gone) — the phone is better than losing it
        if telegram_available():
            from remote_telegram import telegram
            await telegram.send_proactive(written or text, tier=tier, subject=subject)
            self._sent.append(time.time())
            _remember_proactive(text)
            await bus.emit("proactive", text=text, tier=tier, channel="telegram",
                           subject=subject)
            return {"delivered": "telegram", "tier": tier,
                    "why": "he is not at the machine" if not present else "could not speak"}

        # nowhere to send it: keep it for the next time he is here
        await bus.emit("proactive_held", text=text, tier=tier, subject=subject)
        return {"delivered": "held", "why": "not present and no Telegram", "tier": tier}

    async def _speak(self, text: str, interrupt: bool) -> bool:
        """Say it at the PC. False if he could not be spoken to."""
        orch = self.orchestrator
        if orch is None:
            return False
        from state_machine import State
        state = orch.sm.state
        if state is State.SLEEPING:
            # asleep means he put himself away; do not start talking to a
            # minimised window in a room he may have left
            return False
        if state is not State.IDLE:
            if not interrupt:
                return False
            try:
                await orch.interrupt()
            except Exception:
                log.debug("could not interrupt for an alert", exc_info=True)
            for _ in range(40):                    # up to 4 s for the turn to yield
                if orch.sm.state in (State.IDLE, State.INTERRUPTED):
                    break
                await asyncio.sleep(0.1)
            if orch.sm.state not in (State.IDLE, State.INTERRUPTED):
                return False
        try:
            await orch.announce(text)
            return True
        except Exception:
            log.exception("could not speak a proactive message")
            return False


delivery = Delivery()
