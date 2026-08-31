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
import logging
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


def workstation_locked() -> bool:
    try:
        from tools.input_tools import _locked
        return bool(_locked())
    except Exception:
        return False


def is_present() -> bool:
    """Is he actually at the machine right now?

    Keyboard and mouse only — the camera idea is much later and this is enough.
    A locked workstation is away no matter how recent the last keystroke was.
    """
    if workstation_locked():
        return False
    away_after = float(config.get("presence", "away_after_seconds", default=180))
    return user_idle_seconds() < away_after


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
        self._last: dict[str, float] = {}

    def _too_soon(self, key: str, tier: str) -> bool:
        """Don't say the same thing twice in a row. Urgent is exempt — if it is
        worth waking him for, it is worth repeating."""
        if not key or tier == URGENT:
            return False
        gap = float(config.get("proactive", "repeat_cooldown_minutes", default=45)) * 60
        last = self._last.get(key, 0.0)
        if time.time() - last < gap:
            return True
        self._last[key] = time.time()
        return False

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
        if self._too_soon(key, tier):
            return {"delivered": "nothing", "why": "said recently", "tier": tier}

        present = is_present()
        # NOTABLE never interrupts and is not worth a message on its own: it
        # waits for the next brief. Everything else goes now.
        if tier == NOTABLE:
            await bus.emit("proactive_held", text=text, tier=tier, subject=subject)
            return {"delivered": "held for the next brief", "tier": tier}

        if present:
            spoke = await self._speak(text, interrupt=tier in (ALERT, URGENT))
            if spoke:
                _remember_proactive(text)
                await bus.emit("proactive", text=text, tier=tier, channel="voice",
                               subject=subject)
                return {"delivered": "spoken", "tier": tier}
            # he looked present but could not be spoken to (mid-turn, asleep,
            # audio gone) — the phone is better than losing it
        if telegram_available():
            from remote_telegram import telegram
            await telegram.send_proactive(written or text, tier=tier, subject=subject)
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
