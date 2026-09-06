"""The JARVIS turn engine: listen → transcribe → think (with tools) → speak.

Owns the voice loop task. Supports push-to-talk toggle, VAD end-of-speech,
barge-in interruption, and stop-words.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
import uuid

import numpy as np

from audio.io import mic, speaker, MIC_RATE, SpeakerStalled
from audio.stt import stt
from audio.tts import tts
from audio.vad import StreamingVAD
from audio import endpoint, output_watch
from audio.wake import wake
from audio.sounds import PALETTE
from audio.speech_text import clean_for_speech, strip_markdown
import clarify
from brain.router import brain
from lastseen import last_seen
from linkguard import (LinkLedger, check as link_check,
                       check_captions, explain as link_explain, price_caveat,
                       supply as link_supply, wanted_links)
from brain.facts import facts
import collections
import re as _re
from config import config
from events import bus, spawn
from llm.llama_server import llama
from brain.skills import want_honorific
from llm.prompts import pinned_block, system_prompt, turn_context
from llm.provider import local_llm
from memory.store import memory
from state_machine import State, StateMachine
from tools.registry import registry
from tools.shortlist import shortlist

log = logging.getLogger("jarvis.orchestrator")


def _display_off() -> bool:
    """Is the screen dark? Never raises - unknown is treated as lit."""
    try:
        from tools.windows_tools import display_is_off
        return bool(display_is_off())
    except Exception:
        return False

# What the tools actually returned this turn. Nothing else may be linked.
link_ledger = LinkLedger()

WAKE_PHRASE = _re.compile(r"^\s*(?:hey|hi|ok|okay|yo)?[,\s]*(?:jarvis|jarves|jarvus|jovis|jervis|javis|jarvi)[,.!?\s]*", _re.I)
# explicit requests to go online: the model must not answer from memory
SEARCH_INTENT = re.compile(
    r"\b(search|look\s*up|google|research|find\s+(?:me\s+)?(?:online|on the web)|"
    r"what'?s the latest|latest|current|today'?s|right now|news|price of)\b", re.I)
# Confirmations may be answered out loud. Deliberately strict: only a short, bare
# affirmation counts, so speech from a video or a passing sentence can never approve
# a risk-gated action.
# Asymmetric on purpose: be LIBERAL about what cancels and CONSERVATIVE about what
# approves. Parakeet v3 is multilingual and drifts on ultra-short utterances (English
# "No." is transcribed "Não"), so the cancel list carries the common variants; the
# approve list stays English-only so a mis-heard word can never green-light a restart.
YES_WORDS = re.compile(r"^\s*(?:yes|yes please|yeah|yep|yup|sure|ok|okay|do it|go ahead|please do|"
                       r"confirm|confirmed|affirmative|that's right|correct)\s*[.!]?\s*$", re.I)
# A yes that OPENS a sentence, for confirming a guess. Deliberately separate
# from YES_WORDS: that one gates risky actions and must stay bare-word strict so
# a stray word off a video can never approve anything. This one only decides
# whether to run a skill the brain already nearly chose - the worst case is a
# rotated view and another "no" - and "yes please go ahead and finish the
# render" is unmistakably a yes that YES_WORDS threw away.
#
# ANCHORED AT BOTH ENDS. This used to match only the opening word, so after
# "Did you mean lock, sir?" the sentence "okay, what's the weather like" was a
# yes: the PC locked, the weather went unanswered, and "press the windows key"
# was learned as `lock` for good. A yes is the whole utterance, or a yes-word
# followed by nothing but the tails people put on one ("yes please", "ok go
# ahead", "sure, do it"). Anything with a subject after it is a new request.
GUESS_YES = re.compile(r"^\s*(?:yes|yeah|yep|yup|sure|ok|okay|correct|right|"
                       r"that'?s (?:right|it)|please do|go ahead|do it|"
                       r"carry on|keep going|continue)"
                       r"(?:\s*[,.!]?\s*(?:please|sir|thanks|thank you|go ahead|"
                       r"do it|do that|carry on|finish (?:it|the render)))*"
                       r"\s*[.!]?\s*$", re.I)
NO_WORDS = re.compile(r"^\s*(?:no|no thanks|no thank you|nope|nah|naw|cancel|stop|don't|do not|"
                      r"never\s*mind|negative|forget it|not now|n[aã]o|nein|non|nyet|nej|no way)"
                      r"\s*[.!,]?\s*$", re.I)
# Spoken form of the confirmation question, keyed by tool name. NOTE this table
# only ever fires for tools registered MEDIUM or HIGH (see Tool.requires_confirmation):
# today that is type_text, press_keys, click_screen, browser_submit, power_action and
# empty_recycle_bin. The file entries below are deliberately kept but currently
# UNUSED — file operations are LOW because every one of them is reversible (deletes
# go to the Recycle Bin, moves can be moved back). If that ever changes, the wording
# is already here.
CONFIRM_PHRASE = {
    "close_application": lambda a: f"Close {a.get('name', 'that app')}?",
    "open_application": lambda a: f"Open {a.get('name', 'that app')}?",
    "move_file": lambda a: f"Move {a.get('path', 'that file')}?",
    "delete_file": lambda a: f"Send {a.get('path', 'that file')} to the recycle bin?",
    "rename_file": lambda a: f"Rename it to {a.get('new_name', 'that')}?",
    "power_action": lambda a: f"{a.get('action', 'that').capitalize()} the computer?",
    "lock_computer": lambda a: "Lock the computer?",
    "browser_submit": lambda a: "Submit that form?",
    # remote hands (R2): say exactly what will be typed/pressed/clicked
    "type_text": lambda a: f"Type \"{str(a.get('text', ''))[:60]}\""
                           + (f" into {a['window']}" if a.get("window") else "")
                           + (" and press enter?" if a.get("press_enter") else "?"),
    "press_keys": lambda a: f"Press {a.get('keys', 'that')}"
                            + (f" in {a['window']}" if a.get("window") else "") + "?",
    "click_screen": lambda a: f"Click {a.get('cell') or str(a.get('x', '')) + ',' + str(a.get('y', ''))}"
                              + (" (double)" if a.get("double") else "") + "?",
    "click_control": lambda a: f"Click \"{a.get('name', 'that')}\""
                               + (f" in {a['window']}" if a.get("window") else "") + "?",
    "empty_recycle_bin": lambda a: "Permanently empty the recycle bin? This cannot be undone.",
}

# Facts want one right answer every time; a haiku wants a different one each time. The
# configured temperature is deliberately low (see config llm.sampling), which is correct
# for the ~90% of turns that are questions and commands, and deadening for the rest.
CREATIVE_INTENT = re.compile(
    r"\b(write|compose|make up|come up with|invent|brainstorm|imagine|pretend|"
    r"poem|poetry|haiku|limerick|sonnet|story|tale|joke|pun|riddle|song|lyrics|rap|"
    r"slogan|tagline|caption|nickname|name ideas?|ideas for|be creative|surprise me)\b", re.I)
CREATIVE_TEMPERATURE = 0.85

STOP_WORDS = re.compile(r"^\s*(stop|cancel|never\s*mind|nevermind|shut\s*up|quiet|that's\s+enough)\W*$", re.I)
SENTENCE_END = re.compile(r"([.!?…]+[\s\"')\]]*)")
# A bare "Sir." arrives when the model writes the honorific as its own sentence. It is
# already too late to attach it to the line before (that one has been spoken), so speak
# nothing rather than a clipped one-word clip.
BARE_HONORIFIC = re.compile(r"^\W*sir\W*$", re.I)

# "how much is X" / "what does X cost": the answer is a figure, and a search that
# comes back without one has not answered it.
_WANTS_PRICE = re.compile(r"\b(?:price|prices|cost|costs|how much|msrp|"
                          r"going for|selling for|worth)\b", re.I)
_HAS_MONEY = re.compile(r"[$£€]\s?\d|\b\d[\d,]*\s?(?:dollars|usd|gbp|eur|pounds|euros)\b",
                        re.I)

MAX_UTTERANCE_S = 30
SILENCE_END_S = 0.9          # end of speech after this much silence
WAKE_GRACE_S = 3.5           # after a bare 'Jarvis', wait this long for the request to start
MIN_SPEECH_FRAMES = 3        # ~100ms of speech to count as real


class TurnMetrics:
    """Rolling per-turn latency breakdown (last 50 turns)."""

    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.current: dict = {}

    def begin(self) -> None:
        self.current = {"t0": time.time()}

    def mark(self, key: str) -> None:
        if self.current and key not in self.current:
            self.current[key] = round((time.time() - self.current["t0"]) * 1000)

    def finish(self) -> dict:
        cur = self.current
        cur["total_ms"] = round((time.time() - cur.get("t0", time.time())) * 1000)
        cur.pop("t0", None)
        self.turns.append(cur)
        self.turns = self.turns[-50:]
        self.current = {}
        return cur

    def summary(self) -> dict:
        if not self.turns:
            return {}
        def med(key: str) -> int | None:
            vals = sorted(t[key] for t in self.turns if key in t)
            return vals[len(vals) // 2] if vals else None
        return {"turns": len(self.turns),
                "median_stt_ms": med("stt_ms"),
                "median_first_token_ms": med("first_token_ms"),
                "median_first_audio_ms": med("first_audio_ms"),
                "median_total_ms": med("total_ms")}


# Pure command scaffolding: verbs, pronouns and determiners that carry no
# subject of their own. An utterance made ENTIRELY of these is a fragment.
_NO_SUBJECT_WORDS = {
    "show", "me", "tell", "give", "get", "find", "do", "it", "that", "this",
    "them", "those", "these", "the", "a", "an", "my", "your", "again", "now",
    "more", "please", "one", "some", "here", "there", "what", "about",
    "you", "i", "is", "are", "was", "ok", "okay", "yes", "no", "and", "to",
}


# A TURN MAY NOT PUT THE STATE BACK WHEN A NEWER ONE HAS ALREADY TAKEN IT.
#
# Barge-in moves straight to LISTENING and arms the capture, and then the turn
# that was interrupted carries on unwinding and reached `to(IDLE, force=True)` —
# wiping the listening state a few milliseconds after it was set. What he saw:
# he cut in, JARVIS stopped talking, and then nothing happened; he had to wait
# and say the wake word all over again.
#
# These are the states that mean "something newer is in progress". Ending a turn
# must leave them alone.
#
# BUT NOT BY LOOKING AT THE STATE. The first version of this guard asked "is the
# state LISTENING or PROCESSING?" — and a turn's OWN state is PROCESSING, so any
# turn that ended without passing through SPEAKING first (a clarifying question:
# ask, arm the window, return) saw its own PROCESSING, assumed a newer turn owned
# it, and left it there. He could not answer "the company or the stock?" because
# JARVIS was deaf in PROCESSING until the 35 s watchdog put him back — six times
# in two days in the real log, every one right after a question. So the guard is
# a GENERATION: every turn start (and every barge-in) bumps it, each turn carries
# its own number in a context variable, and only the newest turn may settle the
# state. A stale turn unwinding after a barge-in still keeps its hands off.
_NEXT_TURN_STATES = frozenset({State.LISTENING, State.PROCESSING})   # documentation only now
_TURN_GEN: contextvars.ContextVar[int] = contextvars.ContextVar("turn_gen", default=0)


def _teachable(text: str) -> bool:
    """Does this look like an instruction, or like a person talking?

    Self-training turns "the LLM solved this with one tool" into a permanent
    reflex, and it does not care whether the words were ever addressed to
    JARVIS. On 2026-09-02 a wake word fired at 0.82 on him talking to someone
    else, the sentence "I was like this is the challenge? Wait, I need to go on
    easier." reached the model, the model put JARVIS to sleep, and the brain
    duly learned that sentence AS THE SLEEP COMMAND. A mislearned `sleep` is
    particularly bad: it makes random speech dismiss him.

    Commands are SHORT and they are one sentence. This does not have to be
    clever — it has to refuse the obvious non-commands, because the cost of
    declining to learn is that a phrasing stays slow, and the cost of learning
    wrongly is that it does the wrong thing forever.
    """
    t = (text or "").strip()
    if not t:
        return False
    words = t.split()
    if len(words) > 10:
        return False
    # ...and it has to be ABOUT something. Self-training had already learned the
    # bare phrase "show me" as SCREENSHOT, which drags every "show me X" he ever
    # says toward taking a picture of the screen.
    #
    # NOT a minimum word count, which was the first attempt and was too blunt:
    # "open spotify" is two words and a perfectly good command. The difference is
    # that "show me" is nothing but command scaffolding with no object in it.
    if not [w for w in words
            if w.strip(".,!?").lower() not in _NO_SUBJECT_WORDS]:
        return False
    # Two sentences is a person thinking aloud, not an instruction.
    if len(re.findall(r"[.!?]+(?=\s|$)", t)) > 1:
        return False
    # Self-talk and narration markers. "Wait" and "I was like" are never how an
    # instruction opens.
    if re.search(r"\b(?:i was like|i mean|you know|wait|um|uh|hmm|actually,)\b",
                 t, re.I):
        return False
    return True


def _screen_context() -> dict:
    """What is on screen, for the brain to break ambiguous sentences with.

    "Make it bigger" is the interface with an empty stage and the model with
    something on it; "turn it around" is the volume if he is listening to music
    and the model if he is looking at one. The words are genuinely ambiguous and
    only this can settle them.

    Never raises and never blocks: a failure here has to mean "no context",
    which is precisely how the brain behaved before it could see anything.
    """
    ctx = {"stage": False, "project": False, "render": False,
           "last_skill": None}
    try:
        from tools.holo_tools import current
        ctx["stage"] = bool((current() or {}).get("name"))
    except Exception:
        log.debug("no stage context", exc_info=True)
    try:
        from tools.workspace_tools import active
        ctx["project"] = bool(active())
    except Exception:
        log.debug("no project context", exc_info=True)
    try:
        from render_queue import queue
        ctx["render"] = bool((queue.status() or {}).get("busy"))
    except Exception:
        log.debug("no render context", exc_info=True)
    return ctx


def _with_last_skill(orch, ctx: dict) -> dict:
    """Add what he was just talking about, while it is still fresh.

    Only useful to a sentence that points backwards - see _FOLLOW_UP in the
    router - so it is passed always and consulted rarely.
    """
    try:
        from brain.router import FOLLOW_UP_WINDOW_S
        last = getattr(orch, "_last_reflex", None) or {}
        when = getattr(orch, "_last_reflex_at", 0)
        if last.get("skill") and (time.time() - when) < FOLLOW_UP_WINDOW_S:
            ctx["last_skill"] = last["skill"]
    except Exception:
        log.debug("no follow-up context", exc_info=True)
    return ctx


async def _release_camera(why: str) -> None:
    """Put the webcam down. Never raises, never blocks the caller.

    Sleeping with the camera light on is a promise broken: the hand tracker is
    careful never to switch it on by itself, and leaving it running when the
    session ends is the same surprise from the other end.
    """
    try:
        from hand_control import control
        control.disarm(why)
    except Exception:
        log.debug("could not stand the hand tracker down", exc_info=True)
    try:
        from camera import camera
        if camera.is_on:
            await asyncio.to_thread(camera.stop)
            log.info("camera released: %s", why)
    except Exception:
        log.debug("could not release the camera", exc_info=True)


class Orchestrator:
    def __init__(self) -> None:
        self.sm = StateMachine()
        self.vad = StreamingVAD()
        self.metrics = TurnMetrics()
        self._turn_gen = 0                 # see _NEXT_TURN_STATES
        self._history: list[dict] = []
        # Where the prompt's history window starts. Advanced in blocks, never per
        # turn, so the prefix stays byte-identical for the KV cache.
        self._hist_base: int = 0
        self._turn_task: asyncio.Task | None = None
        self._listen_flag = asyncio.Event()   # push-to-talk pressed / listening on
        self._speak_cancel = asyncio.Event()
        # a Telegram-originated turn: same pipeline, but no TTS to an empty room
        # and no spoken yes/no on confirmations (the phone gets buttons instead)
        self.remote_turn = False
        self._loop_task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        # Only assigned once the language model starts, but shutdown() reads it
        # unconditionally — so a failed boot turned into "Application shutdown failed"
        # with an AttributeError on the way out.
        self._watchdog_task: asyncio.Task | None = None
        self._preroll: np.ndarray | None = None     # audio from before the wake word fired
        self._heard_text: str | None = None         # transcript the endpoint check already produced
        self._last_active: float = time.time()      # when he was last needed, for auto-sleep
        self._clarify: clarify.Pending | None = None  # a question asked, answers already fetching
        # A near-miss we asked him to confirm: {skill, args, text}. One
        # question, one turn - it is dropped the moment anything else
        # arrives, so it can never pile up or answer a later sentence.
        self._unsure: dict | None = None
        self._end_silence: tuple[float, float] = (0.0, 0.0)   # (waited, budget) of the last turn
        self._armed_until: float = 0.0               # conversation window (no wake word needed)
        self._sounds = {k: f() for k, f in PALETTE.items()}  # built once, replayed
        self.sm.on_change(self._announce_state)

    # ---------- sound cues ----------

    async def _surface(self) -> None:
        """Come to the front. Never raises — being buried is not worth a crash.

        Split out of _wake_from_sleep because surfacing and un-sleeping are two
        different things, and only one of them was happening when he called.
        Win32 rather than a window message, because the webview is throttled
        while minimised and must not be on the critical path for waking.
        """
        try:
            from tools.windows_tools import exit_sleep_mode
            await asyncio.to_thread(exit_sleep_mode)
        except Exception:
            log.exception("could not come to the front")

    async def _wake_from_sleep(self) -> None:
        await self._surface()
        try:
            await bus.emit("awake", summary="back from sleep")
        except Exception:
            log.exception("could not announce waking")

    # How long JARVIS may sit in a state that cannot hear his name before we
    # decide something has gone wrong and put him back. Comfortably longer than
    # the slowest legitimate turn (a research turn runs ~25s, a brief ~30s).
    # 120s: comfortably past the longest legitimate operation (market_take has a
    # 90s tool budget, a research turn ~25s), and short enough that being deaf is
    # a blip rather than the rest of his afternoon.
    STUCK_AFTER_S = 120.0
    # How long a deaf state may go COMPLETELY SILENT before it is treated as
    # wedged. A turn that is working emits tokens, tool calls and transcripts
    # the whole time, so this cannot cut off slow work — only stalled work.
    # Two minutes of not being able to hear is not a recovery; he had long
    # since decided it was broken.
    SILENT_AFTER_S = 35.0

    async def _deaf_watch(self) -> None:
        """He must never be unable to hear his own name.

        The wake word is only fed in IDLE, WAITING, STARTING and SLEEPING. Nine
        other states exist, and anything that parks him in one of them - a turn
        that raised, a confirmation nobody answered, a tool that never returned -
        leaves him awake, on screen, and deaf. On 2026-08-31 a `press_keys`
        confirmation from a TEST left him on the "NEEDS YOU" screen; Nicholas
        woke the display with the wake word, got his window back, and then could
        not talk to him at all.

        This is the backstop. It does not care WHY he is stuck - the causes will
        keep differing - only that being unable to hear is never a place he stays.
        """
        HEARS = (State.IDLE, State.WAITING, State.STARTING, State.SLEEPING)
        since = time.time()
        last = self.sm.state
        while True:
            # Five seconds, not fifteen: the silence fuse is 35s and a coarse
            # poll would make that anywhere from 35 to 50.
            await asyncio.sleep(5)
            try:
                now_state = self.sm.state
                if now_state != last:
                    last, since = now_state, time.time()
                    continue
                if now_state in HEARS:
                    since = time.time()
                    continue
                stuck_for = time.time() - since
                quiet_for = time.time() - getattr(bus, "last_event_at", time.time())
                # Either it has been silent for a while in a state that cannot
                # hear, or it has been in one far too long even while noisy.
                if stuck_for < self.STUCK_AFTER_S and quiet_for < self.SILENT_AFTER_S:
                    continue
                # Still in the same deaf state, with nothing moving.
                pending = registry.has_pending
                log.error("stuck in %s for %.0fs (silent %.0fs) and cannot hear "
                          "the wake word (pending confirmation: %s) - recovering "
                          "to IDLE", now_state.value, stuck_for, quiet_for, pending)
                if pending:
                    # An unanswered question is what usually holds it. Treat
                    # silence as "no" - refusing is always the safe answer.
                    try:
                        registry.resolve_latest(False)
                    except Exception:
                        log.debug("could not clear the pending confirmation",
                                  exc_info=True)
                await self.sm.to(State.IDLE, force=True)
                await bus.emit("boot", summary="recovered: he can hear you again")
                last, since = self.sm.state, time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("deaf watch tick failed")

    async def _idle_watch(self) -> None:
        """Withdraw when he is not needed.

        After a couple of quiet minutes he minimises himself and goes to sleep,
        so working at the PC does not mean working around him. Sleep is his
        SHIFT, not an off switch — the night school and the proactive watch run
        there — and his name brings him straight back to the front.

        "Idle" means JARVIS is idle, not the machine: an hour in a spreadsheet is
        exactly when he should be out of the way.
        """
        while True:
            await asyncio.sleep(2)
            try:
                # anything that is not sitting still counts as being needed
                if self.sm.state is not State.IDLE:
                    self._last_active = time.time()
                    continue
                if not config.get("presence", "auto_sleep", default=True):
                    continue
                mins = float(config.get("presence", "idle_sleep_minutes", default=2))
                if mins <= 0:
                    continue
                # never vanish in the middle of something, however quiet it looks
                from dictation import dictation as _dict
                if registry.has_pending or self.armed or _dict.active                         or self._clarify is not None:
                    self._last_active = time.time()
                    continue
                if time.time() - self._last_active < mins * 60:
                    continue
                log.info("idle %.0f min - minimising and going to sleep", mins)
                from tools.windows_tools import enter_sleep_mode
                await asyncio.to_thread(enter_sleep_mode)
                # TAKE THE STAGE DOWN ON THE WAY OUT. A hologram deliberately
                # HOLDS the frame while he is working — it is a thing he is
                # working on, not an answer that has stopped being useful — but
                # sleep is the resting state, and a part left projected through
                # it is just stuck. He watched one sit there for half an hour.
                # The file is still on disk: "show me the bracket" brings it back.
                try:
                    from tools.holo_tools import current, hide_hologram
                    if current():
                        await hide_hologram()
                except Exception:
                    log.debug("could not take the stage down for sleep", exc_info=True)
                await _release_camera("going to sleep")
                self._armed_until = 0.0
                await bus.emit("conversation", armed=False)
                await bus.emit("sleep", reason="idle", after_minutes=mins)
                await self.sm.to(State.SLEEPING, force=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("idle watch failed")
                await asyncio.sleep(30)

    def _begin_turn(self) -> None:
        """This task is now the newest turn; older ones may no longer settle the state."""
        self._turn_gen += 1
        _TURN_GEN.set(self._turn_gen)
        # How long since he last spoke to JARVIS, for the wake acknowledgement:
        # a greeting after hours away, a short word otherwise.
        now = time.time()
        self._prev_turn_at = getattr(self, "_last_turn_at", 0.0)
        self._last_turn_at = now

    def _wake_ack_line(self) -> str:
        from brain import persona
        prev = getattr(self, "_prev_turn_at", 0.0)
        gap = (time.time() - prev) if prev else float("inf")
        entries: list[dict] = []
        if gap >= persona.AWAY_S:
            # What JARVIS did on his own while he was away, from the delivery
            # ledger — since he last spoke, or the last twelve hours when this
            # is the first wake of the session.
            try:
                from delivery import delivery
                if not prev:
                    # first wake of THIS process: he last spoke before the
                    # restart, and the transcript remembers when
                    prev = memory.last_user_turn_ts()
                    gap = (time.time() - prev) if prev else float("inf")
                since = prev if prev else time.time() - 12 * 3600
                entries = delivery.entries(since)
                self._away_since = since
                # The voice says one sentence; the screen lists what reached
                # him and what was held back, on the brief stage.
                sections = persona.briefing_sections(entries) if gap >= persona.AWAY_S else []
                if sections:
                    asyncio.create_task(bus.emit("brief", title="While you were away",
                                                 eyebrow="THE LEDGER", sections=sections))
            except Exception:
                log.debug("no delivery ledger for the briefing", exc_info=True)
        ack = persona.wake_line(gap, time.localtime().tm_hour,
                                getattr(self, "_last_wake_ack", None), entries)
        self._last_wake_ack = persona.wake_ack(gap, time.localtime().tm_hour,
                                               getattr(self, "_last_wake_ack", None))
        return ack

    def _newer_turn_started(self) -> None:
        """Something newer took over (a barge-in, a fresh capture) without being
        a turn task of its own yet."""
        self._turn_gen += 1

    def _turn_is_current(self) -> bool:
        return _TURN_GEN.get(0) == self._turn_gen

    async def _settle_idle(self, *keep: "State") -> None:
        """A turn is over: back to IDLE — unless a newer turn owns the state, or
        it is in one of `keep` (ERROR, STARTING, SLEEPING as the caller decides)."""
        if self.sm.state in keep or not self._turn_is_current():
            return
        await self.sm.to(State.IDLE, force=True)
        # The turn may have changed the tool block; if so, re-read the tools
        # prefix now rather than on his next question (see _rewarm_tools_shape).
        if getattr(self, "_warmed_block", None) != shortlist.block_version:
            asyncio.create_task(self._rewarm_tools_shape())

    async def stand_down(self) -> dict:
        """End the conversation window now. He is done talking.

        The window is short on purpose, and he asked for a way to close it
        early rather than for it to be longer — waiting out an open microphone
        is the thing he wanted to stop. Anything mid-flight is interrupted
        first, because "stop listening" while a sentence is still being spoken
        at him is not standing down.

        The window is left alone. Standing down is about the ears; hiding
        himself as well would be a second surprise on top of the one he asked
        to end.
        """
        try:
            if self.sm.state not in (State.IDLE, State.SLEEPING):
                await self.interrupt()
        except Exception:
            log.debug("could not interrupt on stand down", exc_info=True)
        self._armed_until = 0.0
        self._listen_flag.clear()          # the ears, not only the window
        await bus.emit("conversation", armed=False)
        try:
            if self.sm.state not in (State.ERROR, State.STARTING, State.SLEEPING):
                await self.sm.to(State.IDLE, force=True)
        except Exception:
            log.debug("could not settle to idle", exc_info=True)
        log.info("stood down: conversation window closed by hotkey")
        return {"ok": True, "listening": False, "state": self.sm.state.value}

    async def wake_if_sleeping(self, surface: bool = True) -> bool:
        """Any deliberate approach — hotkey, tray, a typed turn — also ends sleep.

        `surface=False` ends the SLEEPING state WITHOUT touching the machine: no
        monitor wake, no window raised to the front.

        Because those are two different things, and treating them as one had a
        consequence he found himself: he messaged JARVIS from Telegram at night
        with the monitor off, and the PC's screen came on. A message from his
        phone is a REMOTE conversation. It has to un-sleep the state machine —
        the turn path only runs from IDLE, so without that he is answered by
        nothing at all — but it has no business lighting up a monitor in a room
        he is not in, or shoving a window in front of whatever was there.
        """
        if self.sm.state != State.SLEEPING:
            return False
        if surface:
            await self._wake_from_sleep()
        else:
            # Still announce it, so the app's own state stays consistent — the
            # HUD does not raise itself on this, it only stops looking asleep.
            try:
                await bus.emit("awake", summary="woken by a remote message")
            except Exception:
                log.exception("could not announce waking")
        await self.sm.to(State.IDLE, force=True)
        return True

    async def play_sound(self, name: str) -> None:
        if not config.get("audio", "sound_cues", default=True):
            return
        snd = self._sounds.get(name)
        if snd is None:
            return
        try:
            from audio.sounds import RATE
            await speaker.play_chunk(snd, RATE)
        except Exception as e:
            log.debug("sound %s failed: %s", name, e)

    def _arm_conversation(self) -> None:
        """Open the follow-up window: speech alone opens a turn, no wake word."""
        # Never while asleep. Six call sites reach here; guarding each one is
        # the kind of thing that stays right until the seventh is added.
        if self.sm.state is State.SLEEPING:
            return
        mode = config.get("wake", "mode", default="push_to_talk")
        win = float(config.get("conversation", "window_s", default=15))
        # WORKING ON A MODEL IS A CONVERSATION, NOT A COMMAND.
        #
        # Five seconds is right for "what's the weather" — ask, hear, done. It is
        # wrong with a part on the stage: he turns it, looks at it, thinks, and
        # says the next thing, and the window has shut every time. His
        # instruction was 35-45 seconds while there is a hologram up, and the
        # reason it can be that long here is that the stage says what the mode
        # is — an open microphone is not a surprise when a model is in front of
        # him and he is working on it.
        try:
            from tools.holo_tools import current
            if current().get("path"):
                win = float(config.get("conversation", "holo_window_s", default=40))
        except Exception:
            log.debug("could not check the stage for the window length",
                      exc_info=True)
        # PICTURES ARE A STAGE TOO. He asked for eight of them, and eight
        # seconds is gone before he has looked at the third — so saying "the
        # second one" needed the wake word again, which is the friction this
        # window exists to remove. Shorter than the hologram's: a model is
        # something he works on for minutes, a grid is something he picks from.
        try:
            from tools import web_tools
            fresh = time.time() - getattr(web_tools, "last_images_at", 0.0)
            if fresh < 30.0:
                win = max(win, float(config.get(
                    "conversation", "images_window_s", default=20)))
        except Exception:
            log.debug("could not check the picture panel", exc_info=True)
        if mode in ("wake_word", "both") and win > 0:
            self._armed_until = time.time() + win
            asyncio.create_task(bus.emit("conversation", armed=True,
                                         until=self._armed_until, window_s=win))

    @property
    def armed(self) -> bool:
        return time.time() < self._armed_until

    async def _announce_state(self, old: State, new: State) -> None:
        await bus.emit("state", state=new.value, prev=old.value)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        registry.confirm_hook = self.ask_confirmation
        registry.confirm_done_hook = self.confirmation_answered
        await self.sm.to(State.STARTING)
        await bus.emit("boot", summary="initializing subsystems")
        # Ears and voice don't depend on the language model: warm them WHILE it loads
        # (20-60 s) so the wake word is live from the first seconds, not the first minute.
        self._llm_ready = False
        audio_boot = asyncio.create_task(self._audio_boot())
        ok = await llama.ensure()
        self._llm_ready = ok
        if not ok:
            await self.sm.to(State.ERROR, force=True)
            await bus.emit("boot_error", summary="language model failed to start — retrying")
            asyncio.create_task(self._llm_retry_loop())
            await audio_boot
            return
        await audio_boot
        self._watchdog_task = asyncio.create_task(self._llm_watchdog())
        self._device_task = asyncio.create_task(self._device_watch())
        self._stuck_task = asyncio.create_task(self._stuck_watchdog())
        self._idle_task = asyncio.create_task(self._idle_watch())
        # The backstop: being unable to hear his name is never permanent.
        self._deaf_task = asyncio.create_task(self._deaf_watch())
        asyncio.create_task(self._warm_prompts())
        asyncio.create_task(tts.warm_phrases())
        # The boot chime plays through whatever the default output is, and on
        # this machine that is the monitor's own speakers over DisplayPort. A
        # sleeping monitor does not accept audio: on 2026-08-31 every restart
        # stalled here for the full 12-second write budget and burned a writer
        # thread, 27 times in one day. He could not have heard it anyway - a
        # dark screen means either he is not there or the panel is asleep.
        if config.get("audio", "boot_sound", default=True) and not _display_off():
            asyncio.create_task(self.play_sound("boot"))
        await self.sm.to(State.IDLE)
        await bus.emit("boot", summary="ready")
        # pre-warm the hidden search browser so the first web search is instant
        from search_brave_web import brave_web
        if brave_web.available:
            asyncio.create_task(brave_web.warmup())

    async def _device_watch(self) -> None:
        """Hot-plug: always use the webcam mic when present, fall back to the
        onboard mic when it's gone. Checks Windows' endpoint list (independent
        of PortAudio's cached view) and re-inits audio only on a change."""
        from audio.io import refresh_devices, speaker as _spk
        patterns = [str(x).lower() for x in
                    config.get("audio", "preferred_input_names",
                               default=["C920", "Webcam", "Logitech"])]
        last_switch = 0.0
        last_heal = 0.0
        last_enum = 0.0

        async def reopen(reason: str) -> bool:
            """Stop, re-enumerate, start — OFF THE EVENT LOOP, and honestly.

            Every one of these calls was running on the loop thread: mic.stop()
            joins the callback, Pa_Initialize probes every endpoint (0.3-2 s on
            Windows), and InputStream open talks to the driver. And when the
            reopen FAILED — the C920 mid-re-enumeration, an exclusive-mode app
            holding the only mic — the exception landed in a DEBUG line, and
            nothing retried: JARVIS was deaf until restart with the HUD saying
            nothing. Now it says so, at a level that is read, and tries again.
            """
            def work() -> None:
                mic.stop()
                _spk.close()      # a re-init would close the output stream underneath us
                refresh_devices()  # refuses, safely, while a writer is stuck in an orphan
                mic.start()
            try:
                await asyncio.to_thread(work)
            except Exception as e:
                log.warning("microphone reopen after %s failed: %s", reason, e)
                await bus.emit("boot", summary="microphone unavailable — retrying")
                return False
            await bus.emit("boot", summary=f"microphone recovered: {mic.device_name}")
            return True

        while True:
            await asyncio.sleep(15)
            try:
                # self-heal: if the stream is open but no audio has arrived for
                # 6 s (e.g. an exclusive-mode app yanked the device), reopen it
                if (mic._stream is not None and mic.last_frame_at
                        and time.time() - mic.last_frame_at > 6
                        and self.sm.state in (State.IDLE, State.SLEEPING)):
                    # BACKED OFF. A stream that opens but never delivers made
                    # this fire every 15 s forever, each time announcing
                    # "microphone recovered" — the 157-false-alarm log.
                    if time.time() - last_heal < 120:
                        continue
                    last_heal = time.time()
                    log.warning("microphone went silent — reopening")
                    await reopen("silence")
                    last_switch = time.time()
                    continue
                # ...and a mic that FAILED to reopen last time gets another go,
                # rather than staying dead because nothing was open to heal.
                if mic.failed and mic._stream is None and time.time() - last_heal > 60 \
                        and self.sm.state in (State.IDLE, State.SLEEPING):
                    last_heal = time.time()
                    await reopen("earlier failure")
                    continue
                if time.time() - last_switch < 300:
                    continue  # never thrash the device: one switch per 5 min max
                if config.get("audio", "input_device") is not None:
                    continue  # user pinned a device explicitly
                if self.sm.state not in (State.IDLE, State.SLEEPING):
                    continue  # never yank the mic mid-conversation
                # ONCE A MINUTE, not every tick. Enumerating every audio endpoint
                # (pycaw CreateDevice per endpoint) was 14% of the sidecar's idle
                # CPU, measured asleep with py-spy on 2026-09-05. A webcam that
                # is unplugged is noticed within a minute, which is soon enough.
                if time.time() - last_enum < 60:
                    continue
                last_enum = time.time()
                from pycaw.pycaw import AudioUtilities
                present = False
                for dev in AudioUtilities.GetAllDevices():
                    try:
                        name = (dev.FriendlyName or "").lower()
                        if dev.state == 1 and any(p in name for p in patterns):  # 1 = active
                            present = True
                            break
                    except Exception:
                        continue
                if not present and mic.using_preferred:
                    # the endpoint query flakes (157 false alarms in one log). Frames still
                    # flowing = the mic is obviously there; and demand two misses in a row.
                    if mic.last_frame_at and time.time() - mic.last_frame_at < 3:
                        misses = 0
                        continue
                    misses = getattr(self, "_dev_misses", 0) + 1
                    self._dev_misses = misses
                    if misses < 2:
                        continue
                self._dev_misses = 0
                if present != mic.using_preferred:
                    log.info("audio device change: webcam mic %s",
                             "connected" if present else "disconnected")
                    last_switch = time.time()
                    await reopen("device change")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("device watch: %s", e)

    async def _llm_watchdog(self) -> None:
        """Runtime self-healing: recover a dead llama-server without a restart."""
        while True:
            await asyncio.sleep(60)
            if self.sm.state not in (State.IDLE, State.SLEEPING, State.ERROR):
                continue  # never health-poke mid-turn
            if await llama.healthy():
                if self.sm.state == State.ERROR:
                    await self.sm.to(State.IDLE, force=True)
                continue
            log.warning("llama-server unhealthy — attempting recovery")
            if self.sm.state != State.ERROR:
                await self.sm.to(State.ERROR, force=True)
                await bus.emit("error", summary="language model connection lost — recovering")
            if await llama.ensure():
                await self.sm.to(State.IDLE, force=True)
                await bus.emit("boot", summary="language model recovered")

    async def _warm_prompts(self) -> None:
        """Pay the prompt-cache cost for both prefix variants (with tools / without) in the
        background at boot, so the user's first real question isn't the slow one (~12 s)."""
        sysmsg = {"role": "system", "content": system_prompt(pinned_block(memory.list_pinned()))}
        user = {"role": "user", "content": turn_context("") + chr(10) + "hi"}
        # The tools variant warms the block a session actually STARTS with
        # (the always-offered set in sticky order), not the full registry in
        # registry order — a prefix nothing would ever extend.
        for tools in (shortlist.warm_block(registry), None):
            try:
                # Each shape on the slot it will live on (see _llm_with_tools).
                async for _ in local_llm.stream([sysmsg, user], tools=tools, max_tokens=1,
                                                slot=0 if tools is not None else 1):
                    pass
            except Exception as e:
                log.info("prompt warm skipped: %s", e)
                return
        self._warmed_block = shortlist.block_version
        log.info("prompt cache warmed (tools + no-tools prefixes)")

    async def _rewarm_tools_shape(self) -> None:
        """Re-read the tools-shape prefix in the background when the block changed.

        A turn that used a tool outside the sticky block changes the block, and
        the NEXT tools-shape turn then re-reads everything after the change -
        2,185 of 7,370 tokens, nine seconds to the first word, measured on
        release 28 right after the suites. Pay it now, while he is not
        waiting, on slot 0, one token, only when nothing else is going on.
        """
        await asyncio.sleep(3.0)
        try:
            if getattr(self, "_warmed_block", None) == shortlist.block_version:
                return
            if self.sm.state not in (State.IDLE, State.SLEEPING) or not getattr(self, "_llm_ready", True):
                return
            version = shortlist.block_version
            tools = shortlist.current_block(registry)
            if not tools:
                return
            sysmsg = {"role": "system", "content": system_prompt(pinned_block(memory.list_pinned()))}
            user = {"role": "user", "content": turn_context("") + chr(10) + "hi"}
            t0 = time.time()
            async for _ in local_llm.stream([sysmsg, user], tools=tools, max_tokens=1, slot=0):
                pass
            self._warmed_block = version
            log.info("tools prefix re-warmed (%d tools) in %.1f s", len(tools), time.time() - t0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.info("tools prefix re-warm skipped: %s", e)

    async def _audio_boot(self) -> None:
        """Warm STT/TTS/wake and open the mic; start the listening loops. Failures
        degrade (logged + announced), never wedge the boot."""
        async def _wake_warm():
            await asyncio.to_thread(wake.warmup)
        for label, warm in (("wake word", _wake_warm),
                            ("speech recognition", stt.warmup),
                            ("voice synthesis", tts.warmup)):
            try:
                await warm()
            except Exception as e:
                log.error("%s warmup failed (continuing): %s", label, e)
                await bus.emit("boot", summary=f"{label} degraded: {e}")
        try:
            mic.start()
        except Exception as e:
            log.error("microphone unavailable: %s", e)
            await bus.emit("boot", summary="microphone unavailable")
        self._loop_task = asyncio.create_task(self._listen_loop())
        self._wake_task = asyncio.create_task(self._wake_loop())
        await bus.emit("boot", summary="ears ready")

    async def _llm_retry_loop(self) -> None:
        """Self-healing: keep retrying LLM startup with backoff (e.g. after OOM)."""
        delay = 15
        while self.sm.state == State.ERROR:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
            log.info("retrying llama-server startup")
            if await llama.ensure():
                self._llm_ready = True
                await bus.emit("boot", summary="language model recovered")
                # audio may already be up (ears warm in parallel at boot); (re)start anything
                # that died, and never let one warmup exception kill self-healing.
                try:
                    if self._loop_task is None or self._loop_task.done():
                        await self._audio_boot()
                    else:
                        await self._warm_prompts()
                except Exception:
                    log.exception("post-recovery warmup failed (continuing)")
                if self._watchdog_task is None or self._watchdog_task.done():
                    self._watchdog_task = asyncio.create_task(self._llm_watchdog())
                if getattr(self, "_device_task", None) is None or self._device_task.done():
                    self._device_task = asyncio.create_task(self._device_watch())
                await self.sm.to(State.IDLE, force=True)
                return

    # ---------- proactive announcements ----------

    def note_proactive(self, text: str) -> None:
        """Something JARVIS said unprompted belongs in the conversation too.

        On 2026-08-31 he was sent a Nepal alert at 1:42, then asked "Any updates
        on that?" at 1:53 and was told "I can't pull the current weather data
        right now." The model was not confused - it was correct about what it
        could see. Proactive messages never entered `_history`, so the last thing
        JARVIS believed they had discussed was a weather question from 1:34, and
        "that" honestly meant the weather.

        Anything he is told is a thing he can ask about next. It goes in the
        history like any other turn.
        """
        text = (text or "").strip()
        if not text:
            return
        self._history.append({"role": "assistant", "content": text})
        self._history = self._history[-20:]
        try:
            from lastseen import last_seen
            last_seen.note_reply(text)
        except Exception:
            log.debug("could not remember the proactive item", exc_info=True)



    async def announce(self, text: str) -> None:
        from brain.skills import honorific as _hon
        text = _hon(text, kind="alert")
        """Speak proactively (reminders etc). Only interrupts IDLE; otherwise
        the message still reaches the user via the event stream/transcript."""
        await bus.emit("announcement", text=text)
        memory.log_turn("assistant", text)
        if self.sm.state != State.IDLE:
            return
        await self.sm.to(State.SPEAKING, force=True)
        cancel = asyncio.Event()
        self._speak_cancel = cancel
        heard = False        # did any audio actually reach the speakers?
        try:
            await self.play_sound("attention")  # 'this wasn't asked for'
            await asyncio.sleep(0.3)
            await bus.emit("speaking", text=text)
            async for chunk in tts.synthesize_stream(clean_for_speech(text), cancel):
                if cancel.is_set():
                    break
                # play_chunk says whether it WROTE. Under /debug/silence it
                # returns early without writing, and `heard = True` here used
                # to open the follow-up window anyway — a mic listening in a
                # silenced room, the exact case the comment below describes.
                if await speaker.play_chunk(chunk, tts.sample_rate):
                    heard = True
        finally:
            if self.sm.state == State.SPEAKING:
                await self.sm.to(State.IDLE, force=True)
            # IT JUST SPOKE TO HIM, SO IT SHOULD BE READY FOR THE ANSWER.
            #
            # This is a REPLY-SHAPED moment even though he did not start it: a
            # render finishing, a reminder, an alert. Without arming, JARVIS says
            # "the plate is ready, sir", he says "thank you" and then "rotate it",
            # and nothing happens — because the follow-up window is only opened
            # at the end of a turn HE began.
            #
            # He hit exactly that: a part finished, he spoke twice, got no reply,
            # and reasonably concluded it had stopped listening. It had not; it
            # was waiting to be named again after speaking to him unprompted,
            # which is not how being spoken to works.
            # ONLY IF HE COULD ACTUALLY HEAR IT.
            #
            # This armed unconditionally for about an hour and it cost him: the
            # output device stalled (his monitor's speakers, asleep), JARVIS
            # spoke into a dead device, and the window opened anyway. He heard
            # nothing, said something that was not to JARVIS, and the mic was
            # sitting open — "Two video." became a YouTube video playing.
            #
            # If he did not hear it, there is nothing for him to be replying to,
            # and an open microphone is worse than a missed follow-up.
            if heard and not cancel.is_set():
                self._arm_conversation()
                self._last_active = time.time()   # ...and he is plainly still here

    # ---------- wake word ----------

    async def _wake_loop(self) -> None:
        """Always-listening detector, active while IDLE and while SLEEPING.

        - keeps a rolling pre-roll so the words spoken *during* wake-word
          detection ("hey jarvis what time is it") are not lost
        - while the conversation window is armed, plain speech opens a turn
        """
        q = mic.subscribe()
        last_fire = 0.0
        last_noise_log = 0.0
        preroll: collections.deque = collections.deque(maxlen=int(MIC_RATE * 2.0 / 1024) + 1)
        armed_vad = StreamingVAD(threshold=0.6)
        consec = 0
        try:
            await asyncio.to_thread(wake.warmup)
        except Exception as e:
            log.error("wake model unavailable: %s", e)
            mic.unsubscribe(q)
            return
        try:
            while True:
                block = await q.get()
                preroll.append(block)
                mode = config.get("wake", "mode", default="push_to_talk")
                # SLEEPING MUST be in this list. Without it the detector is never fed
                # while he is asleep, and the wake word — the whole point of sleep mode —
                # cannot bring him back.
                if mode not in ("wake_word", "both") or self.sm.state not in (
                        State.IDLE, State.WAITING, State.STARTING, State.SLEEPING):
                    consec = 0
                    continue
                # Dictating into another app: his own name may well be in the
                # text being written. Do not answer it.
                from dictation import dictation as _dict
                if _dict.active:
                    consec = 0
                    continue
                # follow-up window: speech alone is enough
                if self.armed:
                    probs = armed_vad.feed(block)
                    consec = consec + 1 if any(p >= armed_vad.threshold for p in probs) else 0
                    if consec >= 3:
                        consec = 0
                        # A television is not talking to him. While another app
                        # is making noise the open window closes and his name is
                        # required again — the wake word still works, so nothing
                        # is lost but the shortcut. (Film dialogue came through
                        # here once and he ran a web search on it.)
                        if config.get("wake", "ignore_while_audio_plays", default=False):
                            playing, who = await output_watch.playing()
                            if playing:
                                # observable, not just logged: a thing that
                                # silently ignores you must be able to say so
                                await bus.emit("wake_suppressed", reason="audio",
                                               app=who or "something")
                                if time.time() - last_noise_log > 30:
                                    last_noise_log = time.time()
                                    log.info("speech heard while %s is playing - "
                                             "the wake word is required", who or "something")
                                continue
                        log.info("follow-up speech (conversation window)")
                        self._preroll = np.concatenate(list(preroll)[-8:])  # ~0.5 s lead-in
                        self._armed_until = 0.0
                        await bus.emit("conversation", armed=False)
                        self.vad.reset()
                        self._listen_flag.set()
                        continue
                score = await asyncio.to_thread(wake.feed, block)
                if score >= wake.threshold and time.time() - last_fire > 2.0:
                    last_fire = time.time()
                    log.info("wake word detected (%.2f)", score)
                    # SNAPSHOT FIRST, SURFACE SECOND. The pre-roll used to be
                    # taken AFTER the window was brought forward — EnumWindows,
                    # an ALT tap, SetForegroundWindow, a display check — and the
                    # audio blocks that arrived in that gap sat in the queue
                    # un-appended, then were drained by the capture. "hey
                    # jarvis what…" in one breath lost its first syllable now
                    # and then. The words are kept before anything else moves.
                    wake.reset()
                    self._preroll = np.concatenate(list(preroll))
                    preroll.clear()
                    self.vad.reset()
                    self._listen_flag.set()
                    if self.sm.state == State.SLEEPING:
                        # must also LEAVE the sleeping state, not just raise the window —
                        # the capture/turn path only runs from IDLE, so restoring the
                        # window alone left him awake-looking but deaf.
                        await self.wake_if_sleeping()
                    else:
                        # Awake but BURIED. Surfacing used to happen only on the
                        # sleeping path, so calling his name while he sat behind
                        # another window got an answer from something invisible —
                        # and the case is common precisely because opening a page
                        # for him focuses Brave over the top of him. Saying his
                        # name is a deliberate approach; it brings him forward.
                        await self._surface()
                    await bus.emit("wake", score=round(score, 2))
                    await self.play_sound("chime")
        except asyncio.CancelledError:
            raise
        finally:
            mic.unsubscribe(q)

    async def shutdown(self) -> None:
        for _t in (self._wake_task, self._loop_task, self._watchdog_task,
                   getattr(self, "_device_task", None), getattr(self, "_turn_task", None)):
            if _t is not None and not _t.done():
                _t.cancel()
        if getattr(self, "_idle_task", None):
            self._idle_task.cancel()
        if self._wake_task:
            self._wake_task.cancel()
        if self._loop_task:
            self._loop_task.cancel()
        self._speak_cancel.set()
        mic.stop()
        speaker.close()
        await llama.stop()

    # ---------- listening control (push-to-talk / toggle) ----------

    async def toggle_listen(self) -> None:
        """Ctrl+Shift+J, and the tray: reach for him, or tell him you're done.

        One key, both directions. Pressed while he is speaking it interrupts;
        pressed while the ears are open — capturing, or the conversation
        window still armed — it STANDS DOWN, closing the window rather than
        merely dropping the capture flag and leaving the window to wait
        itself out. Standing down used to be Ctrl+Shift+S, a global hotkey
        that stole Save As from every application on the machine.
        """
        if self.sm.state == State.SPEAKING:
            await self.interrupt()
            return
        if self._listen_flag.is_set() or self.armed():
            await self.stand_down()
        else:
            mic.drain()
            self.vad.reset()
            self._listen_flag.set()

    async def interrupt(self) -> None:
        self._speak_cancel.set()
        speaker.abort()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        await self.sm.to(State.INTERRUPTED, force=True)
        await bus.emit("interrupted")
        await self.sm.to(State.IDLE)

    # ---------- main listen loop ----------

    async def _listen_loop(self) -> None:
        while True:
            await self._listen_flag.wait()
            if self.sm.state is State.WAITING and registry.has_pending:
                # "JARVIS, YES" WHILE A QUESTION IS OPEN. The wake word fires
                # in WAITING, plays the chime and sets the flag — and this loop
                # used to spin at 20 Hz until the state left WAITING, so the
                # answer was never captured: he said yes, nothing happened,
                # thirty seconds later "I didn't get a yes", and the stale
                # flag then opened a capture at IDLE that ran his "yes" as a
                # brand-new turn ("Did you mean sleep, sir?"). The answer is
                # captured here without touching the state machine and put to
                # the question directly; anything that is not a yes or a no is
                # dropped, and the question stays open for its timer.
                utterance = await self._capture_utterance()
                self._listen_flag.clear()
                self._preroll = None
                if utterance is not None and len(utterance) >= MIC_RATE // 4:
                    try:
                        spoken = (await stt.transcribe(utterance) or "").strip()
                        spoken = WAKE_PHRASE.sub("", spoken, count=1).strip()
                        await bus.emit("transcript", role="user", text=spoken,
                                       source="confirm")
                        if not await self.try_voice_confirmation(spoken):
                            log.info("heard %r during a confirmation; not an answer",
                                     spoken[:40])
                    except Exception:
                        log.exception("could not hear the answer to a question")
                continue
            # LISTENING IS IN THIS LIST. The barge-in handler stops him, sets
            # the flag and puts the state at LISTENING itself; this loop then
            # came round, saw a state it did not start from, and slept 50 ms
            # forever - flag set, nobody capturing, the wake detector not fed
            # in LISTENING either. Thirty-five seconds of "he can't hear me"
            # until the deaf watch reset him (2026-09-06 09:50, and again in
            # the reproduction at 09:58). This loop is the only thing that
            # captures, so a LISTENING it did not set is still its job.
            if self.sm.state in (State.IDLE, State.INTERRUPTED, State.LISTENING):
                self._newer_turn_started()
                await self.sm.to(State.LISTENING)
                utterance = await self._capture_utterance()
                self._listen_flag.clear()
                if utterance is None or len(utterance) < MIC_RATE // 4:
                    await self.sm.to(State.IDLE)
                    continue
                self._turn_task = asyncio.create_task(self._run_turn(utterance))
                try:
                    await self._turn_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.exception("voice turn failed")
                    await bus.emit("error", summary=f"turn failed: {e}")
                    await self.sm.to(State.IDLE, force=True)
            else:
                await asyncio.sleep(0.05)

    async def _capture_utterance(self) -> np.ndarray | None:
        """Capture one utterance, and never leave an endpoint pass running.

        The loop has five exits and only one of them used to cancel
        `endpoint_task`. A live endpoint pass holds `stt._lock` - the very lock
        the turn needs a moment later to transcribe - so the leftover Parakeet
        run competed with the turn's own against the same model. Worse,
        cancelling an `await asyncio.to_thread` does not stop the worker, so it
        also kept a default-pool thread busy.
        """
        try:
            return await self._capture_utterance_inner()
        finally:
            t = getattr(self, "_endpoint_task", None)
            if t is not None and not t.done():
                t.cancel()
            self._endpoint_task = None

    async def _capture_utterance_inner(self) -> np.ndarray | None:
        """Record until VAD detects end-of-speech, PTT released, or timeout."""
        buf: list[np.ndarray] = []
        speech_frames = 0
        last_speech_t: float | None = None
        t0 = time.time()
        self.vad.reset()
        lead_in = self._preroll
        self._preroll = None
        woke_by_name = False
        new_speech_frames = 0
        endpoint_task: asyncio.Task | None = None
        self._endpoint_task = None
        # Cancelled on EVERY exit below, not just one of them: a live endpoint
        # pass holds stt._lock, which is the lock the turn immediately needs to
        # transcribe. Leaving it running meant a stray Parakeet pass competing
        # with the turn's own, against the same model.
        semantic_budget: float | None = None
        # The endpoint check transcribes the utterance to judge it. If nothing
        # more is said afterwards that transcript IS the turn's transcript, and
        # running Parakeet over the same audio a second time costs ~1.5 s of
        # dead air before he starts thinking. Keep it, with the speech count it
        # was taken at, and hand it to the turn when it still covers everything.
        self._heard_text = None
        self._end_silence = (0.0, 0.0)
        endpoint_text: str | None = None
        endpoint_frames = -1
        frames_at_decision = -1
        if lead_in is not None and len(lead_in):
            # the wake phrase (and any words already spoken) live in here;
            # count it as speech so a command said in one breath is kept
            buf.append(lead_in)
            speech_frames = MIN_SPEECH_FRAMES
            last_speech_t = time.time()
            woke_by_name = True
        mic.drain()
        while True:
            if time.time() - t0 > MAX_UTTERANCE_S:
                break
            if not self._listen_flag.is_set() and buf:
                break  # PTT released
            try:
                block = await asyncio.wait_for(mic.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self._listen_flag.is_set():
                    break
                continue
            buf.append(block)
            probs = self.vad.feed(block)
            if any(p >= self.vad.threshold for p in probs):
                n = sum(1 for p in probs if p >= self.vad.threshold)
                speech_frames += n
                new_speech_frames += n
                if last_speech_t is not None and time.time() - last_speech_t > 0.30:
                    # they carried on: whatever we decided was about a fragment
                    semantic_budget = None
                    if endpoint_task is not None:
                        endpoint_task.cancel()
                        endpoint_task = None
                last_speech_t = time.time()
            # After a bare "Jarvis" people often pause before the request.
            # Until new speech actually starts, allow a longer grace period
            # instead of the normal end-of-speech silence.
            end_silence = (WAKE_GRACE_S if (woke_by_name and new_speech_frames < MIN_SPEECH_FRAMES)
                           else semantic_budget or SILENCE_END_S)
            quiet_for = (time.time() - last_speech_t) if last_speech_t else 0.0
            # Once the pause looks real, ask whether the SENTENCE is finished
            # rather than only whether the room is quiet. Runs once per
            # utterance, off the loop, and only moves the deadline below.
            if (endpoint_task is None and semantic_budget is None
                    and last_speech_t is not None
                    and speech_frames >= MIN_SPEECH_FRAMES
                    and not (woke_by_name and new_speech_frames < MIN_SPEECH_FRAMES)
                    and quiet_for > 0.30 and buf):
                frames_at_decision = speech_frames
                endpoint_task = self._endpoint_task = asyncio.create_task(
                    endpoint.decide(np.concatenate(buf), stt, brain))
            if endpoint_task is not None and endpoint_task.done():
                try:
                    secs, why, heard = endpoint_task.result()
                    semantic_budget = secs
                    if heard:
                        endpoint_text, endpoint_frames = heard, frames_at_decision
                    log.info("endpoint: %.2fs (%s) after %r", secs, why, heard[-60:])
                except Exception:
                    semantic_budget = SILENCE_END_S
                endpoint_task = None
            if (last_speech_t is not None
                    and speech_frames >= MIN_SPEECH_FRAMES
                    and quiet_for > end_silence):
                # how long he actually waited, and what he was waiting for
                self._end_silence = (quiet_for, end_silence)
                log.info("end of speech after %.2fs of silence (budget %.2fs)",
                         quiet_for, end_silence)
                break
        if speech_frames < MIN_SPEECH_FRAMES:
            return None
        # Only reuse it if not another word was spoken after it was taken —
        # otherwise it is a transcript of half the sentence.
        if endpoint_text and speech_frames == endpoint_frames:
            self._heard_text = endpoint_text
        return np.concatenate(buf) if buf else None

    # ---------- one conversational turn ----------

    async def _stuck_watchdog(self) -> None:
        """Nothing may leave him unable to answer. A turn that stops making
        progress — a hung audio device, a wedged tool, a lost await — used to
        park the state machine off IDLE forever, silently dropping every later
        request on every channel (2026-08-27: 90 minutes of that). Now a stalled
        state is force-cleared and reported.

        WAITING is exempt: a confirmation is legitimately waiting on the user,
        and registry.execute has its own timeout for it."""
        LIMITS = {State.PROCESSING: 180, State.THINKING: 300, State.SEARCHING: 300,
                  State.EXECUTING: 300, State.SPEAKING: 240, State.INTERRUPTED: 60}
        last_state, since = None, time.time()
        while True:
            await asyncio.sleep(15)
            try:
                st = self.sm.state
                if st is not last_state:
                    last_state, since = st, time.time()
                    continue
                limit = LIMITS.get(st)
                if limit is None or time.time() - since <= limit:
                    continue
                stuck_for = int(time.time() - since)
                log.error("state machine stuck in %s for %ss — forcing IDLE", st.value, stuck_for)
                await bus.emit("error",
                               summary=f"a turn stalled in {st.value} for {stuck_for}s — recovered")
                try:                       # stop any half-played speech and free the device
                    self._speak_cancel.set()
                    speaker.abort()
                except Exception:
                    log.exception("could not quiesce audio during recovery")
                if self._turn_task and not self._turn_task.done():
                    self._turn_task.cancel()
                await self.sm.to(State.IDLE, force=True)
                last_state, since = State.IDLE, time.time()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("stuck watchdog tick failed")

    async def run_text_turn(self, text: str) -> None:
        """Typed input path — same pipeline, no STT.

        Typing is deliberate, so a message that lands while he is mid-sentence
        INTERRUPTS him rather than being thrown away (that used to answer "busy"
        and the message simply vanished). While he is thinking or running a tool
        the turn waits its turn instead."""
        if self.sm.state is State.SPEAKING:
            await self.interrupt()
        # PATIENCE, THEN HONESTY — never force. This waited 60 s and then forced
        # PROCESSING over whatever was still running, so a phone message during
        # a long market read (90 s budget) started a second `_converse` on top
        # of the first: both shared `_speak_cancel` and `_history`, and the
        # first turn's ending forced IDLE out from under the second. The wait
        # now covers the longest tool budget, and if it is still busy after
        # that he is told so rather than having two turns fighting.
        for _ in range(1800):                     # up to 180 s of patience
            if self.sm.state in (State.IDLE, State.INTERRUPTED, State.SLEEPING):
                break
            await asyncio.sleep(0.1)
        else:
            await bus.emit("transcript", role="user", text=text, source="text")
            await bus.emit("turn_done", text="I'm still on the last one, sir — give "
                                             "me a moment and say it again.")
            return
        self._begin_turn()
        await self.sm.to(State.PROCESSING, force=True)
        self.metrics.begin()
        await bus.emit("transcript", role="user", text=text, source="text")
        try:
            await self._converse(text, time.time())
        except Exception as e:
            # a failed turn must never wedge the state machine
            log.exception("text turn failed")
            await bus.emit("error", summary=f"turn failed: {e}")
            # ...and must never leave a remote listener waiting either. The
            # Telegram bridge waits on `turn_done` to know a turn ended; with
            # only `error` emitted here it sat for its full 240 s and then sent
            # "Done, sir." for a turn that had blown up.
            await bus.emit("turn_done", text="Something went wrong with that one, sir.")
            await self.sm.to(State.IDLE, force=True)

    # Skills whose confirmation is not worth the risk of a mis-heard yes.
    # edit_part rewrites the source and re-renders a part he may be about to
    # print; a wrong view is free and a wrong edit is not.
    # ...and lock, sleep and to_phone: a mis-heard "yes" to "Did you mean lock,
    # sir?" locks the PC, and to "to phone" sends whatever was last on screen to
    # his phone. Neither is a wrong view; both are a wrong action he then has to
    # undo, which is exactly the class the guess flow is not allowed to touch.
    _NEVER_GUESS = frozenset({"holo_edit", "holo_revert", "lock", "sleep", "to_phone"})

    async def _ask_if_unsure(self, text: str, t_start: float) -> bool:
        """One short question when the brain nearly knew. True if it was asked."""
        from brain.skills import SKILL_BY_NAME
        u = getattr(brain, "unsure", None)
        if not u or not config.get("brain", "ask_when_unsure", default=True):
            return False
        name = u.get("skill")
        skill = SKILL_BY_NAME.get(name)
        if skill is None or name in self._NEVER_GUESS:
            return False
        if brain.was_rejected(text, name):
            return False        # he has already told me no to exactly this
        # A QUESTION IS NOT A NEAR-MISS FOR AN ACTION. "How many milliliters in
        # a US cup" ranked holo_make at 0.68 and he was asked "Did you mean
        # render that in 3D, sir?" - which is worse than a wrong answer,
        # because it is a wrong answer that also needs a reply. If he is
        # asking how many, who, when or why, the model answers; only a skill
        # that itself answers questions may still be offered.
        from brain.skills import ask_allowed
        if not ask_allowed(text, name):
            log.info("not asking about %s: '%s' is a question, not an order", name, text[:60])
            return False
        from brain.skills import confirm_as
        say = confirm_as(name)
        if not say:
            # No English for it means no question: "Did you mean wakeack,
            # sir?" is worse than a plain answer from the LLM.
            return False
        line = f"Did you mean {say}, sir?"
        self._unsure = {"skill": name, "args": u.get("args") or {},
                        "text": text}
        memory.log_turn("assistant", line)
        await bus.emit("assistant_delta", text=line)
        try:
            await self.speak_line(line)
        except Exception:
            log.warning("could not speak the confirmation aloud", exc_info=True)
        await bus.emit("turn_done",
                       latency_ms=int((time.time() - t_start) * 1000), breakdown={})
        # Not over a NEWER turn: a barge-in during this reply already moved the
        # machine to LISTENING, and forcing IDLE here made the next PROCESSING
        # transition (non-forced, from IDLE) refuse — the HUD showed idle for
        # that whole next turn. Same guard as _ask_clarification.
        await self._settle_idle(State.ERROR, State.STARTING)
        return True

    async def _run_confirmed_guess(self, pend: dict, t_start: float) -> bool:
        """He said yes: do it, and LEARN the wording so it never asks again."""
        from brain.skills import SKILL_BY_NAME
        skill = SKILL_BY_NAME.get(pend.get("skill") or "")
        if skill is None:
            return False
        try:
            # source="user" - his own confirmation is the best label there is,
            # and it skips the checks meant for guesses made from tool use.
            await brain.learn(pend["text"], skill.name, source="user")
        except Exception:
            log.debug("could not learn the confirmed phrasing", exc_info=True)
        await self._reflex_turn(pend["text"],
                                (skill, pend.get("args") or {}, 1.0), t_start)
        return True

    async def _run_turn(self, audio: np.ndarray) -> None:
        if registry.has_pending:          # answering a question, not starting a turn
            pre, self._heard_text = self._heard_text, None
            spoken = (pre if pre is not None else await stt.transcribe(audio) or "").strip()
            spoken = WAKE_PHRASE.sub("", spoken, count=1).strip()
            await bus.emit("transcript", role="user", text=spoken, source="confirm")
            if await self.try_voice_confirmation(spoken):
                return
            # anything else = "no, and here's what I actually want": decline, then carry on
            log.info("heard %r while waiting on a confirmation - treating as no", spoken[:40])
            registry.resolve_latest(False)
            await bus.emit("confirmation_answered", approved=False, source="implicit")
            for _ in range(50):          # let the declined turn wind down (<= 5 s)
                if self.sm.state in (State.IDLE, State.INTERRUPTED):
                    break
                await asyncio.sleep(0.1)
            if not spoken or STOP_WORDS.match(spoken):
                return
            self._begin_turn()
            await self.sm.to(State.PROCESSING, force=True)
            self.metrics.begin()
            await bus.emit("transcript", role="user", text=spoken)
            try:
                await self._converse(spoken, time.time())
            finally:
                self._arm_conversation()
            return
        self._begin_turn()
        await self.sm.to(State.PROCESSING)
        t_start = time.time()
        self.metrics.begin()
        already = self._heard_text          # transcribed while judging the pause
        self._heard_text = None
        text = already if already is not None else await stt.transcribe(audio)
        self.metrics.mark("stt_ms")
        text = WAKE_PHRASE.sub("", text or "", count=1).strip()
        waited, budget = self._end_silence
        await bus.emit("transcript", role="user", text=text,
                       stt_ms=int((time.time() - t_start) * 1000),
                       silence_ms=int(waited * 1000), budget_ms=int(budget * 1000))
        if not text:
            # just the wake word — acknowledge and open the window. Not always
            # "Yes?": the films' JARVIS varies it, and greets him by the time
            # of day when he has been away (brain/persona.py).
            ack = self._wake_ack_line()
            await self.sm.to(State.SPEAKING, force=True)
            await bus.emit("speaking", text=ack)
            cancel = asyncio.Event()
            self._speak_cancel = cancel
            try:
                async for chunk in tts.synthesize_stream(ack, cancel):
                    if cancel.is_set():
                        break
                    await speaker.play_chunk(chunk, tts.sample_rate)
            except SpeakerStalled as e:
                log.error("wake acknowledgement not spoken: %s", e)
            finally:
                await self.sm.to(State.IDLE, force=True)
            self._arm_conversation()
            return
        if STOP_WORDS.match(text):
            await self.sm.to(State.IDLE)
            return
        await self._converse(text, t_start)
        # NOT AFTER "GO TO SLEEP". The sleep reflex disarms the window and moves
        # to SLEEPING; this unconditional re-arm then opened it again, so the
        # HUD showed the open-mic badge right after he had dismissed him, and
        # anything said in the next fifteen seconds set the listen flag — which
        # is never cleared until the next wake word, and stored a stale pre-roll
        # that came out in front of his next real request.
        if self.sm.state is not State.SLEEPING:
            self._arm_conversation()

    async def _converse(self, text: str, t_start: float) -> None:
        # Just his NAME and nothing else. By voice this is handled up in the turn
        # ("he only said the wake word — acknowledge and listen"), but a typed
        # "Jarvis" reached here with the name stripped off, leaving an empty
        # string for the router to match — and an empty string is nearest to
        # something. Over Telegram it came back "It's 7:46 AM, sir."
        # A new turn: forget which links the LAST one was allowed to mention.
        link_ledger.clear()
        if not WAKE_PHRASE.sub("", text or "", count=1).strip():
            await self._say_and_finish("At your service, sir.", text, t_start, "attention")
            return
        memory.log_turn("user", text)
        facts.reset_evidence()
        # ---- "did you mean X?" is still open: this may be the answer -------
        if self._unsure is not None:
            pend, self._unsure = self._unsure, None
            if GUESS_YES.match(text or ""):
                if await self._run_confirmed_guess(pend, t_start):
                    return
            elif NO_WORDS.match(text or ""):
                # NEVER ASK HIM THAT AGAIN. Dropping the guess and remembering
                # nothing is what made "look at reddit and tell me what's
                # trending" ask "did you mean news, sir?" twice in a row.
                try:
                    brain.reject(pend.get("text") or "", pend.get("skill") or "")
                except Exception:
                    log.debug("could not record the rejection", exc_info=True)
                # AND STOP HERE. A bare "no" that fell through went to the
                # router as its own utterance, matched the `correction` skill
                # at 1.0, and unlearned whatever reflex had fired in the last
                # forty seconds — a phrasing he had confirmed the day before,
                # silently gone — before answering "Sorry about that. What did
                # you want?" to a question he had just answered.
                await self._say_and_finish("Understood, sir.", text, t_start, "guess")
                return
            # Anything else is not an answer to the question. Fall through and
            # treat it as the request it is - he asked for something else.
        # ---- a question he was asked is still open: this may be the answer ----
        if self._clarify is not None:
            if await self._answer_clarification(text, t_start):
                return
        # ---- or the request splits, and one short question settles it --------
        amb = clarify.detect(text)
        if amb is not None and await self._ask_clarification(amb, t_start):
            return
        # ---- reflex: JARVIS's own brain handles known requests without the LLM ----
        reflex = None
        if config.get("brain", "enabled", default=True):
            try:
                steps = await brain.match_command(text)
                if steps:
                    await self._routine_turn(text, steps, t_start)
                    return
                # A protocol he never taught gets an offer to set one up, not
                # the shrug an unknown sentence gets from the model.
                from brain import protocols
                pname = protocols.protocol_name(text)
                if pname and not protocols.wants_listing(text):
                    await self._say_and_finish(protocols.missing_line(pname), text,
                                               t_start, "protocol")
                    return
                reflex = await brain.decide(
                    text, context=_with_last_skill(self, _screen_context()))
            except Exception:
                log.exception("brain decide failed - falling back to the LLM")
        self.metrics.mark("brain_ms")
        if reflex and (not reflex[0].llm_after
                       or (reflex[0].direct_if is not None and reflex[0].direct_if(text))):
            await self._reflex_turn(text, reflex, t_start)
            return
        # ---- it nearly knew: one short question rather than a wrong guess ----
        if reflex is None and await self._ask_if_unsure(text, t_start):
            return
        # ---- realm 1: a stored, web-verified, timeless fact answers instantly ----
        if reflex is None:
            try:
                fact = await facts.lookup(text)
            except Exception:
                log.exception("fact lookup failed — continuing to the LLM")
                fact = None
            if fact:
                await self._fact_turn(text, fact, t_start)
                return
        if not getattr(self, "_llm_ready", True):
            line = "My language model is still loading. Give me a few more seconds and ask again."
            memory.log_turn("assistant", line)   # History must show BOTH sides of the turn
            await bus.emit("assistant_delta", text=line)
            try:
                await self.speak_line(line)
            except Exception:
                # He asked a question and heard nothing back. That is
                # indistinguishable from JARVIS being broken, so it does not get
                # to happen without a line in the log saying why.
                log.warning('could not speak the reply aloud', exc_info=True)
            await bus.emit("turn_done", latency_ms=int((time.time() - t_start) * 1000), breakdown={})
            await self._settle_idle(State.ERROR, State.STARTING)
            return
        # brain thinks this is a plain question -> steer the LLM away from needless tool use
        self._no_tools_first = False
        general_hint = ""
        if not reflex and config.get("brain", "enabled", default=True) and not SEARCH_INTENT.search(text):
            try:
                level = await brain.general_level(text)
            except Exception:
                level = None
            if level:
                self._no_tools_first = level == "sure"
                general_hint = ("[Note: this is a general knowledge or creative question - answer from "
                                "your own knowledge right away; do not search or use tools for it.]")
                await bus.emit("reflex", skill="general", tool=None, args={},
                               confidence=brain._last[1],
                               mode="answer_directly" if level == "sure" else "answer_hint")
        # speaker + speak-before-thinking start now, before memory search and any tool
        # pre-run, so the filler lands ~0.7 s after the user stops talking
        await self.sm.to(State.THINKING)
        self._speak_cancel = asyncio.Event()
        speak_queue: asyncio.Queue[str | None] = asyncio.Queue()
        speaker_task = asyncio.create_task(self._speaker_worker(speak_queue))
        self._first_token = asyncio.Event()
        filler_task = asyncio.create_task(self._filler(speak_queue, reflex))
        try:
            mem_hits = await memory.search(text, top_k=4)
        except Exception:
            log.exception("memory search failed — continuing without recall")
            mem_hits = []
        self.metrics.mark("memory_ms")
        # PINNED MEMORIES LIVE IN THE SYSTEM PROMPT, not in the turn note. They
        # change once a week; the turn note changes every minute. Carrying ten
        # of them in the note made them part of the ~300 tokens the model had
        # to read afresh on every turn, at five milliseconds a token.
        pinned = memory.list_pinned()
        lines = [f"- {m['content']}" for m in mem_hits
                 if m["content"] not in pinned]
        mem_ctx = "\n".join(lines)

        # Static prefix (persona + tools) is identical every turn -> KV-cache hit.
        # Time + memories ride along inside the latest user message instead.
        messages: list[dict] = [{"role": "system", "content": system_prompt(pinned_block(pinned))}]
        # The window START must not move every turn. `self._history[-10:]`
        # advanced by two entries per turn, so everything after the tool block
        # was new text and llama.cpp re-processed the lot - the prompt cache
        # could never hit. Moving the base in BLOCKS makes four turns in five a
        # pure prefix extension, which is exactly what the cache is for.
        if len(self._history) - self._hist_base > 16:
            self._hist_base = len(self._history) - 8
        messages += self._history[self._hist_base:]
        messages.append({"role": "user", "content": turn_context(mem_ctx, want_honorific()) + chr(10)
                         + (general_hint + chr(10) if general_hint else "") + text})

        if reflex:
            # brain knew which tool to run; run it now and let the LLM compose the answer
            skill, args, conf = reflex
            brain.stats["reflex"] += 1
            await bus.emit("reflex", skill=skill.name, tool=skill.tool, args=args,
                           confidence=conf, mode="tool_then_llm")
            await self.sm.to(State.SEARCHING if skill.tool in ("web_search", "research")
                             else State.EXECUTING, force=True)
            result = await registry.execute(skill.tool, args)
            last_seen.note_result(result.get("result") if isinstance(result, dict) else result)
            link_ledger.note(result)
            call_id = "reflex-" + uuid.uuid4().hex[:8]
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": skill.tool, "arguments": json.dumps(args)}}]})
            if isinstance(result, dict) and result.get("ok"):
                # Normally the work is done and the model just composes the answer.
                # But when he asked what something COSTS and not one result carries a
                # price, forbidding another tool leaves only two ways out: invent a
                # figure, or shrug. He shrugged. Let him open the page instead —
                # that is what a person does, and it is why they searched.
                if _WANTS_PRICE.search(text) and not _HAS_MONEY.search(
                        json.dumps(result, default=str)):
                    result = {**result, "note": "These results may not contain the price. "
                                                "If they do not, open the most promising one "
                                                "and read it, then answer in one or two "
                                                "spoken sentences."}
                    log.info("search results carry no price - letting him read a page")
                else:
                    result = {**result, "note": "Answer the user from these results now, "
                                                "in one or two spoken sentences."}
                    self._no_tools_first = True
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(result, default=str)})
        else:
            brain.stats["llm"] += 1

        await self.sm.to(State.THINKING, force=True)
        full_reply = ""
        try:
            full_reply = await self._llm_with_tools(messages, speak_queue)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("turn failed")
            await bus.emit("error", summary=str(e))
            await speak_queue.put("I hit a problem with that. Give me a moment.")
        finally:
            filler_task.cancel()
            await speak_queue.put(None)
            try:
                await speaker_task
            except asyncio.CancelledError:
                pass

        # THE GATE. Every URL here has to have come out of a tool this turn.
        # He asked for Amazon links, JARVIS ran six real searches, ignored all
        # sixty results and invented the ASINs - including "B08XYZ1234", which is
        # a placeholder wearing a product's clothes. A missing link disappoints
        # him; a fabricated one costs him the trust he has in every other link.
        if full_reply:
            full_reply, invented = link_check(full_reply, link_ledger)
            if invented:
                log.warning("blocked %d invented link(s) the model made up: %s",
                            len(invented), ", ".join(invented[:4]))
                full_reply += link_explain(invented)
            # Blocking the fakes is half of it. If he ASKED for links and now has
            # none, hand him the ones the searches really returned - otherwise he
            # is back where he started and has to ask a second time.
            if wanted_links(text):
                full_reply = link_supply(full_reply, link_ledger)
            # A real link described as something it is not is still a wrong
            # answer. Where the caption and the source's own title share nothing,
            # show him what the page actually calls itself.
            full_reply, mislabelled = check_captions(full_reply, link_ledger)
            if mislabelled:
                log.info("annotated %d link(s) whose caption did not match the "
                         "source's own title", mislabelled)
            # And a price nobody looked up is not a fact about today.
            full_reply = price_caveat(full_reply, link_ledger)

        if full_reply:
            from brain.skills import without_honorific
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": without_honorific(full_reply)})
            trimmed = len(self._history) - 20
            if trimmed > 0:
                self._history = self._history[-20:]
                self._hist_base = max(0, self._hist_base - trimmed)
            memory.log_turn("assistant", full_reply)
        breakdown = self.metrics.finish()
        latency = int((time.time() - t_start) * 1000)
        memory.log_turn_stat("tool_then_llm" if reflex else
                             "llm_general" if general_hint else "llm_tools",
                             reflex[0].name if reflex else None, latency)
        # the turn's web evidence may graduate into a timeless fact (realm 1) —
        # classified in the background, never blocking the conversation
        evidence = facts.take_evidence()
        if full_reply and evidence:
            from brain.skills import without_honorific as _wh
            answer = _wh(strip_markdown(full_reply))
            ev = evidence[-1]
            spawn(self._fact_intake(text, answer, ev), name='fact-intake')
        # the transcript takes the markdown-free text; the streamed deltas are raw
        await bus.emit("turn_done", latency_ms=latency,
                       breakdown=breakdown, text=strip_markdown(full_reply or ""))
        # The LLM path, same rule: a barge-in mid-reply has already moved on.
        await self._settle_idle(State.ERROR)

    _FILLERS = ["Let me see.", "One moment.", "Let me think.", "Hmm, let me check.", "Just a second."]
    _TOOL_FILLERS = {"web_search": ["Searching.", "Let me look that up.", "Checking the web."],
                     "research": ["Let me dig into that.", "Researching."],
                     "show_images": ["Finding pictures."],
                     "browser_open": ["Let me read that page.", "Loading the page."],
                     "recall": ["Let me think back.", "Let me remember."]}

    async def _filler(self, speak_queue: asyncio.Queue, reflex) -> None:
        """Say a short human filler if the model hasn't produced a word within ~0.7 s."""
        if not config.get("speech", "fillers", default=True):
            return
        delay = 0.05 if reflex else 0.35  # an LLM first token is never faster than ~2 s anyway
        try:
            await asyncio.wait_for(self._first_token.wait(), timeout=delay)
            return
        except asyncio.TimeoutError:
            pass
        if self._speak_cancel.is_set():
            return
        pool = self._TOOL_FILLERS.get(reflex[0].tool, self._FILLERS) if reflex else self._FILLERS
        choice = pool[int(time.time() * 10) % len(pool)]
        if choice == getattr(self, "_last_filler", None):
            choice = pool[(pool.index(choice) + 1) % len(pool)]
        self._last_filler = choice
        await bus.emit("filler", text=choice)
        await speak_queue.put(choice)

    async def _exec_skill(self, skill, args: dict, queue: asyncio.Queue,
                          prefetched: dict | None = None) -> str:
        """Run one reflex skill (tool + templated speech) and return what was said.

        `prefetched` is a result already fetched while a clarifying question was
        being asked — the whole point of asking early, so don't fetch it twice."""
        res: dict = {}
        reply = ""
        from brain.skills import polish
        if skill.speak_first and skill.tool:
            # announce the action immediately ("Opening youtube.com."), then do it
            reply = polish(skill.speak(args, {}))
            self.metrics.mark("first_token_ms")
            # A skill may deliberately say NOTHING — taking a screenshot is the
            # case: the picture is the answer and "Screenshot saved." is one more
            # thing to read. Nothing may be pushed at the speaker in that case.
            if reply.strip():
                await bus.emit("assistant_delta", text=reply)
                await queue.put(clean_for_speech(reply))
        if skill.tool and prefetched is not None:
            res = prefetched if isinstance(prefetched, dict) else {"value": prefetched}
            last_seen.note_result(res)
            link_ledger.note(res)
        elif skill.tool:
            await self.sm.to(State.EXECUTING, force=True)
            out = await registry.execute(skill.tool, args)
            last_seen.note_result(out.get("result"))
            link_ledger.note(out)
            if out.get("ok"):
                res = out.get("result")
            else:
                res = {k: v for k, v in out.items() if k != "ok"} or {"error": "failed"}
                res.setdefault("error", "failed")
            if not isinstance(res, dict):
                res = {"value": res}
        # A COST QUESTION, raised by the tool rather than detected from his words.
        # `_ask` means "this would take a while — say how long and let him
        # decide". It is not the risk gate: the tool is honestly LOW and stays
        # LOW, and promoting it to force a confirmation would corrupt what the
        # tier means. The question is asked here, the answer arrives as the next
        # utterance, and `_answer_clarification` runs the branch he picked.
        asked = res.pop("_ask", None) if isinstance(res, dict) else None
        if asked:
            installed = self._install_cost_question(asked)
            if installed:
                reply = polish(asked.get("question") or "Shall I, sir?")
                self.metrics.mark("first_token_ms")
                await bus.emit("assistant_delta", text=reply)
                await queue.put(clean_for_speech(reply))
                return reply

        if skill.speak_first and skill.tool:
            if "error" in res:
                extra = skill.speak(args, res)
                reply += " " + extra
                await bus.emit("assistant_delta", text=" " + extra)
                await queue.put(clean_for_speech(extra))
        else:
            try:
                # A SKILL WITH NO TEMPLATE SPEAKS ITS TOOL'S OWN WORDS. Six
                # skills (project_list, project_open, project_note, return_home,
                # media_play and friends) have `speak=None`; calling None
                # raised TypeError into the except below, which answered "Done."
                # to "what projects do we have" while the tool's actual answer —
                # the list, in its `spoken` field — was thrown away, and the log
                # filled with "reflex speak template failed" for every one.
                if skill.speak is not None:
                    reply = polish(skill.speak(args, res))
                else:
                    spoken = res.get("spoken") if isinstance(res, dict) else None
                    if not spoken and isinstance(res, dict) and res.get("error"):
                        spoken = str(res["error"])
                    reply = polish(spoken or "Done.")
            except Exception:
                log.exception("reflex speak template failed")
                reply = "Done." if "error" not in res else "I'm afraid that didn't work."
            self.metrics.mark("first_token_ms")
            if reply.strip():          # see above: silence can be the right answer
                await bus.emit("assistant_delta", text=reply)
                await queue.put(clean_for_speech(reply))
        return reply

    async def _fact_intake(self, question: str, answer: str, evidence: dict) -> None:
        """Background: maybe graduate this turn's sourced answer into the fact
        store. Waits out the TTS tail so the classifier never contends with a turn."""
        try:
            await asyncio.sleep(3)
            stored = await facts.consider(question, answer,
                                          evidence.get("sources") or [],
                                          evidence.get("origin", "search"))
            if stored:
                await bus.emit("fact_learned", question=question)
        except Exception:
            log.exception("fact intake failed")

    async def _fact_turn(self, text: str, fact: dict, t_start: float) -> None:
        """A stored timeless fact answers without the LLM (~0.3 s). The receipts
        (sources, verified date) stay on facts.last_served for "how do you know"."""
        from brain.skills import polish
        brain.stats["fact"] = brain.stats.get("fact", 0) + 1
        await bus.emit("reflex", skill="fact", tool=None,
                       args={"verified": fact["verified_ts"]},
                       confidence=fact["score"], mode="direct")
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        reply = polish(fact["answer"])
        self.metrics.mark("first_token_ms")
        await bus.emit("assistant_delta", text=reply)
        await queue.put(clean_for_speech(reply))
        await self._finish_reflex(text, reply, t_start, "fact", queue, task)

    # ---------- clarify: ask, but fetch every answer while asking ----------

    def _drop_clarification(self, why: str) -> None:
        """Stop speculating. Any fetch still running is work nobody asked for."""
        if self._clarify is not None:
            self._clarify.cancel()
            log.info("clarification dropped (%s)", why)
            self._clarify = None

    def _install_cost_question(self, asked: dict) -> bool:
        """Arm the yes/no a tool asked for, so his next words answer it.

        Nothing is fetched and nothing is started — both branches are deferred,
        because the whole point of asking is that the expensive thing has not
        happened yet. Returns False if it could not be armed, in which case the
        caller speaks normally and nothing is lost but the question.
        """
        try:
            tool = str(asked.get("tool") or "")
            if not tool or registry.get(tool) is None:
                return False

            def render(a, r, _t=tool):
                if not isinstance(r, dict):
                    return "Started, sir."
                if r.get("error"):
                    return f"{str(r['error']).rstrip('.')}, sir."
                return str(r.get("spoken") or "Starting now, sir.")

            # THE VERB OF THE THING IS A YES. Asked "about forty seconds, sir,
            # shall I?" about a Spider-Man render, he said "Render it." and was
            # told that was not an answer; the words then became a NEW request
            # for a model called "it". Repeating the order is the plainest
            # yes there is.
            verbs = {
                "make_hologram": ("render", "make", "build", "create", "generate", "show",
                                  "project", "model"),
                "generate_part": ("make", "build", "generate", "create", "print", "design"),
                "slice_part": ("slice", "print", "prepare"),
            }
            amb = clarify.approval(
                subject=str(asked.get("subject") or "that"),
                question=str(asked.get("question") or "Shall I, sir?"),
                tool=tool, args=dict(asked.get("args") or {}), render=render,
                yes_words=tuple(asked.get("yes_words") or verbs.get(tool, ())))
            self._drop_clarification("superseded")
            self._clarify = clarify.Pending(amb)
            # He must be able to answer without saying the name again — but only
            # if he is in the room. A question asked over Telegram must not open
            # the microphone here.
            if not self.remote_turn:
                self._arm_conversation()
            return True
        except Exception:
            log.exception("could not ask whether to start that")
            return False

    async def _ask_clarification(self, amb, t_start: float) -> bool:
        """Ask which reading he meant, and start fetching ALL of them right now.

        Returns False if this cannot be done safely, in which case the turn
        carries on as if the ambiguity had never been noticed.
        """
        def needs_confirmation(tool_name: str) -> bool:
            tool = registry.get(tool_name)
            if tool is None:
                raise KeyError(tool_name)           # unknown = not safe = refuse
            return tool.requires_confirmation

        if not clarify.validate(amb, needs_confirmation):
            return False
        self._drop_clarification("superseded")
        pending = clarify.Pending(amb)
        for b in amb.branches:
            # A branch that ACTS is not run until he picks it. Speculating on a
            # lookup wastes a read; speculating on an action does the thing.
            if not getattr(b, "speculative", True):
                continue
            async def fetch(tool=b.tool, args=dict(b.args)):
                out = await registry.execute(tool, args)
                return out.get("result") if out.get("ok") else {
                    "error": out.get("error") or "that didn't come back"}
            pending.tasks[b.label] = spawn(fetch(), name=f"clarify:{b.label}")
        self._clarify = pending
        await bus.emit("clarify", subject=amb.subject, question=amb.question,
                       options=[b.label for b in amb.branches])
        log.info("asking %r; fetching %s in the background", amb.question,
                 [b.label for b in amb.branches])
        self.metrics.mark("first_token_ms")
        memory.log_turn("assistant", amb.question)
        try:
            await self.speak_line(amb.question)      # emits the text itself
        except Exception:
            log.exception("could not ask the clarifying question")
        await bus.emit("turn_done", latency_ms=int((time.time() - t_start) * 1000),
                       breakdown={}, reflex="clarify", text=amb.question)
        await self._settle_idle(State.ERROR, State.SLEEPING)
        # Answer it without saying his name again — but only if he is IN the room.
        # A question asked over Telegram must not open the microphone here, or
        # anything said near the PC gets treated as his reply to a question he
        # asked from somewhere else entirely.
        if not self.remote_turn:
            self._arm_conversation()
        return True

    async def _answer_clarification(self, text: str, t_start: float) -> bool:
        """He answered the question. Use the branch that is already warm.

        Returns False when what he said was not an answer at all — then the
        speculation is dropped and the sentence is treated as a fresh request,
        which is what it is.
        """
        pending = self._clarify
        if pending is None:
            return False
        if pending.stale:
            self._drop_clarification("stale")
            return False
        picked = clarify.choose(pending, text)
        if picked is None:
            # NOT an answer — so this sentence is a fresh request and is treated
            # as one. But the question STAYS open: something said in the room
            # (a television, someone else talking) must not throw away an answer
            # he is still about to give. Its own TTL ends it soon enough.
            log.info("that was not an answer — the question stays open")
            return False
        self._clarify = None
        if picked == "drop":
            pending.cancel()
            await self._say_and_finish("Of course, sir.", text, t_start, "clarify")
            return True
        chosen = list(pending.amb.branches) if picked == "both" else [picked]
        pending.cancel(keep=None if picked == "both" else picked.label)

        from brain.skills import polish
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        said = []
        for b in chosen:
            try:
                if not b.tool:
                    res = {}          # declining runs nothing, which is the point
                elif b.label not in pending.tasks:
                    # A deferred branch: nothing was run on speculation, so it
                    # runs NOW, having been chosen. This is the only path on
                    # which a clarified answer costs a round trip, and it is the
                    # one where the alternative was doing something he had not
                    # asked for yet.
                    out = await registry.execute(b.tool, dict(b.args))
                    res = out.get("result") if out.get("ok") else {
                        "error": out.get("error") or "that didn't come back"}
                else:
                    res = await asyncio.wait_for(pending.tasks[b.label], timeout=30)
            except asyncio.CancelledError:
                res = None
            except Exception:
                log.exception("the %s branch failed", b.label)
                res = {"error": "that didn't come back"}
            waited = int((time.time() - t_start) * 1000)
            log.info("answered %r from the %s branch (%s, %d ms into the turn)",
                     pending.amb.subject, b.label,
                     "already fetched" if res is not None else "refetching", waited)
            if res is None:                     # cancelled out from under us
                res = {"error": "that didn't come back"}
            try:
                line = polish(b.render(dict(b.args), res))
            except Exception:
                log.exception("clarified answer failed to render")
                line = "I'm afraid that didn't work."
            if said:
                await bus.emit("assistant_delta", text=" ")
            self.metrics.mark("first_token_ms")
            await bus.emit("assistant_delta", text=line)
            await queue.put(clean_for_speech(line))
            said.append(line)
        await self._finish_reflex(text, " ".join(said), t_start, "clarify", queue, task)
        return True

    async def _say_and_finish(self, line: str, text: str, t_start: float,
                              label: str) -> None:
        """One spoken line, through the normal reflex plumbing."""
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        self.metrics.mark("first_token_ms")
        await bus.emit("assistant_delta", text=line)
        await queue.put(clean_for_speech(line))
        await self._finish_reflex(text, line, t_start, label, queue, task)

    async def _finish_reflex(self, text: str, reply: str, t_start: float, label: str,
                             queue: asyncio.Queue, task: asyncio.Task) -> None:
        await queue.put(None)
        try:
            await task
        except asyncio.CancelledError:
            pass
        last_seen.note_reply(reply)
        if reply:
            from brain.skills import without_honorific
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": without_honorific(reply)})
            trimmed = len(self._history) - 20
            if trimmed > 0:
                self._history = self._history[-20:]
                self._hist_base = max(0, self._hist_base - trimmed)
            memory.log_turn("assistant", reply)
        breakdown = self.metrics.finish()
        breakdown["reflex"] = label
        latency = int((time.time() - t_start) * 1000)
        memory.log_turn_stat("fact" if label == "fact" else
                             "routine" if label == "command" else "reflex", label, latency)
        await bus.emit("turn_done", latency_ms=latency,
                       breakdown=breakdown, reflex=label, text=strip_markdown(reply or ""))
        if self.sm.state != State.ERROR:
            # "go to sleep" ends in SLEEPING, not IDLE: no follow-up window, nothing but
            # the wake word gets his attention again.
            if label == "sleep":
                self._armed_until = 0.0
                # nothing may keep fetching once he has been dismissed
                self._drop_clarification("he went to sleep")
                await self.sm.to(State.SLEEPING, force=True)
                await bus.emit("conversation", armed=False)
            elif self._turn_is_current():
                await self.sm.to(State.IDLE, force=True)

    async def ask_confirmation(self, tool: str, args: dict) -> None:
        """A risk-gated tool is waiting on the user: ask out loud AND listen for a spoken
        yes/no here (this hook is awaited before registry.execute waits on the future, so
        resolving it now unblocks the tool). Falls back to the on-screen confirm if voice
        is off or unclear."""
        await self.sm.to(State.WAITING, force=True)
        if self.remote_turn:
            return  # the phone shows DO IT / NO buttons; no room to speak into
        phrase = CONFIRM_PHRASE.get(tool, lambda a: f"Should I run {tool.replace('_', ' ')}?")(args or {})
        try:
            await self.speak_line(phrase + " Say yes or no.")
        except Exception:
            log.exception("could not speak the confirmation question")

        mode = config.get("wake", "mode", default="both")
        if mode not in ("wake_word", "both") or not config.get("confirm", "by_voice", default=True):
            return  # push-to-talk only, or voice-confirm disabled -> UI/text answers it
        for attempt in range(2):
            answer = await self._listen_yes_no()
            if answer is None:
                if attempt == 0 and registry.has_pending:
                    await self.speak_line("I didn't catch that. Yes or no?")
                    continue
                return  # give up on voice; the UI modal / 30 s timeout takes over
            if not registry.has_pending:
                return  # already answered elsewhere
            registry.resolve_latest(answer == "yes")
            log.info("confirmation answered by voice: %s", answer)
            await bus.emit("confirmation_answered", approved=(answer == "yes"), source="voice")
            try:
                await self.speak_line("Okay." if answer == "yes" else "Cancelled.")
            except Exception:
                log.warning('could not speak the answer to a question', exc_info=True)
            return

    async def _listen_yes_no(self, timeout: float = 8.0) -> str | None:
        """Capture one short utterance and classify it as 'yes'/'no' (or None). Runs during
        WAITING, when no other task is consuming the mic."""
        from audio.vad import StreamingVAD
        vad = StreamingVAD(threshold=0.5)   # "no." is ~0.4 s: don't miss it
        q = mic.subscribe()
        buf: list = []
        speech = 0
        last_speech: float | None = None
        t0 = time.time()
        try:
            mic.drain_queue(q)
            while time.time() - t0 < timeout:
                try:
                    block = await asyncio.wait_for(q.get(), timeout=0.4)
                except asyncio.TimeoutError:
                    if last_speech and time.time() - last_speech > 0.7 and speech >= MIN_SPEECH_FRAMES:
                        break
                    continue
                buf.append(block)
                probs = vad.feed(block)
                if any(p >= vad.threshold for p in probs):
                    speech += sum(1 for p in probs if p >= vad.threshold)
                    last_speech = time.time()
                elif last_speech and time.time() - last_speech > 0.7 and speech >= MIN_SPEECH_FRAMES:
                    break
        finally:
            mic.unsubscribe(q)
        if speech < 2 or not buf:      # a one-word answer can be 2 frames
            return None
        import numpy as _np
        text = (await stt.transcribe(_np.concatenate(buf)) or "").strip()
        text = WAKE_PHRASE.sub("", text, count=1).strip()
        log.info("yes/no heard: %r", text[:40])
        if YES_WORDS.match(text):
            return "yes"
        if NO_WORDS.match(text):
            return "no"
        return None

    async def confirmation_answered(self) -> None:
        if self.sm.state == State.WAITING:
            await self.sm.to(State.EXECUTING, force=True)

    async def speak_line(self, line: str) -> None:
        """Speak one line immediately (outside the normal turn queue)."""
        cancel = self._speak_cancel if self._speak_cancel is not None else asyncio.Event()
        await bus.emit("assistant_delta", text=line + " ")
        if self.remote_turn:
            return   # the text reaches the phone; nobody is in the room to hear it
        try:
            async for chunk in tts.synthesize_stream(clean_for_speech(line), cancel):
                if cancel.is_set():
                    break
                await speaker.play_chunk(chunk, tts.sample_rate)
        except SpeakerStalled as e:
            # a dead output device must never propagate into a turn (it once froze one)
            log.error("line not spoken: %s", e)
            await bus.emit("error", summary=f"audio output stalled: {e}")

    async def try_voice_confirmation(self, text: str) -> bool:
        """If a confirmation is pending and the user just said a bare yes/no, answer it."""
        if not registry.has_pending or not text:
            return False
        if YES_WORDS.match(text):
            approved = True
        elif NO_WORDS.match(text):
            approved = False
        else:
            return False
        registry.resolve_latest(approved)
        log.info("confirmation answered by voice: %s", "yes" if approved else "no")
        await bus.emit("confirmation_answered", approved=approved, source="voice")
        try:
            await self.speak_line("Okay." if approved else "Cancelled.")
        except Exception:
            # Silence after a confirmation is the worst case of all: he does not
            # know whether the thing he approved actually happened.
            log.warning('could not confirm the decision aloud', exc_info=True)
        return True

    async def _reflex_turn(self, text: str, reflex, t_start: float) -> None:
        """Handle a request JARVIS recognized himself: tool + templated speech, no LLM."""
        skill, args, conf = reflex
        brain.stats["reflex"] += 1
        await bus.emit("reflex", skill=skill.name, tool=skill.tool, args=args,
                       confidence=conf, mode="direct")
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        if skill.name == "ui":
            await bus.emit("ui", **args)
            reply = skill.speak(args, {})
            self.metrics.mark("first_token_ms")
            await bus.emit("assistant_delta", text=reply)
            await queue.put(clean_for_speech(reply))
        elif skill.name == "teach":
            reply = await self._teach(args, queue)
        elif skill.name == "correction":
            reply = await self._correct(args, queue, t_start)
            if reply is None:
                return  # the correction re-ran as a fresh turn, which finished itself
        else:
            self._last_reflex = dict(brain.last_match or {})
            self._last_reflex_at = time.time()
            reply = await self._exec_skill(skill, args, queue)
        await self._finish_reflex(text, reply, t_start, skill.name, queue, task)

    async def _routine_turn(self, text: str, steps: list[dict], t_start: float) -> None:
        """A phrase the user taught: run its steps in order, one spoken line each."""
        from brain.skills import SKILL_BY_NAME
        brain.stats["reflex"] += 1
        await bus.emit("reflex", skill="command", tool=None, args={"steps": steps},
                       confidence=1.0, mode="routine")
        self._last_reflex = dict(brain.last_match or {})
        self._last_reflex_at = time.time()
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        said = []
        for st in steps:
            sk = SKILL_BY_NAME.get(st.get("skill", ""))
            if sk is None or self._speak_cancel.is_set():
                continue
            if said:
                await bus.emit("assistant_delta", text=" ")
            said.append(await self._exec_skill(sk, st.get("args", {}), queue))
        await self._finish_reflex(text, " ".join(said), t_start, "command", queue, task)

    async def _compile_steps(self, action: str) -> tuple[list[dict], str | None]:
        """'mute and open spotify' -> [{skill, args}, ...]; returns (steps, unknown part)."""
        parts = [p.strip(" ,.") for p in re.split(r"\s*(?:,\s*|\band then\b|\bthen\b|\band\b)\s*", action) if p.strip(" ,.")]
        steps: list[dict] = []
        for part in parts:
            d = await brain.decide(part)
            if not d or d[0].name in ("teach", "correction") or (
                    d[0].llm_after and not (d[0].direct_if and d[0].direct_if(part))):
                return steps, part
            steps.append({"skill": d[0].name, "args": d[1]})
        return steps, None

    async def _teach(self, args: dict, queue: asyncio.Queue) -> str:
        from brain.skills import SKILL_BY_NAME
        phrase, action = args["phrase"], args["action"]
        steps, unknown = await self._compile_steps(action)
        if unknown is not None:
            reply = f"I don't know how to do '{unknown}' on my own yet, so I didn't save that."
        else:
            await brain.teach_command(phrase, steps)
            names = [SKILL_BY_NAME[st["skill"]].label if st["skill"] in SKILL_BY_NAME else st["skill"]
                     for st in steps]
            what = " and ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " and " + names[-1]
            reply = f"Got it. When you say '{phrase}', I'll {what}."
            await bus.emit("brain_learned", text=phrase, skill="command", examples=brain.example_count)
        self.metrics.mark("first_token_ms")
        await bus.emit("assistant_delta", text=reply)
        await queue.put(clean_for_speech(reply))
        return reply

    async def _correct(self, args: dict, queue: asyncio.Queue, t_start: float) -> str | None:
        """'No, I meant X' right after a reflex: un-learn what misfired, then do X."""
        last = getattr(self, "_last_reflex", None)
        # only un-learn if a reflex actually fired in the last ~40 s; a stray "no ..."
        # out of nowhere must not silently delete a learned example
        recent = (time.time() - getattr(self, "_last_reflex_at", 0)) < 40
        dropped = await brain.unlearn(last) if recent else None
        self._last_reflex = None
        if dropped:
            await bus.emit("brain_learned", text=f"forgot: {last.get('text')}", skill=dropped,
                           examples=brain.example_count)
        rest = (args.get("rest") or "").strip()
        if rest:
            # LEARN THE LESSON, not merely forget the mistake. unlearn() above
            # deletes the wrong association; without this nothing takes its
            # place, so the same sentence misfires again and he corrects it
            # again. His words: "if I say flip it upside down and it only turns
            # it to the right and then I correct him ... let him learn."
            original = (last or {}).get("query") or ""
            if original and recent:
                try:
                    d = await brain.decide(rest, context=_screen_context())
                    if d and d[0].name not in ("correction", "teach"):
                        if await brain.learn(original, d[0].name, source="user"):
                            await bus.emit("brain_learned",
                                           text=f"learned: {original}",
                                           skill=d[0].name,
                                           examples=brain.example_count)
                except Exception:
                    # A lesson that cannot be stored must never cost him the
                    # correction itself.
                    log.debug("could not learn from the correction", exc_info=True)
            ack = "Sorry."
            await bus.emit("assistant_delta", text=ack + " ")
            await queue.put(ack)
            await queue.put(None)
            # run the corrected request as a normal turn (LLM if the brain isn't sure)
            await self._converse(rest, t_start)
            return None
        reply = "Sorry about that. What did you want?"
        self.metrics.mark("first_token_ms")
        await bus.emit("assistant_delta", text=reply)
        await queue.put(clean_for_speech(reply))
        return reply

    async def _llm_with_tools(self, messages: list[dict],
                              speak_queue: asyncio.Queue) -> str:
        """Run the LLM, executing tool calls in a loop, streaming sentences to TTS."""
        full_text = ""
        empty_retries = 0
        user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        # the message carries a bracketed context note ("current time", ...) above the
        # utterance: only the user's own words decide whether a search is mandatory
        raw_user = user_text.split(chr(10))[-1] if user_text else ""
        # Offer the tools this turn could plausibly need instead of all sixty: fewer
        # schema tokens before the first word, and fewer wrong things to reach for.
        # Any tool this turn has ALREADY used stays on the list so a follow-up round
        # can call it again.
        already = {tc["function"]["name"] for m in messages
                   for tc in (m.get("tool_calls") or [])}
        tools = await shortlist.pick(registry, raw_user or user_text, keep=already)
        must_use_tool = bool(SEARCH_INTENT.search(raw_user or ""))
        if getattr(self, "_no_tools_first", False):
            must_use_tool = False   # the brain already ran the tool; the model only composes
        used_tools: list[tuple[str, bool]] = []   # (name, ok) - for self-training
        # for the follow-up rule (see strip_repeat): what he was told last time
        from brain.skills import strip_repeat
        prev_reply = next((str(m.get("content") or "") for m in reversed(self._history)
                           if m.get("role") == "assistant"), "")
        spoke_any = False
        held_repeat: str | None = None
        dropped_repeat: str | None = None
        lead_cut: tuple[str, str] | None = None
        for _round in range(8):
            round_text = ""
            pending = ""
            tool_calls: list[dict] | None = None
            # generous budget: gpt-oss spends tokens on hidden reasoning first —
            # a tight cap silently starves the spoken reply (see Houston notes)
            cancelled = False
            # first round of an explicit search/lookup request: a tool call is required
            choice = "required" if (must_use_tool and _round == 0) else None
            if _round == 0 and getattr(self, "_no_tools_first", False) and not must_use_tool:
                choice = "none"
            # "none" isn't honoured reliably by llama-server's template: omit the tools instead
            round_tools = None if choice == "none" else tools
            sampling = ({"temperature": CREATIVE_TEMPERATURE}
                        if CREATIVE_INTENT.search(raw_user or "") else None)
            # TWO PROMPT SHAPES, TWO CACHES. A turn the brain already answered
            # ("who directed jaws" is a general-knowledge reflex) goes to the
            # model WITHOUT the tools block, and a prompt without the block
            # shares nothing past the system prompt with one that has it — on
            # one slot the two shapes evicted each other, and every switch
            # re-read ~5k tokens (21-22 s, measured on release 20 while every
            # same-shape turn took 1.5-3.5 s). The no-tools shape lives on the
            # side slot, where it keeps its own prefix.
            self.metrics.mark("llm_sent_ms")
            async for chunk in local_llm.stream(messages, tools=round_tools, max_tokens=4096,
                                                tool_choice=None if choice == "none" else choice,
                                                sampling=sampling,
                                                slot=0 if round_tools is not None else 1):
                if self._speak_cancel.is_set():
                    # user interrupted: stop generating AND stop streaming to the UI
                    cancelled = True
                    break
                if chunk.text:
                    self.metrics.mark("first_token_ms")
                    ft = getattr(self, "_first_token", None)
                    if ft is not None:
                        ft.set()
                    pending += chunk.text
                    round_text += chunk.text
                    full_text += chunk.text
                    await bus.emit("assistant_delta", text=chunk.text)
                    # flush complete sentences to TTS
                    while True:
                        m = SENTENCE_END.search(pending)
                        if not m:
                            break
                        sentence = pending[: m.end()].strip()
                        pending = pending[m.end():]
                        if sentence and not BARE_HONORIFIC.match(sentence):
                            # A FOLLOW-UP NEVER REPEATS THE LAST ANSWER. "And
                            # Chile?" came back "Lima. Santiago." (measured
                            # 2026-09-05, and a prompt rule changed nothing):
                            # a first sentence that IS the previous answer is
                            # held until a second one proves there is more.
                            if held_repeat is not None:
                                log.info("dropped a repeated first sentence: %r", held_repeat)
                                dropped_repeat = held_repeat
                                held_repeat = None
                            if not spoke_any:
                                raw_first = sentence
                                sentence, only_repeat = strip_repeat(sentence, prev_reply)
                                if only_repeat:
                                    held_repeat = sentence
                                    continue
                                if sentence != raw_first:
                                    # "Lima, Santiago, sir." -> "Santiago, sir." for the
                                    # ear; the transcript must say the same thing
                                    lead_cut = (raw_first, sentence)
                            spoke_any = True
                            await speak_queue.put(clean_for_speech(sentence))
                if chunk.done:
                    tool_calls = chunk.tool_calls
                    break
            if cancelled:
                return full_text
            tail = pending.strip()
            if tail and not BARE_HONORIFIC.match(tail):
                if held_repeat is not None:
                    log.info("dropped a repeated first sentence: %r", held_repeat)
                    dropped_repeat = held_repeat
                    held_repeat = None
                if not spoke_any:
                    raw_tail = tail
                    tail, _only = strip_repeat(tail, prev_reply)
                    if tail != raw_tail:
                        lead_cut = (raw_tail, tail)
                spoke_any = True
                await speak_queue.put(clean_for_speech(tail))
            elif held_repeat is not None:
                # the whole reply WAS the repeat - "repeat that" - say it
                spoke_any = True
                await speak_queue.put(clean_for_speech(held_repeat))
                held_repeat = None
            if dropped_repeat and full_text.strip().startswith(dropped_repeat):
                full_text = full_text.strip()[len(dropped_repeat):].strip()
            if lead_cut and lead_cut[0] in full_text:
                full_text = full_text.replace(lead_cut[0], lead_cut[1], 1)

            if not tool_calls:
                if not round_text.strip() and empty_retries < 1:
                    # empty round (reasoning ate the budget) — nudge once
                    empty_retries += 1
                    log.warning("empty LLM round — retrying with a nudge")
                    messages.append({"role": "user", "content":
                                     "(Continue: finish the task or answer now, "
                                     "in one or two spoken sentences.)"})
                    continue
                if not full_text.strip():
                    # never end a turn in silence
                    fallback = "I'm afraid I lost that. Would you say it again?"
                    await bus.emit("assistant_delta", text=fallback)
                    await speak_queue.put(clean_for_speech(fallback))
                    return fallback
                await self._maybe_learn(raw_user, used_tools)
                return full_text

            # execute tools, then loop for the model's follow-up
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls],
            })
            for tc in tool_calls:
                state = (State.SEARCHING
                         if tc["name"] in ("web_search", "research", "fetch_page")
                         else State.EXECUTING)
                await self.sm.to(state, force=True)
                result = await registry.execute(tc["name"], tc["arguments"])
                last_seen.note_result(result.get("result"))
                link_ledger.note(result)
                used_tools.append((tc["name"], bool(result.get("ok"))))
                if result.get("declined") or result.get("unconfirmed"):
                    # the user said no (or nothing): acknowledge and stop - never re-ask
                    line = "Alright, leaving it." if result.get("declined") else "I didn't get a yes, so I left it alone."
                    await bus.emit("assistant_delta", text=line)
                    await speak_queue.put(clean_for_speech(line))
                    return (full_text + " " + line).strip()
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                })
            await self.sm.to(State.THINKING, force=True)
        return full_text

    async def _maybe_learn(self, user_text: str, used_tools: list[tuple[str, bool]]) -> None:
        """Self-training: a turn the LLM solved with exactly one successful known tool
        teaches the brain that phrasing -> skill, so next time it's a reflex."""
        if len(used_tools) != 1 or not used_tools[0][1]:
            return
        skill = brain.learned_from_tool(used_tools[0][0])
        if not skill or not user_text.strip():
            return
        if not _teachable(user_text):
            # Not everything the microphone hears is a command being given, and
            # a reflex learned from something that was not one is permanent.
            log.info("not learning %r -> %s: it does not look like a command",
                     user_text[:60], skill)
            return
        try:
            if await brain.learn(user_text, skill):
                await bus.emit("brain_learned", text=user_text, skill=skill,
                               examples=brain.example_count)
        except Exception:
            log.exception("brain learn failed")

    # How far synthesis may run ahead of the speakers. Bounded so a long reply
    # cannot be synthesized in full into memory, but deep enough that the next
    # sentence is always ready before the current one stops playing.
    _SYNTH_LOOKAHEAD = 8

    async def _speaker_worker(self, queue: asyncio.Queue) -> None:
        """Consumes sentences, synthesizes and plays them; watches for barge-in.

        Synthesis runs AHEAD of playback. It used to be strictly serial —
        synthesize a sentence, play it, and only then start synthesizing the
        next — so every sentence boundary in a multi-sentence reply was dead
        air, about 0.6-0.9 s of it, while Kokoro worked in a silence he was
        sitting through. A three-sentence answer paid that twice.

        Now a producer fills a bounded audio queue while the consumer plays
        from it, so the next sentence is already waiting when the current one
        ends. First-audio latency is unchanged — nothing can be prefetched
        before the first sentence exists — but the gaps between sentences close.
        """
        barge_task = asyncio.create_task(self._barge_in_watch())
        spoke = False
        tts.idle.clear()      # hold off the background phrase warm until he has finished
        # (sentence, chunk, is_first) — is_first marks a sentence boundary for the
        # consumer, which is where the HUD event and the state change belong.
        audio_q: asyncio.Queue = asyncio.Queue(maxsize=self._SYNTH_LOOKAHEAD)

        async def produce() -> None:
            try:
                while True:
                    sentence = await queue.get()
                    if sentence is None:
                        break
                    if self._speak_cancel.is_set():
                        continue
                    if self.remote_turn:
                        continue   # remote turn: text goes to the phone, not the speakers
                    first = True
                    t_sent = time.time()
                    try:
                        async for chunk in tts.synthesize_stream(sentence,
                                                                 self._speak_cancel):
                            if self._speak_cancel.is_set():
                                break
                            if first:
                                # Where the reply's first sound actually waits:
                                # this line and the "to speaker" one below are
                                # the two halves of first_audio_ms.
                                log.info("speak: first chunk of %r after %d ms",
                                         sentence[:32], int((time.time() - t_sent) * 1000))
                            await audio_q.put((sentence, chunk, first, t_sent))
                            first = False
                    except Exception:
                        log.exception("synthesis failed for %r", sentence[:60])
                    if self._speak_cancel.is_set():
                        break
            finally:
                await audio_q.put(None)          # always release the consumer

        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await audio_q.get()
                if item is None:
                    break
                sentence, chunk, first, t_sent = item
                if self._speak_cancel.is_set():
                    break
                if first:
                    if not spoke or self.sm.state != State.SPEAKING:
                        await self.sm.to(State.SPEAKING, force=True)
                        spoke = True
                    await bus.emit("speaking", text=sentence)
                    self._saying_own_name = "jarvis" in sentence.lower()
                    log.info("speak: %r to the speaker after %d ms",
                             sentence[:32], int((time.time() - t_sent) * 1000))
                try:
                    self.metrics.mark("first_audio_ms")
                    await speaker.play_chunk(chunk, tts.sample_rate)
                except SpeakerStalled as e:
                    # the device died mid-sentence: say so on screen, drop the rest of
                    # the speech, and let the turn FINISH — silence beats a frozen JARVIS
                    log.error("speech aborted: %s", e)
                    await bus.emit("error", summary=f"audio output stalled: {e}")
                    break
        finally:
            producer.cancel()
            # Drain whatever was synthesized ahead: on a barge-in that audio is
            # already stale, and leaving it queued would speak it over the next turn.
            while not audio_q.empty():
                try:
                    audio_q.get_nowait()
                except asyncio.QueueEmpty:
                    break
            tts.idle.set()
            barge_task.cancel()
            self._saying_own_name = False
            # own-name echo can linger in the wake model's window: reset before idle listening
            try:
                wake.reset()
            except Exception:
                # His own name can stay in the model's window after he speaks,
                # and the next "hey JARVIS" is swallowed by the echo.
                log.warning("wake reset after speaking failed", exc_info=True)

    async def _barge_in_watch(self) -> None:
        """While speaking, watch for the user cutting in.

        interrupt.mode = "wake_word" (default): only his name interrupts him —
        immune to his own voice bleeding from speakers into the mic.
        interrupt.mode = "any_speech": any sustained speech interrupts
        (needs a headset or good echo isolation).
        """
        vad = StreamingVAD(threshold=0.75)
        consec = 0
        q = mic.subscribe()
        try:
            while True:
                block = await q.get()
                if self._speak_cancel.is_set():
                    consec = 0
                    continue
                mode = config.get("interrupt", "mode", default="wake_word")
                fired = False
                if mode == "any_speech":
                    probs = vad.feed(block)
                    consec = (consec + sum(1 for p in probs if p >= vad.threshold)
                              if any(p >= vad.threshold for p in probs) else 0)
                    fired = consec >= 6
                else:
                    score = await asyncio.to_thread(wake.feed, block)
                    fired = score >= wake.threshold
                    if fired and getattr(self, "_saying_own_name", False):
                        # that was him saying "Jarvis", bleeding from the speakers
                        log.info("ignored own name in speech (score %.2f)", score)
                        wake.reset()
                        fired = False
                if fired:
                    log.info("barge-in detected (%s)", mode)
                    self._speak_cancel.set()
                    speaker.abort()
                    wake.reset()
                    await self.sm.to(State.INTERRUPTED, force=True)
                    await bus.emit("interrupted", reason="barge-in")
                    mic.drain()
                    self.vad.reset()
                    self._preroll = None
                    self._listen_flag.set()  # capture what the user is saying
                    self._newer_turn_started()
                    await self.sm.to(State.LISTENING, force=True)
                    await self.play_sound("chime")  # same cue: 'listening now'
                    return
        except asyncio.CancelledError:
            raise
        finally:
            mic.unsubscribe(q)


orchestrator = Orchestrator()
