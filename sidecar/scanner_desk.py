"""The scanner desk: his police scanner, heard and judged.

Why it exists (2026-11-07): in eleven days he spoke to JARVIS five times and
ignored 94 unprompted Telegram messages, because almost none of them were
local or actionable. What he asked for instead: "Framingham PD and FD,
Sudbury, Acton, Newton, Wellesley, Boston, Maynard, Natick, Marlborough,
Hudson, Waltham. Basically info within a 10-15-20 mile radius of me."

The radio itself belongs to his own app, BC125AT Console, which owns the
scanner's COM port, logs every transmission and records each as a 16 kHz
WAV. It runs headless with `--serve` on 127.0.0.1:8125. This desk:

  * keeps it running - starts the installed exe hidden when it does not
    answer, at most once per few minutes, OUTSIDE JARVIS's process tree so
    JARVIS closing (taskkill /T) never takes the scanner with it;
  * follows its feed of finished transmissions about once a second, from a
    cursor that survives restarts, and never chews through hours of backlog:
    anything older than ~10 minutes is counted, not transcribed;
  * transcribes analog traffic with JARVIS's own Parakeet - only when he is
    not speaking, never while one of his turns is being heard, in short
    pieces so his voice never waits behind a radio clip - and posts the
    words back to the console;
  * builds incidents from the words (scanner/rules.py) and, with no words at
    all, from how busy a fire channel suddenly is compared with its own
    history for that hour;
  * interrupts him only for active danger close to him, through
    delivery.deliver with a stable key per incident, inside quiet hours and
    a daily cap per tier, and logs every decision that reaches him with why;
  * tells him every other real call in his radius quietly (FYI, below): one
    silent line on his phone, or one sentence at the PC when he is idle.

Measured on 2026-11-07: the audio reaching the PC is weak (~-35 dBFS) and
some channels (Newton PD, Newton FD 1) are digital, which no transcriber
reads - so the desk has to be useful from metadata alone and better with
words.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from collections import Counter, deque
from functools import lru_cache
from pathlib import Path

import numpy as np

import geo
from config import config
from delivery import ALERT, URGENT
from events import bus, spawn
from scanner import factcheck, rules, store, tones
from scanner.client import ConsoleClient

log = logging.getLogger("jarvis.scanner")

RATE = 16000
CURSOR_KEY = "scanner:cursor"
SENT = ("spoken", "telegram")
# THE QUIET FEED (2026-11-07, his words after four hours of nothing: "I haven't
# gotten anything from Jarvis yet?! ... No way there hasn't been anything in
# over an hour"). The desk heard 400 transmissions and built two crashes, two
# fire alarms and a busy spell, and told him none of it, because only active
# danger interrupted. A real call in his radius - a crash, a reported fire, a
# gas leak, a break-in - now reaches him as one line: silent on his phone,
# said at the PC only when he is idle, never cutting into what he is doing.
FYI = "fyi"
_RANK = {"": 0, "notable": 1, FYI: 1, ALERT: 2, URGENT: 3}
_DAILY_CAP = {URGENT: 4, ALERT: 8, FYI: 80}       # FYI raised with medical and police calls
SAID_KEEP = 10            # transmissions kept per incident: the dispatch and its follow-ups
# Characters kept of each transmission. Was 300: the Wayland lookout's "back to
# Framingham" fell past it and the start-up re-read could not correct the town.
SAID_TEXT_MAX = 1000


def _cut_at(said_row: dict) -> int:
    """How long a kept line can be before it was cut: rows say so since
    SAID_TEXT_MAX; older rows were cut at 300."""
    return int(said_row.get("max") or 300)


def _union(old: list, new: list, cap: int) -> list:
    """old + whatever in new is not already there, order kept, at most cap."""
    return list(dict.fromkeys(list(old) + list(new)))[:cap]


# CAN I ACTUALLY HEAR IT? From 10:06 PM on 2026-11-07 the scanner's sound did
# not reach the PC - unplugged for the night, then back at volume 3 instead of
# 15 - and for 19 hours nothing said so: the console showed every transmission,
# health said "the audio level is fine", the morning summary said "nothing
# overnight". Hearing is judged from what the transmissions gave: a long run
# of them without one understood word is deafness. Calibrated on his real
# day (2026-11-07, 1,800 transmissions): the longest wordless run while
# everything worked was 40 and the longest run with no recording at all was
# 9; the outage reached both thresholds within 25 minutes of his plugging in.
DEAF_WORDLESS = 80        # this many in a row and not one word understood
DEAF_UNRECORDED = 25      # ...or this many in a row with no sound at all
DEAF_SETTLE_S = 180       # the newest few may not be transcribed yet
DEAF_KEY = "scanner:deaf"
NIGHT_GAP_S = 7200        # two hours with nothing logged at all is said, not assumed quiet
LOST_AFTER_S = 600        # console silent, or scanner disconnected, this long before he is told
GOOD_VOLUME = 15          # every transmission understood on 2026-11-07 was recorded at 15

# THE SCAN DOCTOR DELETED FRAMINGHAM FIRE (2026-11-08, 10:40 AM), his own town's
# priority channel, while the scanner was deaf at volume 3: every clip was
# "unknown", so it was "never understandable under any setting tried". Nobody
# was told. A house fire in Framingham on 11-10 (3 displaced, 1 to hospital)
# reached him as a line in the evening news, and the audit of 11-12 found four
# days without one Framingham transmission. So: the doctor is paused while I
# can't hear, he is told what it removes, and a home town with no fire channel
# (or one gone silent while the rest of the scanner is busy) is said once a day.
DOCTOR_EVERY_S = 1800
DOCTOR_SEEN_KEY = "scanner:doctor-seen"
DOCTOR_PAUSED_KEY = "scanner:doctor-paused"
HOME_GAP_KEY = "scanner:home-gap"
HOME_SILENT_S = 36 * 3600     # Framingham Fire logged about 19 transmissions a day
HOME_SILENT_OTHERS = 300      # ...and this many elsewhere proves the scanner was listening


def doctor_removed_text(removed: list[dict], home: str) -> str:
    """What the scan doctor took off the scanner, home town first."""
    items = []
    for c in removed:
        b = c.get("before") or {}
        lab = rules.parse_label(str(b.get("name") or "").strip())
        why = re.sub(r"\s+", " ", str(c.get("why") or "")).strip().rstrip(".")
        items.append((0 if lab.town and lab.town.name == home else 1, lab.spoken, why[:90]))
    items.sort(key=lambda x: x[0])
    said = [f"{name} ({why})" if why else name for _, name, why in items[:4]]
    more = len(items) - len(said)
    if len(items) == 1:
        text = f"The scan doctor took {items[0][1]} off the scanner, sir" + (f": {items[0][2]}." if items[0][2] else ".")
    else:
        text = (f"The scan doctor took {len(items)} channels off the scanner, sir: " + "; ".join(said)
                + (f"; and {more} more." if more > 0 else "."))
    if items[0][0] == 0:
        text += f" {items[0][1]} is your own town - I can no longer hear it until it is put back."
    return text


def home_fire_channels(channels: list[dict], home: str) -> list[str]:
    """The names of the scanner's channels that are his own town's fire department."""
    out = []
    for c in channels or []:
        if not isinstance(c, dict) or not c.get("freq") or c.get("lockout"):
            continue
        lab = rules.parse_label(str(c.get("name") or "").strip())
        if lab.town and lab.town.name == home and lab.agency == "FD":
            out.append(str(c.get("name") or "").strip())
    return out


def deaf_spans(hits: list[dict]) -> list[dict]:
    """The stretches in `hits` (oldest first) when the radio was carrying
    traffic and none of it reached me as words: [{start, end, n, recorded,
    silent}]. `silent` means a long run arrived with no sound at all (a
    cable out, a volume of 0); otherwise the sound was too faint or garbled.
    Judged by the run of no sound, not the total: on 2026-11-08 a stretch
    began with nine faint recordings at 10 PM, then nothing at all."""
    spans, run, unrec, worst = [], [], 0, 0

    def close():
        if len(run) >= DEAF_WORDLESS or worst >= DEAF_UNRECORDED:
            spans.append({"start": float(run[0]["ts"]), "end": float(run[-1].get("end_ts") or run[-1]["ts"]),
                          "n": len(run), "recorded": sum(1 for h in run if h.get("audio_path")),
                          "silent": worst >= DEAF_UNRECORDED})

    for h in hits:
        if (h.get("transcript") or "").strip():
            if run:
                close()
            run, unrec, worst = [], 0, 0
            continue
        run.append(h)
        unrec = 0 if h.get("audio_path") else unrec + 1
        worst = max(worst, unrec)
    if run:
        close()
    return spans


def _quiet_gaps(hits: list[dict], start: float, end: float) -> list[tuple[float, float]]:
    """Stretches of at least NIGHT_GAP_S inside [start, end] with nothing
    logged at all - a scanner off, unplugged or disconnected looks like this."""
    marks = [start] + [float(h["ts"]) for h in hits] + [end]
    return [(a, b) for a, b in zip(marks, marks[1:]) if b - a >= NIGHT_GAP_S]


def _hearing_note(spans: list[dict]) -> str:
    """What I could not hear while the radio was busy, or ''. Only traffic
    without words counts: that is known deafness, not a guess."""
    bits = []
    for s in spans:
        why = "no sound reached the PC" if s["silent"] else "the sound was too faint to make out"
        bits.append(f"from {_hm(s['start'])} to {_hm(s['end'])} ({why})")
    return ("I couldn't hear the scanner " + "; and ".join(bits) + ", so this is incomplete.") if bits else ""


def _gap_note(gaps: list[tuple[float, float]]) -> str:
    """Long stretches with nothing logged, said as what they are: a quiet
    radio and a scanner that was off look the same from here."""
    if not gaps:
        return ""
    return ("Nothing at all came through " + " or ".join(f"from {_hm(a)} to {_hm(b)}" for a, b in gaps)
            + " - a quiet radio, or the scanner was off or unplugged.")
CONSOLE_EXE_NAME = "bc125at console.exe"


def _cfg(key: str, default):
    return config.get("scanner", key, default=default)


@lru_cache(maxsize=1)
def home_town() -> geo.Town:
    """The town he lives in: the one nearest home (Framingham)."""
    return min(geo.towns().values(), key=lambda t: t.miles)


def miles_words(m: float | None) -> str:
    if m is None:
        return "somewhere nearby"
    if m < 1:
        return "under a mile away"
    r = int(round(m))
    return "a mile away" if r == 1 else f"{r} miles away"


def _hm(ts: float) -> str:
    t = dt.datetime.fromtimestamp(ts)
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 45:
        return "a few seconds ago" if s < 10 else f"{s} seconds ago"
    if s < 90:
        return "about a minute ago"
    if s < 3600:
        return f"{int(round(s / 60))} minutes ago"
    h = s / 3600
    return "about an hour ago" if h < 1.5 else f"about {int(round(h))} hours ago"


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _place(address: str) -> str:
    """' at 45 Union Avenue', ' at Concord Street and Waverly Street', but
    ' on Route 9 eastbound at Speen Street' and ' on Moody Street' - for the voice."""
    if not address:
        return ""
    said = rules.spoken_address(address)
    return f" at {said}" if re.match(r"\d", address) or " and " in address else f" on {said}"


# ---------------------------------------------------------------- audio
def decode_wav(data: bytes) -> np.ndarray | None:
    """16-bit PCM WAV bytes -> 16 kHz mono float32 in [-1, 1]."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
    except Exception:
        return None
    if width != 2 or not raw:
        return None
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != RATE and len(x) > 1:
        t_new = np.arange(0, len(x) * RATE / rate) * (rate / RATE)
        x = np.interp(t_new, np.arange(len(x)), x).astype(np.float32)
    return x


def normalise_audio(x: np.ndarray, target: float = 0.7, max_gain_db: float = 45.0) -> tuple[np.ndarray | None, float]:
    """Peak-normalise a clip: the console's input peaks around -35 dBFS, far
    below what a recogniser was trained on. (None, peak dBFS) for silence."""
    if x is None or len(x) == 0:
        return None, -120.0
    x = x - float(np.mean(x))
    peak = float(np.max(np.abs(x)))
    db = 20 * np.log10(peak) if peak > 0 else -120.0
    if peak < 10 ** (-60 / 20):
        return None, db
    gain = min(target / peak, 10 ** (max_gain_db / 20))
    return (x * gain).astype(np.float32), db


def split_audio(x: np.ndarray, max_s: float = 8.0) -> list[np.ndarray]:
    """Pieces of at most ~max_s seconds, cut at the quietest 50 ms near each
    boundary, so the recogniser's lock is never held long enough to make one
    of his turns wait."""
    n = len(x)
    if n <= int(max_s * 1.25 * RATE):
        return [x]
    out, start, hop = [], 0, int(0.05 * RATE)
    while n - start > int(max_s * 1.25 * RATE):
        target = start + int(max_s * RATE)
        lo, hi = max(start + RATE, target - int(1.5 * RATE)), min(n - RATE, target + int(1.5 * RATE))
        best, best_e = target, None
        for i in range(lo, max(lo + 1, hi - hop), hop):
            e = float(np.mean(x[i:i + hop] ** 2))
            if best_e is None or e < best_e:
                best, best_e = i + hop // 2, e
        out.append(x[start:best])
        start = best
    out.append(x[start:])
    return out


def write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- the console process
def _spawn_console(exe: str) -> bool:
    """Start the console headless, hidden, and OUTSIDE JARVIS's process tree.

    The Tauri shell stops the sidecar with `taskkill /F /T`, which kills every
    descendant - a console started as our child would die with every JARVIS
    restart and take his scanner log with it. `cmd /c start` launches it and
    exits at once, so the console's parent is gone and it belongs to nobody.
    It is a windowed build, so `--serve` shows nothing; cmd itself runs with
    CREATE_NO_WINDOW. A second --serve exits 0 by itself (single instance)."""
    try:
        p = subprocess.Popen(["cmd.exe", "/d", "/c", "start", "", "/b", exe, "--serve"],
                             cwd=os.path.dirname(exe) or None, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        p.wait(timeout=15)
        return p.returncode == 0
    except Exception:
        log.warning("scanner: could not start the console", exc_info=True)
        return False


def _console_process_running() -> bool:
    """Is BC125AT Console already running (perhaps as his desktop window)?"""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            if str(p.info.get("name") or "").lower() == CONSOLE_EXE_NAME:
                return True
    except Exception:
        log.debug("scanner: could not list processes", exc_info=True)
    return False


def _loaded(module: str, attr: str):
    """An app singleton if its module is already loaded; never imports one
    (the desk must not drag the audio stack into a process that lacks it)."""
    mod = sys.modules.get(module)
    return getattr(mod, attr, None) if mod is not None else None


def _voice_busy_default() -> bool:
    """True while any part of HIS voice path is live: a turn being heard,
    thought about or answered, the recogniser in use or wanted, dictation, or
    the two seconds after any of those. Unknown means busy - his voice wins."""
    try:
        stt = _loaded("audio.stt", "stt")
        if stt is None or stt.busy:
            return True
        dictation = _loaded("dictation", "dictation")
        if dictation is not None and dictation.active:
            return True
        orch = _loaded("orchestrator", "orchestrator")
        if orch is None:
            return True
        if str(getattr(orch.sm.state, "value", orch.sm.state)) not in ("idle", "sleeping"):
            return True
        if time.time() - float(orch.sm.changed_at or 0) < 2.0:
            return True
    except Exception:
        return True
    return False


def _remote_turn_default() -> bool:
    orch = _loaded("orchestrator", "orchestrator")
    return bool(getattr(orch, "remote_turn", False)) if orch is not None else False


def _night_window() -> tuple[tuple[int, int], tuple[int, int]] | None:
    """His quiet hours as ((start h, m), (end h, m)), from the proactive
    settings the rest of JARVIS uses; None if they cannot be read."""
    try:
        s = str(config.get("proactive", "quiet_start", default="22:30"))
        e = str(config.get("proactive", "quiet_end", default="05:30"))
        sh, sm = (int(x) for x in s.split(":"))
        eh, em = (int(x) for x in e.split(":"))
        return (sh, sm), (eh, em)
    except (TypeError, ValueError):
        return None


def _in_quiet_hours() -> bool:
    try:
        from delivery import _in_quiet_hours as quiet
        return bool(quiet())
    except Exception:
        return False


# WHAT IS GOING ON, IN HIS WORDS (2026-11-08). He read a call's raw transcript
# on his phone and said: "I don't want the actual transmission. I want Jarvis
# to tell me what's going on." The model writes it; scanner.factcheck then
# cuts every sentence that leans on a word nobody said, because on weak audio
# this model has invented "no injuries reported" and "responders on scene".
# His home town is deliberately NOT in the prompt: it leaked into a summary
# of a Boston call as "near Framingham, Massachusetts" when it was.
# The garble rules are from replaying real calls (2026-11-08): told only "add
# nothing", the model echoed "Rail Party sleeping" word for word and listed a
# licence plate ("Fox, Charlie, Romeo 213") as the units sent.
_RULES_OF_WRITING = """- Use only what the transmissions say. Add nothing: no injuries or "no injuries" unless said, \
no "on scene", no cause, no guess at how serious it is.
- Much of the audio is garbled. Say what the clear parts mean in your own plain words; leave out any \
sentence or name that makes no sense - never copy garbled words, never guess at or correct them.
- Phonetic letters (Alpha, Bravo, Charlie...) and long strings of numbers are plates, case numbers or \
radio codes - leave them out. Units are only named ones like Engine 1, Ladder 2, Car 3 or Ambulance 1.
- Say "someone" unless the transmissions say man, woman, male, female, he or she.
- Never give a person's name or a vehicle's plate, even when the radio does."""

_CALL_FACTS = """Agency: {who} ({town}, {miles})
The rules guessed: {label}{address}{units} (a keyword guess - if the transmissions say something \
different, go by the transmissions)
Transmissions, oldest first (machine transcripts of weak radio audio - expect errors and garbled words):
{said}"""

SUMMARY_PROMPT = """Someone nearby wants to know what is going on in a police or fire radio call. \
Tell them in a text message.
""" + _CALL_FACTS + """
Answer with JSON only, no other text:
{{"summary": "<one or two short plain sentences>", "type": "<2-4 words>", \
"address": "<street address or intersection, or empty>", \
"active": <true, or false if the traffic says it is over, a false alarm or nothing found>}}
The summary says what is happening, where, who was sent, and any detail the transmissions give about \
the people, vehicles or weapons involved - for example "Sudbury Fire sent Car 3 and Ambulance 1 to a \
two-car crash on Route 20; one driver has neck pain."
""" + _RULES_OF_WRITING + """
- No radio codes, no unit numbers, no dates or times (the message already has the time)."""

MORE_PROMPT = """Someone nearby asked what is going on in a police or fire radio call. Tell them \
everything the radio said about it, in a text message.
""" + _CALL_FACTS + """
Answer with JSON only, no other text:
{{"account": "<two to four short plain sentences, in the order it happened>", \
"clear": <true if the transmissions clearly say what happened; false if they are too garbled to make out>}}
Say what happened, where, who was sent and every detail the transmissions give about the people, \
vehicles or weapons involved.
""" + _RULES_OF_WRITING + """
- No radio codes and no dates. Unit names (Engine 1, Ambulance 2) are fine."""

# The background call writes both at once, so "tell me more" is answered from
# what is already written instead of a half-minute wait (replayed 2026-11-08:
# 26-40 s for the account alone, asked after the fact).
BOTH_PROMPT = """Someone nearby wants to know what is going on in a police or fire radio call. \
Tell them in a text message.
""" + _CALL_FACTS + """
Answer with JSON only, no other text:
{{"summary": "<one or two short plain sentences>", \
"account": "<two to four short plain sentences: everything the radio said about it, in the order it happened>", \
"clear": <true if the transmissions clearly say more than the summary does; false if the rest is too garbled \
to make out>, "type": "<2-4 words>", "address": "<street address or intersection, or empty>", \
"active": <true, or false if the traffic says it is over, a false alarm or nothing found>}}
The summary says what is happening, where, who was sent, and any detail the transmissions give about \
the people, vehicles or weapons involved - for example "Sudbury Fire sent Car 3 and Ambulance 1 to a \
two-car crash on Route 20; one driver has neck pain." The account is for when they ask for more: \
every detail, units by name, in order.
""" + _RULES_OF_WRITING + """
- No radio codes, no dates or times (the message already has the time); no unit numbers in the summary."""

RETRY_PROMPT = """These words in your answer are not in the transmissions: {bad}.{negative} \
Write it again using only what the transmissions say - leave out anything they do not support. \
Same JSON."""

# mode -> (prompt, [(field, longest allowed)]); the first field must be there
_MODES = {
    "summary": (SUMMARY_PROMPT, [("summary", 240)]),
    "more": (MORE_PROMPT, [("account", 600)]),
    "both": (BOTH_PROMPT, [("summary", 240), ("account", 600)]),
}

# HIS "TELL ME MORE" (2026-11-08: "if I say okay, tell me more then Jarvis can
# [tell me] according to what's going on"). A bare "ok" stays an
# acknowledgement (remote_telegram.ACK_ONLY answers it with silence).
FOLLOWUP_RE = re.compile(
    r"^\s*(?:(?:and|so|ok|okay|ok then|yes|yeah|yep|sure|please|jarvis|hmm|oh)[,.!]?\s+)*"
    r"(?:(?:please\s+)?tell me more(?:\s+about\s+(?:it|that|this|the call|that call|this call))?"
    r"|(?:any\s+)?more(?:\s+(?:please|info|information|details?))?"
    r"|(?:any\s+)?(?:details|updates?|news)"
    r"|what(?:'?s|\s+is)\s+(?:going on|happening)(?:\s+(?:there|with (?:it|that|this)))?"
    r"|what\s+happened|what(?:'?s|\s+is)\s+(?:it|that|this)(?:\s+about)?"
    r"|go on|say more|keep going|what else|anything else)"
    r"(?:\s+please)?\s*[.!?]*\s*$", re.I)


def parse_summary(out: str, fields: list[tuple[str, int]] | None = None) -> dict | None:
    """The model's JSON, checked; None when there is nothing usable. The first
    of `fields` (default: the summary) must be there; the rest may be empty."""
    fields = fields or [("summary", 240)]
    blobs = re.findall(r"\{[^{}]*\}", out or "", re.S)
    for b in reversed(blobs):
        try:
            d = json.loads(b)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        got = {}
        for key, limit in fields:
            s = re.sub(r"\s+", " ", str(d.get(key) or "")).strip().strip('"')
            got[key] = s if len(s) <= limit else ""
        if not got[fields[0][0]]:
            continue
        act, clear = d.get("active"), d.get("clear")
        got.update({"type": str(d.get("type") or "")[:40], "address": str(d.get("address") or "")[:80],
                    "active": act if isinstance(act, bool) else None,
                    "clear": clear if isinstance(clear, bool) else None})
        return got
    return None


def call_sources(inc: dict) -> tuple[list[str], list[str]]:
    """(what was heard, what the rules found word for word) - all a summary
    of this call may lean on."""
    lab = rules.parse_label(inc.get("channel") or "")
    heard = [str(s.get("text") or "") for s in inc.get("said") or []]
    known = [inc.get("label") or "", inc.get("address") or "", inc.get("town") or "",
             inc.get("channel") or "", inc.get("agency") or "", lab.spoken] \
        + list(inc.get("roads") or []) + list(inc.get("units") or []) + list(inc.get("details") or [])
    return heard, [k for k in known if k]


def checked(text: str, inc: dict) -> str:
    """Only the sentences of `text` that what was heard supports."""
    heard, known = call_sources(inc)
    return factcheck.check(text or "", heard, known)[0]


def call_prompt(template: str, inc: dict) -> str:
    said = "\n".join(f'- [{_hm(s.get("ts", 0))}] "{s.get("text", "")}"' for s in inc.get("said") or []) or "- (none)"
    return template.format(
        who=rules.parse_label(inc.get("channel") or "").spoken, town=inc.get("town") or "?",
        miles=miles_words(inc.get("miles")), label=inc.get("label") or "?",
        address=f"; address {inc['address']}" if inc.get("address") else "",
        units=f"; units {', '.join(inc['units'])}" if inc.get("units") else "", said=said)


async def write_checked(inc: dict, mode: str, ask, budget_s: float) -> dict | None:
    """The model's words for one call, fact-checked, with one second try when
    the check cut something: told which words nothing supports, the model
    usually keeps the fact and drops the guess ("suspect may be armed" ->
    "someone has a knife"). `ask(messages, timeout)` returns the raw output.
    Returns the parsed answer with its text replaced by what survived (""
    when nothing did) and "dropped" listing what was cut; None when the model
    gave nothing usable in time."""
    template, fields = _MODES[mode]
    loop = asyncio.get_running_loop()
    end = loop.time() + budget_s
    messages = [{"role": "user", "content": call_prompt(template, inc)}]
    heard, known = call_sources(inc)

    def judge(got: dict) -> dict:
        return {k: factcheck.check(got.get(k) or "", heard, known) for k, _ in fields}
    try:
        out = await ask(messages, max(1.0, end - loop.time()))
    except asyncio.TimeoutError:
        return None
    got = parse_summary(out, fields)
    if not got:
        return None
    verdict = judge(got)
    cut = [(s, ws) for _, dropped in verdict.values() for s, ws in dropped]
    if cut and end - loop.time() > 4.0:
        bad = sorted({w for _, ws in cut for w in ws if not w.startswith("(")})
        neg = any(w.startswith("(") for _, ws in cut for w in ws)
        messages += [{"role": "assistant", "content": out},
                     {"role": "user", "content": RETRY_PROMPT.format(
                         bad=", ".join(bad) or "(none)",
                         negative=(" Nothing negative (no, not, none) was said." if neg else "")
                         + (" Leave out plate numbers and people's names."
                            if any("leave it out" in w for _, ws in cut for w in ws) else ""))}]
        try:
            got2 = parse_summary(await ask(messages, max(1.0, end - loop.time())), fields)
        except asyncio.TimeoutError:
            got2 = None
        if got2:
            # field by field, whichever try kept more of what was heard
            v2 = judge(got2)
            for k, _ in fields:
                if len(v2[k][0]) > len(verdict[k][0]):
                    verdict[k] = v2[k]
            for flag in ("active", "clear"):
                if got2.get(flag) is not None:
                    got[flag] = got2[flag]
    for k, _ in fields:
        got[k] = verdict[k][0]
    got["dropped"] = [s for _, dropped in verdict.values() for s, _ in dropped]
    return got


# The console's Scan Doctor (1.3.0) records every change as
# {kind, index, before, after, why, backup}; its summary is a dict of counts.
_DOCTOR_VERBS = {     # {n} is "40 channels"
    "remove": "removed {n}", "drop": "dropped {n} as candidates", "add": "added {n} to the trial bank",
    "promote": "promoted {n} from the trial bank", "fix": "re-tuned {n}", "keep": "renamed {n}",
    "trial": "tried new settings on {n}", "restore": "restored {n}",
}


def _doctor_name(c: dict) -> str:
    for side in ("before", "after"):
        d = c.get(side)
        if isinstance(d, dict) and str(d.get("name") or "").strip():
            return str(d["name"]).strip()
    return ""


def _describe_doctor_v1(rep: dict) -> str:
    """The 1.3.0 report, spoken: what it changed, grouped, and where it stands.
    Empty when the report is not in that shape (the generic reader takes over)."""
    changes = rep.get("changes")
    summ = rep.get("summary")
    if not isinstance(changes, list) or not all(isinstance(c, dict) and "kind" in c for c in changes):
        return ""
    groups: dict[str, list[dict]] = {}
    for c in changes:
        groups.setdefault(str(c.get("kind")), []).append(c)
    parts = []
    lvl = groups.pop("level", [])
    for kind in ("remove", "add", "promote", "fix", "keep", "drop", "trial", "restore"):
        cs = groups.pop(kind, [])
        if not cs:
            continue
        names = []
        for c in cs:
            n = re.sub(r"\s+\d+$", "", _doctor_name(c))
            if n and n not in names:
                names.append(n)
        n = f"{len(cs)} channel{'s' if len(cs) != 1 else ''}"
        what = _DOCTOR_VERBS.get(kind, kind + " {n}").format(n=n)
        if names:
            what += ": " + ", ".join(names[:4]) + (f" and {len(names) - 4} more" if len(names) > 4 else "")
        whys = {str(c.get("why") or "").strip() for c in cs if c.get("why")}
        if kind == "remove" and whys:
            # the reasons share a shape ("X is 22 mi from home, outside your radius")
            if all("outside your" in w for w in whys):
                what += ", all outside your radius"
            elif len(whys) == 1:
                what += f", because {next(iter(whys)).rstrip('.')}"
        parts.append(what)
    for c in groups.values():                     # a kind this desk does not know yet
        if c:
            parts.append(f"{len(c)} other change{'s' if len(c) != 1 else ''}")
    if lvl:
        after = lvl[-1].get("after") or {}
        if isinstance(after, dict) and after.get("volume") is not None:
            parts.append(f"set the scanner's volume to {after['volume']}")
    line = ("The scan doctor " + "; ".join(parts) + ".") if parts else "The scan doctor hasn't changed anything yet."
    if isinstance(summ, dict):
        obs = int(summ.get("observing") or 0)
        keep = int(summ.get("keep") or 0)
        if obs or keep:
            line += (f" It is still listening to {obs} channel{'s' if obs != 1 else ''}"
                     + (f" and has confirmed {keep} as keepers" if keep else "") + ".")
    return line


def describe_doctor(rep) -> str:
    """The scan optimiser's report, spoken. Its exact shape belongs to the
    console and may change, so this reads the obvious fields defensively."""
    if isinstance(rep, str) and rep.strip():
        return rep.strip()[:400]
    if not isinstance(rep, dict):
        return "The scan doctor's report has nothing I can read out, sir."
    spoken = _describe_doctor_v1(rep)
    if spoken:
        return spoken
    lead = ""
    for k in ("spoken", "summary", "headline", "message"):
        if isinstance(rep.get(k), str) and rep[k].strip():
            lead = rep[k].strip().rstrip(".") + "."
            break
    changes = next((rep[k] for k in ("changes", "applied", "actions", "recommendations", "items")
                    if isinstance(rep.get(k), list)), [])
    items = []
    for c in changes[:3]:
        if isinstance(c, str) and c.strip():
            items.append(c.strip().rstrip("."))
        elif isinstance(c, dict):
            what = c.get("summary") or c.get("description") or c.get("text") or " ".join(
                str(c[k]) for k in ("action", "channel", "name", "setting") if c.get(k))
            why = c.get("reason") or c.get("why")
            if what:
                items.append(f"{str(what).strip().rstrip('.')}" + (f" ({str(why).strip().rstrip('.')})" if why else ""))
    if items:
        n = len(changes)
        body = f"The scan doctor made {n} change{'s' if n != 1 else ''}: " + "; ".join(items) + "."
        if n > len(items):
            body += f" And {n - len(items)} more."
        return (lead + " " + body).strip() if lead and lead.rstrip(".") not in body else body
    if lead:
        return lead
    if changes == [] and any(k in rep for k in ("changes", "applied", "actions")):
        return "The scan doctor looked and changed nothing, sir."
    return "The scan doctor's report has nothing I can read out, sir."


class ScannerDesk:
    """One per process (`desk`, below); main.py spawns `run()`."""

    def __init__(self) -> None:
        self.client: ConsoleClient | None = None
        self.after_id: int | None = None
        self.health: dict | None = None
        self.health_ts = 0.0
        self.up: bool | None = None
        self.down_since = 0.0
        self.last_spawn = -1e18
        self.spawns = 0
        self._down_attempts = 0
        self._queue: deque = deque(maxlen=40)
        self._wake = asyncio.Event()
        self._in_flight: int | None = None
        self._baselines: dict = {}
        self._said: dict[str, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._tasks: set = set()
        self._last_retry = 0.0
        self._last_prune = 0.0
        self._lost_since: float | None = None
        self._last_hear = 0.0
        self._last_doctor = 0.0
        self.home_gap = ""            # what is wrong with his own town's fire channel, if anything
        self._home_watched: float | None = None   # when its channel was first seen on the scanner (this run)
        self._worker = None
        self.stats = Counter()
        self.retry_pause = 5.0            # before retrying a clip the recogniser could not take
        self._behind_since = 0.0          # when the console first had something the feed did not
        # seams for the gate - each defaults to the real thing
        self.now = time.time
        self.spawner = _spawn_console
        self.process_running = _console_process_running
        self.voice_busy = _voice_busy_default
        self.quiet_hours = _in_quiet_hours
        self.remote_turn = _remote_turn_default
        self.transcriber = None       # async (np.ndarray) -> str | None
        self.deliverer = None         # async (text, tier, key=, subject=, written=) -> dict
        self.summariser = None        # async (incident, urgent) -> dict | None
        self.clip_sender = None       # async (wav path, caption) -> bool
        self.editor = None            # async (message id, text) -> bool
        self.asker = None             # async (messages) -> raw model output
        self._refreshing: set[int] = set()
        self._dirty: set[int] = set()                         # more arrived while a refresh ran
        self._edited: dict[int, str] = {}                     # id -> the text his message shows
        # the plain-words summary: how many transmissions it covers, when it
        # was last asked for, and the calls being summarised right now
        self._summary_n: dict[int, int] = {}
        self._summary_ts: dict[int, float] = {}
        self._summarising: set[int] = set()
        self._more: dict[int, tuple[int, str]] = {}           # id -> (transmissions, his "tell me more")
        self._pages: dict[str, tuple[float, str]] = {}        # channel -> (when, which) two-tone page

    # ------------------------------------------------------------ plumbing
    def _log_every(self, key: str, every: float, level: int, msg: str, *args, exc: bool = False) -> None:
        """One line per `every` seconds per subject: an absent console must
        not fill the log."""
        now = time.time()
        if now - self._said.get(key, 0.0) < every:
            return
        self._said[key] = now
        log.log(level, msg, *args, exc_info=exc)

    def _ensure_client(self) -> ConsoleClient:
        if self.client is None:
            self.client = ConsoleClient(str(_cfg("url", "http://127.0.0.1:8125")))
        return self.client

    def _spawn_task(self, coro, name: str) -> None:
        t = spawn(coro, name=name)
        if isinstance(t, asyncio.Task):
            self._tasks.add(t)
            t.add_done_callback(self._tasks.discard)

    async def settle(self, timeout: float = 10.0) -> None:
        """Wait for decisions in flight (the gate uses this)."""
        end = time.time() + timeout
        while self._tasks and time.time() < end:
            await asyncio.sleep(0.02)

    # ------------------------------------------------------------ the loop
    async def run(self) -> None:
        if not _cfg("enabled", True):
            log.info("scanner desk: switched off in config (scanner.enabled)")
            return
        client = self._ensure_client()
        try:
            self._rejudge(self.now() - 86400)
        except Exception:
            log.warning("scanner: re-reading the day's incidents failed", exc_info=True)
        if self._worker is None:
            self._worker = spawn(self._transcribe_loop(), name="scanner-transcribe")
        log.info("scanner desk: following the console at %s", client.base_url)
        while True:
            try:
                delay = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log_every("tick", 3600, logging.ERROR, "scanner desk: a tick failed", exc=True)
                delay = 10.0
            await asyncio.sleep(delay)

    async def tick(self) -> float:
        """One look at the console. Returns how long to wait before the next."""
        client = self._ensure_client()
        now = self.now()
        if not self.up or now - self.health_ts >= 5.0:
            h = await client.health()
            if h is None:
                self._mark_down(now)
                await self._supervise(now)
                try:
                    await self._check_hearing(now)      # a console that stays down is said too
                except Exception:
                    self._log_every("hearing", 3600, logging.WARNING, "scanner: the hearing check failed", exc=True)
                return 10.0
            self._mark_up(h, now)
        if self.after_id is None:
            self.after_id = self._load_cursor()
        got = await client.feed(self.after_id, limit=200)
        if got is None:
            # health answered and the feed did not: an older console, or one
            # going away. Health is asked again next time; said once an hour.
            self._log_every("feed", 3600, logging.WARNING,
                            "scanner: the console answers but its feed does not (%s)", client.last_error)
            self.health_ts = 0.0
            return 5.0
        n = await self.ingest(got.get("hits") or [], got.get("last_id"))
        if now - self._last_retry >= 60:
            self._last_retry = now
            await self._retry_pending()
            try:
                await self._morning_summary(now)
            except Exception:
                self._log_every("morning", 3600, logging.WARNING, "scanner: the morning summary failed", exc=True)
            try:
                await self._check_hearing(now)
            except Exception:
                self._log_every("hearing", 3600, logging.WARNING, "scanner: the hearing check failed", exc=True)
            try:
                await self._check_doctor(now)
            except Exception:
                self._log_every("doctor", 3600, logging.WARNING, "scanner: the scan doctor check failed", exc=True)
        if now - self._last_prune >= 86400:
            self._last_prune = now
            store.prune(now)
        return 0.05 if n >= 200 else float(_cfg("poll_s", 1.0))

    def _mark_down(self, now: float) -> None:
        if self.up is not False:
            log.warning("scanner: the console at %s is not answering (%s)",
                        self._ensure_client().base_url, self._ensure_client().last_error or "no reply")
            self.down_since = now
        self.up = False

    def _mark_up(self, h: dict, now: float) -> None:
        if self.up is not True:
            log.info("scanner: console up (version %s; scanner %s, %s; audio %s)",
                     h.get("version"), "connected" if h.get("connected") else "NOT connected",
                     "scanning" if h.get("scanning") else "not scanning",
                     "weak" if (h.get("audio") or {}).get("weak_input") else "ok")
        self.up, self.health, self.health_ts = True, h, now
        self._down_attempts = 0

    async def _supervise(self, now: float) -> None:
        """Start the console when it does not answer - at most once per
        `autostart_every_s`, and once an hour after three tries in a row fail."""
        if not _cfg("autostart", True):
            return
        every = float(_cfg("autostart_every_s", 300))
        if self._down_attempts >= 3:
            every = max(every, 3600.0)
        if now - self.last_spawn < every:
            return
        self.last_spawn = now
        exe = str(_cfg("console_exe", "") or "")
        if not exe or not os.path.exists(exe):
            self._log_every("noexe", 86400, logging.WARNING,
                            "scanner: BC125AT Console is not installed at %r; not starting it", exe)
            return
        if await asyncio.to_thread(self.process_running):
            self._log_every("silent", 21600, logging.WARNING,
                            "scanner: BC125AT Console is running but /api/health does not answer "
                            "(an older build without --serve?); leaving it alone")
            return
        self._down_attempts += 1
        self.spawns += 1
        ok = await asyncio.to_thread(self.spawner, exe)
        log.info("scanner: console not answering - %s %s --serve (try %d of this outage)",
                 "started" if ok else "could NOT start", exe, self._down_attempts)

    # ------------------------------------------------------------ the cursor
    def _load_cursor(self) -> int:
        try:
            import volatile
            got = volatile.get(CURSOR_KEY)
            if got and isinstance(got.get("value"), dict):
                return int(got["value"].get("after_id") or 0)
        except Exception:
            log.debug("scanner: cursor unreadable", exc_info=True)
        return store.max_hit_id()

    def _save_cursor(self) -> None:
        try:
            import volatile
            volatile.put(CURSOR_KEY, {"after_id": int(self.after_id or 0)}, source="scanner")
        except Exception:
            log.debug("scanner: cursor not saved", exc_info=True)

    # ------------------------------------------------------------ the feed
    def _row(self, h: dict) -> dict | None:
        try:
            hid = int(h["id"])
            start = float(h.get("start") or 0)
            end = float(h.get("end") or start)
        except (KeyError, TypeError, ValueError):
            return None
        name = str(h.get("name") or "").strip()
        lab = rules.parse_label(name)
        q = h.get("quality") if isinstance(h.get("quality"), dict) else None
        try:
            dur = float(h.get("duration") if h.get("duration") is not None else max(0.0, end - start))
        except (TypeError, ValueError):
            dur = max(0.0, end - start)
        return {"id": hid, "ts": start, "end_ts": end, "duration": dur, "channel": h.get("channel"),
                "name": name, "freq": h.get("freq"), "town": lab.town.name if lab.town else "",
                "agency": lab.agency, "quality": q, "audio_path": str(h.get("audio_path") or ""),
                "peak_rssi": h.get("peak_rssi")}

    async def ingest(self, hits: list, last_id=None) -> int:
        """Finished transmissions from the feed, ascending. Returns how many came."""
        now = self.now()
        cur = int(self.after_id or 0)
        if not hits:
            self._check_cursor(now, cur, last_id)
            return 0
        self._behind_since = 0.0
        max_age = float(_cfg("backlog_max_age_s", 600))
        moved = False
        def _id(x: dict) -> int:
            try:
                return int(x.get("id") or 0)
            except (TypeError, ValueError):
                return 0
        for h in sorted((x for x in hits if isinstance(x, dict)), key=_id):
            hid = _id(h)
            if hid <= int(self.after_id or 0):
                continue
            self.after_id, moved = hid, True    # past it even if it is malformed: never stuck on one
            row = self._row(h)
            if row is None:
                self.stats["malformed"] += 1
                continue
            if now - row["ts"] > 14 * 86400:
                continue
            store.log_hit(row)
            if now - (row["end_ts"] or row["ts"]) > max_age:
                self.stats["backlog_counted"] += 1
                continue                       # counted for briefs and baselines; too old to act on
            if row["agency"] in ("FD", "EMS") and row["town"]:
                await self._check_burst(row, now)
            text = str(h.get("transcript") or "").strip()
            if text:
                conf = h.get("asr_conf")
                try:
                    conf = float(conf) if conf is not None else rules.asr_confidence(text, row["duration"], row["quality"])
                except (TypeError, ValueError):
                    conf = rules.asr_confidence(text, row["duration"], row["quality"])
                await self.on_text(row, text, conf, annotate=False)
            elif _cfg("transcribe", True) and row["audio_path"] and rules.transcribable(row["quality"]):
                self._enqueue(row)
        if moved:
            self._save_cursor()
        return len(hits)

    def _check_cursor(self, now: float, cur: int, last_id) -> None:
        """A DEAF DESK IS WORSE THAN AN ABSENT ONE. If his console's log is
        ever reset its ids start again at 1, and a cursor still ahead of them
        would see nothing forever while every health check looked fine. Two
        signals, either enough: the feed's last_id below the cursor, or health
        reporting a transmission newer than anything logged here for five
        minutes while the feed stays empty. Then follow from the start; the
        reset log is short, and old rows sharing a new id are replaced."""
        known = isinstance(last_id, int) and not isinstance(last_id, bool)
        if known and last_id >= cur:
            self._behind_since = 0.0             # it has nothing past the cursor: not reset
            return
        newest = float((self.health or {}).get("last_hit_ts") or 0.0)
        mine = store.last_hit() or {}
        mine_ts = float(mine.get("end_ts") or mine.get("ts") or 0.0)
        behind = cur > 0 and newest > 0 and newest - mine_ts > 120
        if not behind:
            self._behind_since = 0.0
            return
        self._behind_since = self._behind_since or now
        if (known and 0 <= last_id < cur) or now - self._behind_since > 300:
            log.warning("scanner: the console has transmissions newer than any I can see (cursor %d, its last "
                        "id %s) - its log looks reset; following it from the start", cur, last_id)
            self.after_id, self._behind_since = 0, 0.0
            self._save_cursor()

    # ------------------------------------------------------------ activity bursts
    def _baseline(self, name: str, hour: int, now: float, window: float) -> tuple[float, int]:
        key = (name, hour)
        got = self._baselines.get(key)
        if got and now - got[2] < 3600:
            return got[0], got[1]
        per, days = store.baseline(name, hour, now - window, window)
        self._baselines[key] = (per, days, now)
        return per, days

    async def _check_burst(self, row: dict, now: float) -> None:
        """A fire or EMS channel far busier than its own history for this hour
        is something happening, words or not."""
        window = float(_cfg("burst_window_s", 600))
        name = row["name"]
        n = store.count_hits(name, now - window)
        if n < int(_cfg("burst_min", 8)):
            return
        base, days = self._baseline(name, dt.datetime.fromtimestamp(now).hour, now, window)
        if n < float(_cfg("burst_ratio", 3.0)) * max(base, 1.0):
            return
        town = row["town"]
        for inc in store.incidents_since(now - float(_cfg("merge_minutes", 90)) * 60, town):
            # an earlier burst, or a fire serious enough to explain the traffic;
            # a fire alarm or a CO call left open must not swallow a real burst
            if (inc["burst"] and not inc["types"]) or (
                    inc["family"] in ("fire", "ems") and inc["status"] == "active"
                    and int(inc["severity"] or 0) >= rules.KINDS["structure_fire"].severity):
                fields = {"last_ts": now, "burst": 1}
                if inc["burst"] and not inc["types"]:
                    fields.update(hits=max(int(inc["hits"] or 0), n),
                                  summary=self._burst_summary(n, base, days, window))
                store.update_incident(inc["id"], fields)
                if inc["burst"] and not inc["types"] and not inc["tier"] and self._burst_worth_sending(n, days):
                    # a stored spell that has grown into a truly unusual one
                    self._spawn_task(self._consider(inc["id"], row, ""), name=f"scanner-consider-{inc['id']}")
                return
        lab = rules.parse_label(name)
        t = geo.town(town)
        iid = store.create_incident({
            "first_ts": now, "last_ts": now, "town": town, "miles": round(t.miles, 1) if t else None,
            "agency": row["agency"], "channel": name, "family": "fire" if row["agency"] == "FD" else "ems",
            "kind": rules.BURST.key, "label": f"unusual activity on {lab.spoken}", "severity": rules.BURST.severity,
            "types": [], "hits": n, "burst": 1, "summary": self._burst_summary(n, base, days, window)})
        log.info("scanner: burst on %s - %d transmissions in %d min (normal %.1f, from %d days)%s",
                 name, n, int(window / 60), base, days, f" -> incident #{iid}" if iid else "")
        # "BUSY" MEANS BUSIER THAN USUAL, AND USUAL TAKES DAYS TO LEARN. On
        # its first morning (2026-11-07) Natick FD's ordinary traffic - 8 in
        # ten minutes against a baseline of nothing - came within a test
        # mute of reaching his phone as "unusually busy". Until a channel
        # has a few days of history its bursts are stored, not sent.
        min_days = int(_cfg("burst_min_days", 3))
        if iid and days < min_days:
            log.info("scanner: #%d stored, not sent - only %d day(s) of history for %s (need %d)",
                     iid, days, name, min_days)
            return
        if iid and not self._burst_worth_sending(n, days):
            log.info("scanner: #%d stored for the briefs, not sent - %d transmissions is an ordinary busy spell "
                     "(sent from %d)", iid, n, int(_cfg("burst_send_min", 15)))
            return
        if iid:
            self._spawn_task(self._consider(iid, row, ""), name=f"scanner-consider-{iid}")

    @staticmethod
    def _burst_worth_sending(n: int, days: int) -> bool:
        """ONLY THE TRULY UNUSUAL (his answer, 2026-11-12). In four days 68
        "unusually busy" messages reached his phone, 55 of them at exactly
        eight transmissions in ten minutes - one ordinary medical call - and
        48 never became a call that could be named. A spell is still recorded
        at eight (the briefs and the morning summary list it); it is sent only
        from fifteen, on a channel with enough history to know its normal."""
        return n >= int(_cfg("burst_send_min", 15)) and days >= int(_cfg("burst_min_days", 3))

    @staticmethod
    def _burst_summary(n: int, base: float, days: int, window: float) -> str:
        line = f"{n} transmissions in {int(window / 60)} minutes"
        if days >= 3 and base > 0:
            times = n / base
            line += f", about {int(round(times))} times normal for this hour" if times < 20 else ", far above normal for this hour"
        return line

    # ------------------------------------------------------------ transcription
    def _enqueue(self, row: dict) -> None:
        if len(self._queue) == self._queue.maxlen:
            self.stats["dropped_full"] += 1    # the deque drops the OLDEST, which is the right one to lose
        self._queue.append(row)
        self._wake.set()

    async def _transcribe_loop(self) -> None:
        while True:
            try:
                if not self._queue:
                    self._wake.clear()
                    await self._wake.wait()
                    continue
                await self._transcribe_one(self._queue.popleft())
            except asyncio.CancelledError:
                raise
            except Exception:
                self._log_every("stt", 3600, logging.ERROR, "scanner: transcription failed", exc=True)

    async def _wait_voice_quiet(self, deadline: float) -> bool:
        while True:
            try:
                busy = bool(self.voice_busy())
            except Exception:
                busy = True
            if not busy:
                return True
            if self.now() > deadline:
                return False
            await asyncio.sleep(0.5)

    def _resolve_audio(self, audio_path: str) -> Path | None:
        if not audio_path:
            return None
        p = Path(audio_path)
        if p.is_absolute():
            return p
        base = str(_cfg("recordings_dir", "") or "") or os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "BC125AT Console", "recordings")
        return Path(base) / p

    async def _load_audio(self, row: dict) -> np.ndarray | None:
        p = self._resolve_audio(row.get("audio_path") or "")
        data = None
        if p is not None and p.exists():
            try:
                data = await asyncio.to_thread(p.read_bytes)
            except OSError:
                data = None
        if not data:
            data = await self._ensure_client().clip(row["id"])
        if not data:
            self.stats["no_audio"] += 1
            return None
        return await asyncio.to_thread(decode_wav, data)

    async def _transcribe_chunk(self, chunk: np.ndarray) -> str | None:
        if self.transcriber is not None:
            return await self.transcriber(chunk)
        from audio.stt import stt
        return await stt.transcribe_background(chunk, max_wait=30.0)

    async def _transcribe_one(self, row: dict) -> None:
        deadline = (row.get("end_ts") or row["ts"]) + float(_cfg("backlog_max_age_s", 600))
        if self.now() > deadline or not await self._wait_voice_quiet(deadline):
            self.stats["dropped_old"] += 1
            return
        self._in_flight = row["id"]
        try:
            audio = await self._load_audio(row)
            await self._note_page(row, audio)
            audio, _peak = normalise_audio(audio) if audio is not None else (None, -120.0)
            if audio is None:
                self.stats["silent"] += 1
                return
            parts = []
            for chunk in split_audio(audio):
                if not await self._wait_voice_quiet(deadline):
                    self.stats["dropped_old"] += 1
                    return
                said = await self._transcribe_chunk(chunk)
                if said is None:
                    # the recogniser is not loaded yet (boot) or was never free:
                    # try this clip again shortly; its deadline still drops it
                    # if the wait runs past ten minutes
                    self.stats["stt_unavailable"] += 1
                    self._queue.appendleft(row)
                    await asyncio.sleep(self.retry_pause)
                    return
                parts.append(said)
        finally:
            self._in_flight = None
        text = re.sub(r"\s+", " ", " ".join(parts)).strip()
        self.stats["transcribed"] += 1
        conf = rules.asr_confidence(text, row.get("duration") or 0.0, row.get("quality"))
        if text:
            await self.on_text(row, text, conf)

    # ------------------------------------------------------------ tone-outs
    # A TWO-TONE PAGE IS THE DISPATCHER SENDING A STATION OUT (2026-11-12).
    # Measured on 4,249 real clips: Ashland Fire pages 905/770 Hz, Natick Fire
    # 2265/950, Holliston 1885/1475, always in the first second or so of a
    # transmission, and what follows is a dispatch ("smoke detector
    # activation", "car fire") - sometimes in the same clip, sometimes in the
    # next. Single long tones were station IDs and daily tests (Sudbury's FCC
    # announcement, Wayland at 6:30 PM), so only two-tone pages count.
    # His answers: a paged dispatch whose kind can't be named still goes out
    # silently; a page does NOT stand in for the second transmission that
    # lets a danger call sound.
    PAGE_WINDOW_S = 120.0         # a page covers what its channel says for this long

    async def _note_page(self, row: dict, audio) -> str:
        """Look for a two-tone page in a transmission's audio and remember it
        for its channel. Returns the page's key ("905/770") or ""."""
        if audio is None or not _cfg("tone_outs", True):
            return ""
        try:
            found = [p for p in await asyncio.to_thread(tones.pages, audio) if p.kind == "two-tone"]
        except Exception:
            self._log_every("tones", 3600, logging.WARNING, "scanner: the tone detector failed", exc=True)
            return ""
        if not found:
            return ""
        key = found[0].key
        row["page"] = key
        ts = float(row.get("ts") or self.now())
        self._pages[row.get("name") or ""] = (ts, key)
        self.stats["pages"] = self.stats.get("pages", 0) + 1
        log.info("scanner: two-tone page %s on %s (hit %s)", key, row.get("name"), row.get("id"))
        return key

    def _page_for(self, row: dict) -> str:
        """The page this transmission belongs to: its own, or one its channel
        carried in the two minutes before it."""
        if row.get("page"):
            return str(row["page"])
        ts, key = self._pages.get(row.get("name") or "", (0.0, ""))
        now = float(row.get("ts") or self.now())
        return key if key and 0 <= now - ts <= self.PAGE_WINDOW_S else ""

    # ------------------------------------------------------------ words -> incidents
    async def on_text(self, row: dict, text: str, conf: float, annotate: bool = True) -> int | None:
        """A transcript for one transmission: stored, read by the rules, merged
        into an incident, posted back to the console."""
        row["transcript"], row["asr_conf"] = text, conf
        store.set_transcript(row["id"], text, conf)
        iid, tags = None, []
        if conf >= rules.TRUST:
            reading = rules.read(text, row.get("town") or "")
            page = self._page_for(row)
            if page and not reading.types and not reading.resolved and (row.get("agency") or "") in ("FD", "EMS"):
                # paged, and the rules can't name it: it is still a dispatch
                kind = rules.KINDS[rules.PAGED]
                reading.types, reading.severity, reading.family = [rules.PAGED], kind.severity, kind.family
            if page and reading.types:
                self._pages.pop(row.get("name") or "", None)    # one page, one dispatch: what follows is follow-up
            tags = list(reading.types) + (["resolved"] if reading.resolved else [])
            shaky_danger = int(reading.severity or 0) >= rules.KINDS["structure_fire"].severity \
                and conf < self.SURE
            if (reading.types and not self._corroborated(row, reading, conf)):
                # ONE SHAKY TRANSMISSION IS NOT A FIRE. On 2026-11-07 a noisy
                # "...for the residential fire activation" (confidence 0.41,
                # really a fire-alarm call) became "a reported structure fire
                # in Wayland". A dangerous reading from a transcript below
                # SURE waits for a second transmission from the same town
                # saying the same kind of thing.
                log.info("scanner: holding %s in %s from one shaky transmission (confidence %.2f) "
                         "until a second one agrees", reading.top, row.get("town") or "?", conf)
                tags.append("unconfirmed")
            elif reading.types or reading.resolved:
                # a shaky dangerous reading that got here had a second
                # transmission agree with it: that is as good as a clear one
                iid = self._merge(row, reading, text, conf, agreed=bool(reading.types) and shaky_danger,
                                  page=page if reading.types else "")
                if iid:
                    store.set_transcript(row["id"], text, conf, iid)
            else:
                iid = self._attach(row, text, conf, reading)
                if iid:
                    store.set_transcript(row["id"], text, conf, iid)
                    tags.append("context")
        if annotate:
            await self._annotate(row["id"], text, conf, tags, iid)
        return iid

    # A transcript at or above this is believed on its own; below it (and
    # above TRUST) a dangerous reading needs a second transmission to agree.
    SURE = 0.6
    SHAKY_WINDOW_S = 1800.0

    def _corroborated(self, row: dict, reading: rules.Reading, conf: float) -> bool:
        """May this reading start or escalate an incident on its own?"""
        danger = int(reading.severity or 0) >= rules.KINDS["structure_fire"].severity
        if not danger or conf >= self.SURE:
            return True
        now = float(row.get("end_ts") or row.get("ts") or self.now())
        town = row.get("town") or ""
        seen = getattr(self, "_shaky", None)
        if seen is None:
            seen = self._shaky = {}
        for k in [k for k, t in seen.items() if now - t > self.SHAKY_WINDOW_S]:
            del seen[k]
        key = (town, reading.family or reading.top)
        if key in seen and seen[key] != now:
            del seen[key]
            return True                        # a second transmission agrees
        seen[key] = now
        return False

    def _merge(self, row: dict, reading: rules.Reading, text: str, conf: float | None = None,
               agreed: bool = False, page: str = "") -> int | None:
        now = float(row.get("end_ts") or row["ts"])
        town = reading.towns[0] if reading.towns else (row.get("town") or "")
        said_row = {"ts": row["ts"], "text": text[:SAID_TEXT_MAX], "hit": row["id"], "channel": row.get("name") or "",
                    "max": SAID_TEXT_MAX}
        if conf is not None:
            said_row["conf"] = round(float(conf), 2)     # the quiet feed believes clear words only
        if agreed:
            said_row["agreed"] = True
        match = None
        for inc in store.incidents_since(now - float(_cfg("merge_minutes", 90)) * 60, town):
            if reading.street and inc["street"]:
                if reading.street == inc["street"]:
                    match = inc
                    break
                continue                        # two different places
            if inc["status"] == "over":
                continue                        # a finished incident takes only its own street back
            if reading.family and (inc["family"] == reading.family
                                   or (inc["burst"] and not inc["types"] and reading.family in ("fire", "ems"))):
                if self._separate_call(inc, reading, text, now):
                    continue
                # A police or medical channel runs several calls at once: a
                # theft and a missing person ten minutes apart are two calls
                # (Wellesley PD, 2026-11-07). They join only on the same kind.
                if reading.family in ("police", "ems") and inc["types"] and \
                        not set(reading.types) & set(inc["types"]):
                    continue
                match = inc
                break
            if not reading.types and reading.resolved and inc["channel"] == row.get("name") and inc["status"] == "active":
                match = inc
                break
        if match is None and reading.street:
            # Mutual aid: "Engine 2 on scene at 88 Eliot Street" does not say
            # Natick again - the same street on the same channel is the same call
            for inc in store.open_on_channel(row.get("name") or "", now - float(_cfg("merge_minutes", 90)) * 60):
                if inc.get("street") == reading.street:
                    match = inc
                    break
        if match is None:
            if not reading.types:
                return None                     # a stand-down with nothing to stand down
            t = geo.town(town) if town else None
            iid = store.create_incident({
                "first_ts": now, "last_ts": now, "town": town, "miles": round(t.miles, 1) if t else None,
                "agency": row.get("agency") or "", "channel": row.get("name") or "", "family": reading.family,
                "kind": reading.top, "label": reading.label, "severity": reading.severity, "types": reading.types,
                "address": reading.address, "street": reading.street, "roads": reading.roads,
                "units": reading.units, "said": [said_row], "hits": 1, "details": rules.details(text),
                "status": "over" if reading.resolved else "active", "paged": page})
            if iid:
                log.info("scanner: incident #%d - %s%s in %s (%s)", iid, reading.label,
                         f" at {reading.address}" if reading.address else "", town or "an unknown town",
                         row.get("name"))
                self._spawn_task(self._consider(iid, row, ""), name=f"scanner-consider-{iid}")
            return iid
        inc = match
        fields: dict = {"last_ts": max(float(inc["last_ts"]), now), "hits": int(inc["hits"] or 0) + 1,
                        "said": (inc["said"] + [said_row])[-SAID_KEEP:],
                        "details": _union(inc.get("details") or [], rules.details(text), 8)}
        esc = rules.escalation(inc["types"], reading) if reading.types else ""
        if page and not inc.get("paged"):
            fields["paged"] = page
        if reading.types:
            types = sorted(set(inc["types"]) | set(reading.types),
                           key=lambda k: -rules.KINDS[k].severity if k in rules.KINDS else 0)
            top = types[0]
            fields.update(types=types, kind=top, label=rules.describe(types),
                          severity=max(int(inc["severity"] or 0), reading.severity),
                          family=rules.KINDS[top].family if top in rules.KINDS else inc["family"],
                          roads=list(dict.fromkeys(inc["roads"] + reading.roads)),
                          units=list(dict.fromkeys(inc["units"] + reading.units))[:10])
            if inc["burst"] and not inc["types"]:
                fields["summary"] = ""            # the words replace the burst's numbers
            if not inc["address"] and reading.address:
                fields.update(address=reading.address, street=reading.street)
            if inc["status"] == "over" and reading.danger:
                fields["status"] = "active"
        if reading.resolved and not reading.danger:
            fields["status"] = "over"
            log.info("scanner: incident #%d stood down: %r", inc["id"], text[:100])
        if esc:
            fields["escalations"] = inc["escalations"] + [esc]
            log.info("scanner: incident #%d escalated - %s", inc["id"], esc)
        store.update_incident(inc["id"], fields)
        # Judged again when it got worse, when it gained a kind - and while he
        # has not been told at all: a shaky first report that a second one now
        # confirms is worth a line ("heard twice"), and nothing else would
        # have asked (review of 2026-11-07).
        # Every transmission that names the call has it judged again: it may
        # have become worse, gained a kind, or - the commonest - been CONFIRMED
        # by this second voice, which is what lets an alert sound (judging is
        # idempotent: nothing is sent twice at a tier).
        if esc or reading.types:
            self._spawn_task(self._consider(inc["id"], row, esc), name=f"scanner-consider-{inc['id']}")
        self._schedule_refresh(inc["id"])       # his message for it, if he has one, grows
        return inc["id"]

    # Crashes and police calls are short: one heard this long after the last
    # word about another in the same town is a new call.
    SHORT_CALL_S = 1800.0
    _ANOTHER = re.compile(r"\banother (?:motor vehicle|mva|mvc|crash|accident|collision|car crash)\b", re.I)

    def _separate_call(self, inc: dict, reading: rules.Reading, text: str, now: float) -> bool:
        """Two calls, not one? On 2026-11-07 four Wellesley PD crash reports
        across two hours ("...for ANOTHER motor vehicle accident") became one
        incident, and its message read "2 vehicles; hit and run; 1 vehicle".
        Only crashes and police calls: a fire is long, and "another report of
        heavy smoke" is the same fire getting worse, not a second one."""
        if inc.get("family") not in ("traffic", "police", "ems"):
            return False                 # medical calls are short too; fires are not
        if reading.types and self._ANOTHER.search(text or ""):
            return True
        return now - float(inc["last_ts"]) > self.SHORT_CALL_S

    # A transmission with no call in it joins the open call on its channel
    # only while that call is this fresh (Wellesley PD's follow-ups on a
    # crash came 12 minutes after it).
    CONTEXT_WINDOW_S = 900.0
    # facts that describe a unit, not a call
    _STATUS_ONLY = frozenset({"units on scene", "mutual aid", "police requested"})

    def _attach(self, row: dict, text: str, conf: float, reading: rules.Reading) -> int | None:
        """THE DETAILS ARE IN THE FOLLOW-UPS (2026-11-07, his words: "I need
        more details than just a crash in Sudbury"). "Car 3 on scene, two
        patients, transporting one to Emerson" names no kind of call, so the
        rules used to drop it - and with it everything he wanted to know. It
        joins the one open call on its own channel: on a fire or EMS channel
        always (one call at a time is the norm); on a police channel, which
        carries several calls at once, only when it states a fact or names
        the call's street. Two open calls on the channel: whose words these
        are would be a guess, so they join neither."""
        if not _cfg("context", True):
            return None
        name = row.get("name") or ""
        now = float(row.get("end_ts") or row.get("ts") or self.now())
        open_now = store.open_on_channel(name, now - self.CONTEXT_WINDOW_S)
        if len(open_now) != 1:
            return None
        inc = open_now[0]
        facts = rules.details(text)
        same_street = bool(reading.street and reading.street == inc.get("street"))
        fire_or_ems = (row.get("agency") or "").upper() in ("FD", "EMS")
        # On a police channel a unit's status ("Car 12 on location with the
        # lockout") is not a fact about THIS call: only the call's substance
        # (injuries, vehicles, transport, damage...) or its street ties a line
        # to it (review of 2026-11-07).
        substance = [f for f in facts if f not in self._STATUS_ONLY]
        if not fire_or_ems and not substance and not same_street:
            return None
        said_row = {"ts": row["ts"], "text": text[:SAID_TEXT_MAX], "hit": row["id"], "channel": name, "max": SAID_TEXT_MAX,
                    "conf": round(float(conf), 2), "context": True}
        fields: dict = {"last_ts": max(float(inc["last_ts"]), now), "hits": int(inc["hits"] or 0) + 1,
                        "said": (inc["said"] + [said_row])[-SAID_KEEP:],
                        "details": _union(inc.get("details") or [], facts, 8),
                        "units": _union(inc.get("units") or [], reading.units, 10)}
        if not inc.get("address") and reading.address and fire_or_ems:
            # a police channel's next line is as likely another call's place
            fields.update(address=reading.address, street=reading.street)
        if not store.update_incident(inc["id"], fields):
            return None
        log.info("scanner: #%d heard more: %r%s", inc["id"], text[:100], f" ({', '.join(facts)})" if facts else "")
        self._schedule_refresh(inc["id"])
        return inc["id"]

    # ------------------------------------------------------------ his message, kept up to date
    REFRESH_GAP_S = 20.0          # at most one edit of a message per this long

    def _schedule_refresh(self, iid: int) -> None:
        if iid in self._refreshing:
            self._dirty.add(iid)       # the refresh in flight goes round once more
            return
        inc = store.incident(iid)
        if not inc or not inc.get("msg_id"):
            return
        self._refreshing.add(iid)
        self._spawn_task(self._refresh(iid), name=f"scanner-refresh-{iid}")

    async def _refresh(self, iid: int) -> None:
        """Bring his message for a call up to date - an edit, which never makes
        a sound - with what has been heard since, at most once per
        REFRESH_GAP_S. One refresh per call at a time: what arrives while it
        waits or edits marks it dirty and it goes round again, so two edits
        never race and none is lost (review of 2026-11-07)."""
        try:
            while True:
                self._dirty.discard(iid)
                inc = store.incident(iid)
                if not inc or not inc.get("msg_id"):
                    return
                wait = float(inc.get("edited_ts") or 0) + self.REFRESH_GAP_S - self.now()
                if wait > 0:
                    await asyncio.sleep(min(wait, self.REFRESH_GAP_S))
                inc = store.incident(iid)
                if inc:
                    self._maybe_resummarise(inc)
                    await self._edit(inc)
                if iid not in self._dirty:
                    return
        finally:
            self._refreshing.discard(iid)
            self._dirty.discard(iid)

    # ------------------------------------------------------------ what is going on, in plain words
    RESUMMARY_GAP_S = 120.0       # a growing call is re-told at most this often

    def _maybe_resummarise(self, inc: dict) -> None:
        """New transmissions since his message was summarised: tell it again
        (in the background - the edit with what is known goes ahead now)."""
        iid = int(inc["id"])
        n = len(inc.get("said") or [])
        if not n or n <= self._summary_n.get(iid, 0) or iid in self._summarising:
            return
        if self.now() - self._summary_ts.get(iid, 0.0) < self.RESUMMARY_GAP_S:
            return
        self._spawn_task(self._summary_after(iid), name=f"scanner-summary-{iid}")

    async def _summary_after(self, iid: int) -> None:
        """A call already on his phone, told in plain words a few seconds
        later: the summary is edited into the same message, which never makes
        a sound (his choice, 2026-11-08: "send now, add summary after")."""
        if iid in self._summarising or not _cfg("llm_summary", True):
            return
        inc = store.incident(iid)
        if not inc or not inc.get("said") or (inc.get("burst") and not inc.get("types")):
            return
        self._summarising.add(iid)
        self._summary_ts[iid] = self.now()
        n = len(inc["said"])
        try:
            got = await self._summarise(inc, urgent=False)
        finally:
            self._summarising.discard(iid)
        self._summary_n[iid] = n
        self._keep_account(iid, n, got)
        if len(self._summary_n) > 256:
            for k in list(self._summary_n)[:128]:
                self._summary_n.pop(k, None)
                self._summary_ts.pop(k, None)
        if got and got.get("summary"):
            store.update_incident(iid, {"summary": got["summary"]})
            self._schedule_refresh(iid)

    def _unconfirmed(self, inc: dict) -> bool:
        """Only one transmission named it and nothing has backed it up (a busy
        spell, which has no words, is never labelled so)."""
        if inc.get("burst") and not inc.get("types"):
            return False
        named, confirmed, _ = self.evidence(inc)
        return named <= 1 and not confirmed

    async def _close_old(self, old_mid: int, iid: int) -> None:
        """A newer message took over this call (it was confirmed and sent as an
        alert): the old line says what it has become, once, silently."""
        inc = store.incident(iid)
        if not inc:
            return
        text = self.written(inc, FYI) + "\nConfirmed - the alert follows."
        try:
            if self.editor is not None:
                await self.editor(old_mid, text)
            else:
                from remote_telegram import telegram
                await telegram.edit_text(old_mid, text)
        except Exception:
            log.debug("scanner: could not close the old message for #%s", iid, exc_info=True)

    async def _edit(self, inc: dict) -> bool:
        text = self.written(inc, inc.get("tier") or FYI)
        if self._edited.get(inc["id"]) == text:
            return True                # nothing new to show; Telegram refuses an identical edit
        try:
            if self.editor is not None:
                ok = await self.editor(int(inc["msg_id"]), text)
            else:
                from remote_telegram import telegram
                ok = await telegram.edit_text(int(inc["msg_id"]), text)
        except Exception:
            log.warning("scanner: could not update the message for #%s", inc.get("id"), exc_info=True)
            ok = False
        if ok:
            self._edited[inc["id"]] = text
            store.update_incident(inc["id"], {"edited_ts": self.now()})
            if len(self._edited) > 256:
                self._edited = dict(list(self._edited.items())[-128:])
        return bool(ok)

    async def _annotate(self, hit_id: int, text: str, conf: float, tags: list[str], iid: int | None) -> None:
        label = None
        if iid:
            inc = store.incident(iid)
            if inc:
                label = f"JARVIS #{iid}: {inc['label']}" + (f", {inc['address']}" if inc["address"] else "") + \
                        (f", {inc['town']}" if inc["town"] else "")
        if await self._ensure_client().annotate(hit_id, text, conf, tags, label):
            self.stats["annotated"] += 1
        else:
            self.stats["annotate_failed"] += 1
            self._log_every("annotate", 3600, logging.WARNING, "scanner: the console would not take an "
                            "annotation (%s)", self._ensure_client().last_error)

    # ------------------------------------------------------------ deciding what reaches him
    def decide(self, inc: dict, now: float | None = None) -> tuple[str | None, str]:
        """(tier or None, why). URGENT only for active danger in his town or
        within near_miles; ALERT for that danger elsewhere in his radius and
        for crashes or closures on the roads he drives; everything else is
        stored. Quiet hours hold all but an URGENT in his own town; each tier
        has a hard daily cap."""
        now = now or self.now()
        town, miles = inc.get("town") or "", inc.get("miles")
        if not town or miles is None:
            return None, "no town for this channel"
        radius = geo.radius_mi()
        if float(miles) > radius:
            return None, f"{town} is {float(miles):.0f} miles away, outside his {radius:.0f}"
        if inc.get("status") == "over":
            return None, "the traffic says it is over"
        types = set(inc.get("types") or [])
        home = home_town().name
        near = town == home or float(miles) <= float(_cfg("near_miles", 5.0))
        place = "in his own town" if town == home else f"{float(miles):.0f} miles away in {town}"
        danger = any(rules.KINDS[t].danger for t in types if t in rules.KINDS) or \
            ("road_closure" in types and bool(types & rules.TRAFFIC))
        roads = inc.get("roads") or []
        if danger:
            tier, why = (URGENT if near else ALERT), f"{inc.get('label')} {place}"
        elif roads and types & (rules.TRAFFIC | {"road_closure"}):
            tier, why = ALERT, f"{inc.get('label')} on {', '.join(roads)}, {place}"
        elif inc.get("burst") and not types:
            if near:
                tier, why = ALERT, f"unusual activity on {inc.get('channel')} {place}"
            elif not _cfg("fyi", True):
                return None, f"a burst {place} is not worth an interruption"
            else:
                tier, why = FYI, f"a busy spell on {inc.get('channel')} {place}"
        else:
            ok, why = self._fyi(inc, types, town == home, place)
            if not ok:
                return None, why
            tier = FYI
        # NO ALERT FROM ONE TRANSMISSION (his answer, 2026-11-07: "wait for
        # confirmation"). Every app that turned one misheard line into a loud
        # alert sent false shootings and fires (CrimeRadar's "show me out at"
        # -> school shooting). One transmission goes out as a silent,
        # "Unconfirmed" line with the recording; the sound - or the spoken
        # interruption at the PC - comes when a second transmission agrees.
        # Busy spells are counts against days of history, not words: exempt.
        if tier in (URGENT, ALERT) and not (inc.get("burst") and not types) and not self.evidence(inc)[2]:
            ok, held = self._believed(inc, inc.get("label") or "activity", place)
            if not ok:
                return None, held
            tier, why = FYI, f"{why} - unconfirmed, one transmission: silent until a second agrees"
        if self.quiet_hours() and not (tier == URGENT and town == home):
            # HIS NIGHT (his answer, 2026-11-07: "send silently plus in morning
            # summary"): everything still goes, silently - the quiet feed is
            # never spoken at night (notify_policy.NIGHT_SILENT) and danger
            # outside Framingham loses its sound. Danger in Framingham itself
            # still wakes him. scanner.night = "hold" restores holding.
            if str(_cfg("night", "silent")) == "hold":
                return None, f"quiet hours ({why})"
            if tier == URGENT:
                tier, why = ALERT, f"{why} (at night, silently: not in his own town)"
        caps = _cfg("max_per_day", {}) or {}
        cap = int(caps.get(tier, _DAILY_CAP[tier])) if isinstance(caps, dict) else _DAILY_CAP[tier]
        used = store.sent_count(tier, now - 86400)
        if used >= cap:
            return None, f"daily {tier} cap reached ({used} of {cap}) - {why}"
        return tier, why

    @staticmethod
    def evidence(inc: dict) -> tuple[int, bool, bool]:
        """(transmissions that named the call, confirmed, confirmed enough to
        sound). Confirmed enough to sound: two transmissions named it, or a
        shaky one had a second agree. Confirmed (the "Unconfirmed" label goes):
        that, or a follow-up on its channel stated something concrete
        ("Engine 1 on scene", "two patients, transporting")."""
        said = inc.get("said") or []
        named = [s for s in said if not s.get("context")]
        loud = len(named) >= 2 or any(s.get("agreed") for s in named)
        backed = any(s.get("context") and rules.anchored(s.get("text") or "") for s in said)
        return len(named), loud or backed, loud

    def _fyi(self, inc: dict, types: set, home: bool, place: str) -> tuple[bool, str]:
        """Worth one quiet line? A real call in his radius - a crash, a
        reported fire, a gas leak, a break-in, an overdose - heard clearly
        enough to believe. A fire alarm or a disturbance only in his own town:
        elsewhere they are the commonest calls on the air and almost always
        nothing (two Natick fire alarms in a morning)."""
        label = inc.get("label") or "activity"
        if not _cfg("fyi", True):
            return False, f"{label} {place}: stored, not a kind that interrupts"
        sev = max((rules.KINDS[t].severity for t in types if t in rules.KINDS), default=0)
        if sev < int(_cfg("fyi_min_severity", 3)) and not (home and sev >= 2):
            return False, f"{label} {place}: stored, too routine to mention"
        return self._believed(inc, label, place)

    def _believed(self, inc: dict, label: str, place: str) -> tuple[bool, str]:
        """Was the call heard well enough to tell him even quietly? Used for the
        quiet feed and for an unconfirmed danger line alike."""
        # only the transmissions that named the call count: a follow-up line
        # ("on scene") says nothing about whether the call was heard right
        said = [s for s in (inc.get("said") or []) if not s.get("context")]
        if inc.get("paged") and said:
            # the dispatcher toned a station out: that it is a dispatch does
            # not rest on how well a machine heard the words after it
            return True, f"{label} {place} (paged {inc['paged']})"
        sure = [s for s in said if s.get("agreed") or float(s.get("conf") or 0) >= self.SURE]
        if not sure and len(said) < 2:
            return False, f"{label} {place}: stored, heard once and not clearly"
        # ...and one transmission alone must SAY something about the call - a
        # street, a unit, a fact, dispatch wording. A fluent line that only
        # contains the word ("I need a crash") waits for a second to agree
        # (his words, 2026-11-07: "That doesn't make sense does it? Fix that").
        if len(said) < 2 and not any(s.get("agreed") or rules.anchored(s.get("text") or "") for s in said):
            return False, f"{label} {place}: stored, heard once with nothing concrete in it"
        return True, f"{label} {place}"

    def _rejudge(self, since: float) -> int:
        """Read the day's incidents again with today's rules. The rules learn
        (a "residential fire activation" is an alarm, not a structure fire) and
        an incident named by an older reading would otherwise keep its wrong
        name for the twelve hours he can ask about it. Only incidents whose
        every transmission was kept whole are rebuilt. Returns how many changed."""
        changed = 0
        for inc in store.incidents_since(since):
            said = inc.get("said") or []
            if not inc.get("types") or not said:
                continue                 # a burst: nothing to read again
            # THE TOWN, read again: a town named as a destination used to move
            # a call ("transport her back to Framingham" put a Wayland PD
            # lookout "here in Framingham"). Where the call's channel has a
            # town, and no kept line names the call's town as where it is by
            # today's rules, the call goes back to its channel's town.
            lab = rules.parse_label(inc.get("channel") or "")
            home_of_channel = lab.town.name if lab.town else ""
            if home_of_channel and (inc.get("town") or "") != home_of_channel:
                named = {t for s in said for t in rules.read(s.get("text") or "", home_of_channel).towns}
                if (inc.get("town") or "") not in named:
                    t = geo.town(home_of_channel)
                    if t and store.update_incident(inc["id"], {"town": t.name, "miles": round(t.miles, 1)}):
                        log.info("scanner: #%d is in %s (its channel's town), not %s - a passing mention",
                                 inc["id"], t.name, inc.get("town"))
                        inc = store.incident(inc["id"]) or inc
                        changed += 1
            # More was heard than was kept: lines cut at 300 characters, the
            # ten-line cap, or an older build that kept six (hits counts every
            # transmission; a fire whose last six lines were stand-downs must
            # not be "closed" for want of its first eight - review 2026-11-07).
            if len(said) >= SAID_KEEP or int(inc.get("hits") or 0) > len(said) or \
                    any(len(s.get("text") or "") >= _cut_at(s) for s in said):
                # ...so only the address is checked, and only when the words it
                # came from are still here to read ("Master 3, Fox drive,
                # Charlie, Romeo" became 3 Fox Dr)
                key = (inc.get("street") or "").split()[:-1]
                words = set(re.findall(r"[a-z0-9']+", " ".join((s.get("text") or "").lower() for s in said)))
                if key and all(w in words for w in key) and not any(
                        rules.read(s.get("text") or "", inc.get("town") or "").street == inc.get("street")
                        for s in said):
                    if store.update_incident(inc["id"], {"address": "", "street": ""}):
                        log.info("scanner: #%d address %r is not an address by today's rules - dropped",
                                 inc["id"], inc.get("address"))
                        changed += 1
                continue
            types: set[str] = set()
            units: list[str] = []
            facts: list[str] = []
            address = street = ""
            for s in said:
                text = s.get("text") or ""
                rd = rules.read(text, inc.get("town") or "")
                types |= {t for t in rd.types if t in rules.KINDS}
                units = _union(units, rd.units, 10)
                facts = _union(facts, rules.details(text), 8)
                if not address and rd.address:
                    address, street = rd.address, rd.street
            if rules.PAGED in (inc.get("types") or []):
                types.add(rules.PAGED)       # given by the page, not read from words
            fields: dict = {}
            if types != set(inc["types"]):
                if not types:
                    fields = {"types": [], "status": "over", "decided": "today's rules no longer read this as a call"}
                    log.info("scanner: #%d (%s) is not a call by today's rules - closed", inc["id"], inc["label"])
                else:
                    ordered = sorted(types, key=lambda k: -rules.KINDS[k].severity)
                    top = rules.KINDS[ordered[0]]
                    fields = {"types": ordered, "kind": top.key, "label": rules.describe(ordered),
                              "severity": top.severity, "family": top.family}
                    log.info("scanner: #%d re-read by today's rules: %s -> %s", inc["id"], inc["label"], fields["label"])
            # "a crash on It About One Place" was still in his answers after the
            # address reader learned better: the place, the units and the facts
            # are read again too
            if address != (inc.get("address") or ""):
                fields.update(address=address, street=street)
                log.info("scanner: #%d address re-read: %r -> %r", inc["id"], inc.get("address"), address)
            if _union(inc.get("units") or [], units, 10) != (inc.get("units") or []):
                fields["units"] = _union(inc.get("units") or [], units, 10)
            if _union(inc.get("details") or [], facts, 8) != (inc.get("details") or []):
                fields["details"] = _union(inc.get("details") or [], facts, 8)
            if fields and store.update_incident(inc["id"], fields):
                changed += 1
        return changed

    def _lock(self, iid: int) -> asyncio.Lock:
        if len(self._locks) > 256:
            self._locks = {k: v for k, v in self._locks.items() if v.locked()}
        return self._locks.setdefault(iid, asyncio.Lock())

    async def _consider(self, iid: int, trigger: dict | None, escalated: str) -> None:
        async with self._lock(iid):
            await self._consider_locked(iid, trigger, escalated)

    async def _consider_locked(self, iid: int, trigger: dict | None, escalated: str) -> None:
        inc = store.incident(iid)
        if not inc:
            return
        tier, why = self.decide(inc)
        if tier is None:
            if why != inc["decided"]:
                store.update_incident(iid, {"decided": why})
                log.info("scanner: #%d not sent - %s", iid, why)
            return
        have, want = _RANK.get(inc["tier"] or "", 0), _RANK[tier]
        update = bool(escalated) and want == have and bool(inc["delivered_ts"])
        if want < have or (want == have and not update):
            return                                  # already told at this level
        if update and tier == FYI:
            # a quiet call that grew is the same message, edited - never a second one
            self._schedule_refresh(iid)
            return
        # The quiet feed goes out at once in the rules' own words - it must
        # never wait behind a model call; its plain-words summary follows,
        # edited in (_summary_after). A louder tier asks the model first.
        if tier != FYI and _cfg("llm_summary", True) and not (inc["burst"] and not inc["types"]):
            self._summary_ts[iid] = self.now()
            got = await self._summarise(inc, urgent=tier == URGENT)
            self._summary_n[iid] = len(inc.get("said") or [])
            self._keep_account(iid, len(inc.get("said") or []), got)
            if got:
                # already fact-checked: "" when nothing in it was supported
                store.update_incident(iid, {"summary": got.get("summary") or ""})
                if got.get("active") is False and tier != URGENT:
                    store.update_incident(iid, {"decided": "the model read the traffic as over"})
                    log.info("scanner: #%d held - the model read it as over: %r", iid, got["summary"])
                    return
            inc = store.incident(iid) or inc        # it may have moved while the model read it
            tier2, why = self.decide(inc)
            if tier2 is None or _RANK[tier2] < want:
                store.update_incident(iid, {"decided": why})
                log.info("scanner: #%d not sent after all - %s", iid, why)
                return
            tier = tier2
            update = update and _RANK[tier] == have   # it may have become worse than an update
        text, written = self.compose(inc, tier, escalated if update else "")
        now = self.now()
        # CLAIM, THEN SPEAK: the incident records what it is about to be sent
        # before anything goes out. If that write fails, nothing is sent.
        if not store.update_incident(iid, {"tier": tier, "delivered_ts": now, "decided": why, "pending": ""}):
            log.error("scanner: could not record #%d before sending it - not sending", iid)
            return
        try:
            if tier == FYI:
                # delivery's ALERT route (now, at the PC or on his phone) with
                # a kind notify_policy keeps silent and never interrupting,
                # under its own key so a later real ALERT is not "a repeat"
                r = await self._deliver(text, ALERT, key=f"scanner-fyi:{iid}",
                                        subject=f"scanner-fyi: {inc['label']} - {inc['town']}", written=written)
            else:
                r = await self._deliver(text, tier, key=f"scanner:{iid}",
                                        subject=f"scanner: {inc['label']} - {inc['town']}", written=written)
        except Exception:
            log.exception("scanner: delivery raised for #%d", iid)
            r = {"delivered": "nothing", "why": "delivery raised"}
        outcome, rwhy = str(r.get("delivered") or "nothing"), str(r.get("why") or "")
        store.record_sent(iid, tier, outcome, f"{why}; {rwhy}" if rwhy else why, now)
        log.info("scanner %s #%d -> %s (%s%s): %s", tier.upper(), iid, outcome, why,
                 f"; {rwhy}" if rwhy else "", text)
        if outcome not in SENT:
            revert = {"tier": inc["tier"] or "", "delivered_ts": inc["delivered_ts"] or 0}
            if rwhy == "said recently" or (rwhy == "hourly message budget spent" and tier != FYI):
                # delivery's repeat window - or its hourly ceiling, which must
                # never simply lose an emergency: retried when it opens
                revert["pending"] = tier
            store.update_incident(iid, revert)
            return
        msg_id = r.get("message_id") if outcome == "telegram" else None
        if msg_id:
            # the newest message for this call is the one the follow-ups edit
            old_mid = int(inc.get("msg_id") or 0)
            store.update_incident(iid, {"msg_id": int(msg_id), "edited_ts": now})
            self._edited[iid] = written
            if old_mid and old_mid != int(msg_id):
                # the quiet "Unconfirmed" line before it must not go on saying so
                self._spawn_task(self._close_old(old_mid, iid), name=f"scanner-close-{iid}")
            # anything heard while the send was in flight found no message to
            # edit; the identical-text check makes this free when nothing came
            self._schedule_refresh(iid)
            if tier == FYI and not (inc.get("burst") and not inc.get("types")):
                # a quiet call goes out at once with where it is; what is going
                # on follows in plain words, edited into the same message
                self._spawn_task(self._summary_after(iid), name=f"scanner-summary-{iid}")
        if outcome == "spoken":
            await self._card(inc, tier)
        if tier == URGENT and outcome == "telegram" and _cfg("send_audio", True) and trigger:
            # the recording sounds only if the alert itself did (the daily
            # sound budget may have sent it silently)
            await self._send_clip(inc, trigger, silent=not r.get("sound", True), reply_to=msg_id)
        elif tier == FYI and outcome == "telegram" and _cfg("fyi_audio", True) and trigger \
                and trigger.get("audio_path"):
            # the dispatch itself, silent, threaded under its line: the words
            # are a machine's guess at weak audio, the recording is not
            await self._send_clip(inc, trigger, silent=True, reply_to=msg_id, kind="scanner_fyi")

    async def _retry_pending(self) -> None:
        """An update delivery held as 'said recently' goes out once its repeat
        window has passed, if the incident is still live."""
        now = self.now()
        for inc in store.pending_incidents(now - 4 * 3600):
            tier = inc["pending"]
            gap = float(config.get("proactive", "urgent_repeat_minutes" if tier == URGENT else
                                   "repeat_cooldown_minutes", default=10 if tier == URGENT else 45)) * 60
            if inc["status"] != "active" or now - float(inc["last_ts"]) > gap + 900:
                store.update_incident(inc["id"], {"pending": ""})    # stale by the time it could go
                continue
            if now - store.last_sent_ts(inc["id"]) < gap:
                continue
            store.update_incident(inc["id"], {"pending": ""})
            esc = inc["escalations"][-1] if inc["escalations"] else "an update"
            self._spawn_task(self._consider(inc["id"], None, esc), name=f"scanner-retry-{inc['id']}")

    async def _deliver(self, text: str, tier: str, **kw) -> dict:
        if self.deliverer is not None:
            return await self.deliverer(text, tier, **kw)
        from delivery import delivery
        return await delivery.deliver(text, tier, **kw)

    async def _summarise(self, inc: dict, urgent: bool) -> dict | None:
        """The model's line for a call, already fact-checked: its "summary" is
        only the sentences what was heard supports ("" when none survive).
        "active" is the model's own reading and stands either way."""
        if self.summariser is not None:
            try:
                got = await self.summariser(inc, urgent)
            except Exception:
                log.warning("scanner: the summariser failed", exc_info=True)
                return None
            if got:
                got = dict(got, summary=checked(got.get("summary") or "", inc),
                           account=checked(got.get("account") or "", inc))
            return got
        return await self._llm_summary(inc, urgent)

    def _keep_account(self, iid: int, n: int, got: dict | None) -> None:
        """The longer account written with the summary, kept for "tell me more"
        - unless the model judged the rest of the audio too garbled to tell:
        replayed on real calls, its account of a garbled one was the garble
        again in other clothes ("First one, Amy P D, at six hundred main
        street"), which is exactly what he asked not to be sent."""
        if got and got.get("clear") is False:
            self._more[iid] = (n, "")
        elif got and got.get("account"):
            self._more[iid] = (n, got["account"])
            if len(self._more) > 64:
                self._more.pop(next(iter(self._more)))

    async def _llm_ask(self, messages: list[dict], timeout: float) -> str:
        """One temp-0 side call; raises asyncio.TimeoutError past `timeout`.
        The asker stub stands in for it in the gates."""
        if self.asker is not None:
            return await asyncio.wait_for(self.asker(messages), timeout=timeout)
        from llm.provider import local_llm
        out = ""

        async def go() -> None:
            nonlocal out
            # a reasoning model thinks out of the same budget it answers from
            async for ch in local_llm.stream(messages, max_tokens=1200, sampling={"temperature": 0.0}):
                out += ch.text
                if ch.done:
                    break
        await asyncio.wait_for(go(), timeout=timeout)
        return out

    async def _llm_summary(self, inc: dict, urgent: bool) -> dict | None:
        """One fact-checked side call for an incident. Anything but an URGENT
        waits for a quiet moment first (never beside one of his turns) and
        writes the longer account for "tell me more" in the same call; an
        URGENT does not wait, writes the summary alone and gets eight seconds,
        or goes out in the rules' own words."""
        try:
            if not urgent and self.asker is None:
                from llm.provider import wait_for_quiet
                await wait_for_quiet(max_wait=45.0)    # never beside one of his turns
            got = await write_checked(inc, "summary" if urgent else "both", self._llm_ask,
                                      8.0 if urgent else 45.0)
        except Exception:
            log.warning("scanner: the summary side call failed", exc_info=True)
            return None
        if got is None:
            log.info("scanner: no summary for #%s in time; using the rules' words", inc.get("id"))
            return None
        log.info("scanner: summary for #%s -> %r%s", inc.get("id"), got.get("summary"),
                 f" (cut as unsupported: {got['dropped']!r})" if got.get("dropped") else "")
        return got

    # ------------------------------------------------------------ words for him
    def _where(self, inc: dict) -> str:
        town, home = inc.get("town") or "", home_town().name
        if town == home:
            return f"here in {town}"
        return f"in {town}, {miles_words(inc.get('miles'))}" if town else ""

    def compose(self, inc: dict, tier: str, update: str = "") -> tuple[str, str]:
        """(spoken, written) for one incident."""
        address = inc.get("address") or ""
        spoken_at = _place(address)
        where = self._where(inc)
        label = inc.get("label") or "activity"
        lab = rules.parse_label(inc.get("channel") or "")
        unconfirmed = self._unconfirmed(inc)
        lead = "Scanner, sir: " if tier == URGENT else \
            ("From the scanner, a paged dispatch: " if unconfirmed and inc.get("paged") else
             "From the scanner, unconfirmed: " if unconfirmed else "From the scanner: ")
        town = inc.get("town") or ""
        unnamed = (inc.get("types") or []) == [rules.PAGED]
        if inc.get("burst") and not inc.get("types"):
            spoken = f"{lab.spoken} is unusually busy, sir: {inc.get('summary') or 'far more traffic than usual'}. " \
                     "I can't make out the words yet."
        elif update:
            noun = {"fire": "the fire", "traffic": "the crash", "police": "the police call",
                    "ems": "the medical call"}.get(inc.get("family") or "", "the call")
            spoken = f"Update from the scanner on {noun}{spoken_at}" + (f" {where}" if where else "") + f": {update}."
        elif inc.get("summary") and checked(inc["summary"], inc):
            s = checked(inc["summary"], inc).rstrip(".")
            if town and town.lower() not in s.lower():
                s += f", {where}"
            elif town and town != home_town().name and "mile" not in s:
                s += f", {miles_words(inc.get('miles'))}"
            spoken = lead + s + "."
        elif unnamed:
            spoken = f"From the scanner: {lab.spoken} toned a station out{spoken_at}" + \
                (f" {where}" if where else "") + ". I couldn't make out what for."
        else:
            spoken = lead + f"{label}{spoken_at}" + (f" {where}" if where else "") + "."
        extra = self._spoken_details(inc) if not update else ""
        return spoken + (f" {extra}" if extra else ""), self.written(inc, tier, update)

    def _spoken_details(self, inc: dict, most: int = 2) -> str:
        """One more sentence for the voice, from what was actually heard:
        "Two vehicles, minor injuries. Sudbury Fire sent Car 3 and Ambulance 1."
        Empty when nothing more was heard. The phone's line takes every fact
        (`most`); the voice, two."""
        if inc.get("burst") and not inc.get("types"):
            return ""
        facts = [d for d in (inc.get("details") or []) if d != "units on scene"][:most]
        out = _cap(", ".join(facts)) + "." if facts else ""
        units = (inc.get("units") or [])[:3]
        if units:
            who = rules.parse_label(inc.get("channel") or "").spoken
            names = units[0] if len(units) == 1 else ", ".join(units[:-1]) + f" and {units[-1]}"
            out += (" " if out else "") + f"{who} sent {names}."
        return out

    def written(self, inc: dict, tier: str, update: str = "") -> str:
        """The phone's version, in his words (2026-11-08): "the heading, the
        where, and the summary of it" - never the raw transcript ("I don't
        want the actual transmission. I want Jarvis to tell me what's going
        on"). The summary is the model's, fact-checked again here so nothing
        unsupported can reach him whatever is stored; until it is ready, or
        when nothing in it survives, the line is built from the facts the
        rules found word for word. Everything else - the units, every fact,
        the order it happened in - is his on "tell me more". Edited in place
        as the call develops."""
        home = home_town().name
        town = inc.get("town") or "?"
        dist = "home" if town == home else f"{float(inc.get('miles') or 0):.0f} mi"
        grade = "" if tier == FYI else f" - {tier.upper()}"
        lines = [f"SCANNER{grade} - {town} ({dist})"]
        if update:
            lines.append(f"UPDATE: {update}.")
        address = inc.get("address") or ""
        if inc.get("burst") and not inc.get("types"):
            lines.append(f"Unusual activity on {inc.get('channel')}: {inc.get('summary') or 'far more traffic than usual'}")
        else:
            what = (inc.get("label") or "activity") + (f" - {address}" if address else "")
            if (inc.get("types") or []) == [rules.PAGED]:
                # toned out, and the words would not name it
                lines.append("Paged dispatch - I couldn't make out the call type" + (f" - {address}" if address else ""))
            elif inc.get("paged") and self._unconfirmed(inc):
                # one transmission, but the dispatcher's own page came with it
                lines.append(f"Paged dispatch: {what} (one transmission so far)")
            else:
                # one transmission is evidence, not a conclusion: said so, plainly
                lines.append(f"Unconfirmed: {what} (one transmission so far)" if self._unconfirmed(inc) else _cap(what))
        roads = [r for r in (inc.get("roads") or []) if r not in address]
        if address or roads:
            lines.append("Where: " + ", ".join(x for x in [address] + roads if x))
        said = inc.get("said") or []
        if said:
            told = self.told(inc)
            if told:
                lines.append(told)
        elif inc.get("burst"):
            lines.append("No words yet (digital or unclear audio).")
        starts = [float(x) for x in [inc.get("first_ts")] + [s.get("ts") for s in said[:1]] if x]
        first = min(starts) if starts else None        # the first word heard, not when it ended
        if first:
            n = int(inc.get("hits") or len(said) or 1)
            foot = f"First heard {_hm(float(first))} - {n} transmission{'s' if n != 1 else ''}"
            if inc.get("status") == "over":
                foot += f" - over by {_hm(float(inc.get('last_ts') or first))}"
            elif float(inc.get("last_ts") or first) - float(first) >= 60:
                foot += f" - latest {_hm(float(inc['last_ts']))}"
            lines.append(foot)
        # ONE Telegram message (4096 characters): the id kept for edits must be
        # the whole message, never the last of several chunks
        return "\n".join(lines)[:self.MAX_WRITTEN]

    MAX_WRITTEN = 3900

    def told(self, inc: dict) -> str:
        """What is going on, for his message: the model's summary as far as
        what was heard supports it, plus any fact the rules heard word for
        word that it left out (an injury must never be lost to a tidy
        sentence); without a summary, the rules' own sentence of facts."""
        summary = checked(inc.get("summary") or "", inc) if inc.get("summary") else ""
        if not summary:
            return self._spoken_details(inc, most=8)
        said = factcheck._words(summary)
        said += [x for w in said for x in factcheck.IMPLIES.get(w, "").split()]   # "two car crash" is 2 vehicles
        have = {factcheck._stem(w) for w in said}
        left = [d for d in (inc.get("details") or []) if d != "units on scene"
                and not any(factcheck._stem(w) in have for w in factcheck._words(d) if len(w) >= 4)]
        return summary + (f" Also heard: {'; '.join(left)}." if left else "")

    # ------------------------------------------------------------ "tell me more"
    FOLLOWUP_WINDOW_S = 6 * 3600      # a bare "tell me more" reaches back this far
    MORE_BUDGET_S = 40.0              # the model's time for an account he is waiting on
    REPLY_WINDOW_S = 24 * 3600        # a reply to the call's own message, this far

    def call_for_followup(self, reply_mid: int | None, last_sent_id: int | None) -> dict | None:
        """The call his "tell me more" is about. A Telegram reply names it:
        his reply to the call's message, its recording or an earlier answer.
        Without a reply it is the call only if that call is the last thing
        JARVIS sent him - after a reminder or the news, "tell me more" is
        about those, and goes to the conversation as before."""
        now = self.now()
        if reply_mid:
            return store.incident_by_message(int(reply_mid), now - self.REPLY_WINDOW_S)
        if last_sent_id:
            return store.incident_by_message(int(last_sent_id), now - self.FOLLOWUP_WINDOW_S)
        return None

    def note_more(self, iid: int, mid: int | None) -> None:
        """His "tell me more" answer is about this call too: a second "more",
        or a reply to the answer, finds it."""
        if mid:
            store.update_incident(iid, {"more_mid": int(mid)})

    def latest_told(self) -> dict | None:
        """The call he was most recently sent (for "tell me more about that
        call" asked aloud), within the follow-up window."""
        now = self.now()
        told = [i for i in store.incidents_since(now - self.FOLLOWUP_WINDOW_S)
                if i.get("tier") and float(i.get("delivered_ts") or 0) >= now - self.FOLLOWUP_WINDOW_S]
        return max(told, key=lambda i: float(i["delivered_ts"])) if told else None

    async def tell_more(self, iid: int) -> tuple[str, str]:
        """(written, spoken): everything known about one call, as it stands
        now - where, whether it is still going, everything the radio said in
        plain words (fact-checked, like the summary), the units and the facts
        heard word for word. Never the raw transcript; the recording is
        under his message."""
        inc = store.incident(iid)
        if not inc:
            return "I can't find that call any more, sir.", "I can't find that call any more, sir."
        home = home_town().name
        town = inc.get("town") or "?"
        dist = "home" if town == home else f"{float(inc.get('miles') or 0):.0f} mi"
        address = inc.get("address") or ""
        head = f"{_cap(inc.get('label') or 'activity')}{' at ' + address if address else ''} - {town} ({dist})."
        said = inc.get("said") or []
        # the first word heard, exactly as his message says it ("First heard")
        starts = [float(x) for x in [inc.get("first_ts")] + [s.get("ts") for s in said[:1]] if x]
        first = min(starts) if starts else self.now()
        last = max(float(inc.get("last_ts") or first), first)
        n = int(inc.get("hits") or len(said) or 1)
        if inc.get("status") == "over":
            state = f"It's over: {n} transmission{'s' if n != 1 else ''}, {_hm(first)} to {_hm(last)}."
        elif self._unconfirmed(inc):
            state = f"Heard once, at {_hm(first)} - nothing has backed it up yet."
        elif last - first >= 60:
            state = f"Still going: {n} transmissions since {_hm(first)}, the latest at {_hm(last)}."
        else:
            state = f"{n} transmissions at {_hm(first)}."
        account = ""
        stuck = False          # the model never answered - which is not the audio's fault
        if inc.get("burst") and not inc.get("types"):
            account = "It's only traffic so far - no words I can make out (digital or unclear audio)."
        elif said:
            cached = self._more.get(iid)
            if cached and cached[0] == len(said):
                account = cached[1]
            else:
                got = None
                try:
                    # forty seconds: the first live try (2026-11-08) ran beside
                    # the start-up cache warm-up and lost a 25-second race
                    got = await write_checked(inc, "more", self._llm_ask, self.MORE_BUDGET_S)
                except Exception:
                    log.warning("scanner: the account of #%s failed", iid, exc_info=True)
                account = "" if (got or {}).get("clear") is False else ((got or {}).get("account") or "")
                log.info("scanner: tell-me-more for #%s -> %r%s%s", iid, account,
                         " (no answer from the model in time)" if got is None else
                         " (judged too garbled)" if got.get("clear") is False else "",
                         f" (cut as unsupported: {got['dropped']!r})" if got and got.get("dropped") else "")
                self._keep_account(iid, len(said), got)
                stuck = got is None
            if not account:
                # HONEST ABOUT WHY (2026-11-08): the first live tries ran
                # beside the start-up warm-up, got no answer in time, and said
                # "the audio is too garbled" - which nobody had judged
                account = (self.told(inc) + " " if self.told(inc) else "") + (
                    "I couldn't work out the rest just now, sir - ask me again in a minute." if stuck else
                    "I can't make out more than that, sir - the audio is too garbled.")
        written = [head, state] + ([account] if account else [])
        if inc.get("units"):
            written.append("Units: " + ", ".join(inc["units"]))
        facts = [d for d in (inc.get("details") or [])]
        if facts:
            written.append("Heard: " + "; ".join(facts))
        spoken = " ".join(x for x in (head, state, account) if x)
        return "\n".join(written), spoken

    async def _card(self, inc: dict, tier: str) -> None:
        """The incident on the HUD while it is said (the brief card; no new UI)."""
        try:
            lines = [_cap(inc.get("label") or "activity") + (f" - {inc['address']}" if inc.get("address") else ""),
                     f"{inc.get('town') or '?'} - {miles_words(inc.get('miles'))} - {inc.get('channel') or ''}"]
            if inc.get("units"):
                lines.append("Units: " + ", ".join(inc["units"][:6]))
            if inc.get("details"):
                lines.append("Known: " + "; ".join(inc["details"][:4]))
            for s in (inc.get("said") or [])[-2:]:
                lines.append(f'"{s.get("text", "")}" ({_hm(s.get("ts", 0))})')
            if inc.get("burst") and inc.get("summary") and not inc.get("types"):
                lines.append(inc["summary"])
            await bus.emit("brief", title="Scanner", eyebrow=f"THE SCANNER - {tier.upper()}",
                           sections=[{"title": inc.get("town") or "Scanner", "lines": lines}])
        except Exception:
            log.debug("scanner: card failed", exc_info=True)

    async def _send_clip(self, inc: dict, row: dict, silent: bool = False, reply_to: int | None = None,
                         kind: str = "") -> None:
        """The transmission itself, to his phone: an URGENT's with a sound
        (unless the alert went silently), a quiet call's silently, threaded
        under its message. It is a message like any other: it answers to the
        hourly ceiling of its kind, and a clip that sounds spends the day's
        sound budget (review of 2026-11-07)."""
        if self.clip_sender is None:
            from delivery import delivery
            if not delivery.has_budget(ALERT if kind else URGENT, kind):
                log.info("scanner: clip of hit %s for #%s held - the hourly ceiling is reached",
                         row.get("id"), inc.get("id"))
                return
        audio = await self._load_audio(row)
        audio, _ = normalise_audio(audio) if audio is not None else (None, 0.0)
        if audio is None:
            return
        folder = Path(tempfile.gettempdir()) / "jarvis-scanner"
        folder.mkdir(exist_ok=True)
        path = folder / f"hit-{row['id']}.wav"
        try:
            await asyncio.to_thread(write_wav, path, audio)
            caption = f"{inc.get('channel') or ''} {_hm(row['ts'])} - {inc.get('label')}"[:200]
            if self.clip_sender is not None:
                ok = await self.clip_sender(str(path), caption)
            else:
                from remote_telegram import telegram
                ok = await telegram.send_voice_clip(str(path), caption, silent=silent, reply_to=reply_to)
                if ok:
                    if telegram.last_clip_id:
                        # read straight after the send (no await between): a
                        # reply to the recording is about this call too
                        store.update_incident(int(inc["id"]), {"clip_mid": int(telegram.last_clip_id)})
                    from delivery import delivery
                    delivery.note_sent(kind)        # a second message on his phone counts
                    if not silent:
                        delivery.note_sounded()
            log.info("scanner: clip of hit %s for #%s %s", row["id"], inc.get("id"), "sent" if ok else "NOT sent")
        except Exception:
            log.warning("scanner: could not send the clip", exc_info=True)
        finally:
            try:
                path.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ the morning summary
    MORNING_KEY = "scanner:morning"

    async def _morning_summary(self, now: float) -> bool:
        """One silent message at the end of his night listing what the radio
        carried (his answer, 2026-11-07: "send silently plus in morning
        summary"). Once a day, claimed before it is sent so a restart cannot
        send it twice; not after noon (a summary at 3 PM is not a morning).
        True when it went out."""
        if not _cfg("morning_summary", True):
            return False
        # HIS NIGHT IS HIS QUIET HOURS (proactive.quiet_start/end; his call,
        # 2026-11-07: awake 05:30-22:30). The summary covers exactly that window
        # and arrives as it ends, unless scanner.morning_summary_at says when.
        night = _night_window()
        if night is None:
            return False
        (sh, sm), (eh, em) = night
        try:
            hh, mm = (int(x) for x in str(_cfg("morning_summary_at", f"{eh:02d}:{em:02d}")).split(":"))
        except ValueError:
            return False
        t = dt.datetime.fromtimestamp(now)
        due = t.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t < due or t.hour >= 12:
            return False
        day = t.strftime("%Y-%m-%d")
        import volatile
        if (volatile.get(self.MORNING_KEY) or {}).get("value", {}).get("day") == day:
            return False
        if not volatile.put(self.MORNING_KEY, {"day": day}, source="scanner"):
            return False                        # claim first: no claim, no message
        end_dt = t.replace(hour=eh, minute=em, second=0, microsecond=0)
        if end_dt > due:
            end_dt = due                        # a summary asked for before the night ends covers up to then
        start_dt = end_dt.replace(hour=sh, minute=sm)
        if start_dt >= end_dt:
            start_dt -= dt.timedelta(days=1)    # the night began yesterday evening
        spoken, written = self.night_summary(start_dt.timestamp(), end_dt.timestamp())
        r = await self._deliver(spoken, ALERT, key=f"scanner-night:{day}",
                                subject="scanner-fyi: the night's calls", written=written)
        log.info("scanner: morning summary -> %s: %s", r.get("delivered"), spoken)
        return str(r.get("delivered")) in SENT

    def hearing_now(self, now: float) -> dict | None:
        """The deaf stretch still going on now, or None: {start, n, recorded}."""
        hits = store.hits_since(now - 12 * 3600, until=now - DEAF_SETTLE_S)
        spans = deaf_spans(hits)
        if not (spans and hits):
            return None
        last = hits[-1]
        return spans[-1] if spans[-1]["end"] == float(last.get("end_ts") or last["ts"]) else None

    async def _check_hearing(self, now: float) -> str:
        """Tell him once, silently, when I stop being able to hear the scanner,
        and once when I can again (his answer, 2026-11-08: "Warn me when sound
        stops"). Claimed in volatile before it is sent, like the morning
        summary, so a restart cannot say it twice. Returns "lost", "back" or ""."""
        if not _cfg("hearing_watch", True) or now - self._last_hear < 60:
            return ""
        self._last_hear = now
        import volatile
        state = (volatile.get(DEAF_KEY) or {}).get("value") or {}
        h = self.health or {}
        lost = None
        if self.up is False or (self.up and h.get("connected") is False):
            if self._lost_since is None:
                self._lost_since = now
            if now - self._lost_since >= LOST_AFTER_S:
                why = ("the scanner console isn't answering" if self.up is False
                       else "the scanner isn't connected to the PC")
                lost = (self._lost_since,
                        f"I've lost the scanner, sir: {why} since {_hm(self._lost_since)}. "
                        "Check its USB cable and that the scanner is switched on.")
        else:
            self._lost_since = None
            deaf = self.hearing_now(now)
            if deaf:
                if deaf["silent"]:
                    lost = (deaf["start"],
                            f"I can't hear the scanner, sir: {deaf['n']} transmissions since {_hm(deaf['start'])} "
                            "and no sound reached the PC. Check the audio cable at both ends and the scanner's "
                            f"volume - {GOOD_VOLUME} is what works.")
                else:
                    lost = (deaf["start"],
                            f"I can't make out the scanner, sir: {deaf['n']} transmissions since "
                            f"{_hm(deaf['start'])} and not one word I could understand - the sound is too faint. "
                            f"Turn the scanner's volume up to {GOOD_VOLUME} and check the cable is pushed all the way in.")
        if lost and not state.get("since"):
            since, text = lost
            if not volatile.put(DEAF_KEY, {"since": since}, source="scanner"):
                return ""                               # claim first: no claim, no message
            if await self._pause_doctor(now):
                text += " I've paused the scan doctor, so it can't judge channels I can't hear."
            r = await self._deliver(text, ALERT, key=f"scanner-fyi:hearing:{int(since)}",
                                    subject="scanner-fyi: hearing")
            log.warning("scanner: can't hear it -> %s: %s", r.get("delivered"), text)
            return "lost"
        if not lost and state.get("since"):
            # back only on real evidence: connected, and two understood
            # transmissions in the last half hour, so a lone word in a dead
            # stretch cannot flap it
            heard = [x for x in store.hits_since(now - 1800) if (x.get("transcript") or "").strip()]
            if not (self.up and h.get("connected") is not False and len(heard) >= 2):
                return ""
            if not volatile.put(DEAF_KEY, {}, source="scanner"):
                return ""
            text = (f"I can hear the scanner again, sir. I couldn't from {_hm(float(state['since']))} "
                    f"to {_hm(now)}.")
            if await self._resume_doctor():
                text += " The scan doctor is running again."
            r = await self._deliver(text, ALERT, key=f"scanner-fyi:hearing-back:{int(float(state['since']))}",
                                    subject="scanner-fyi: hearing")
            log.info("scanner: hearing it again -> %s", r.get("delivered"))
            return "back"
        return ""

    # ------------------------------------------------------------ the scan doctor, watched
    async def _pause_doctor(self, now: float) -> bool:
        """Stop the console's scan optimiser while the scanner can't be heard
        (it deletes channels it cannot understand), undoing the settings it
        was in the middle of trying. True when it was running and is stopped."""
        if self.up is not True:
            return False
        try:
            import volatile
            client = self._ensure_client()
            st = await client.doctor_status()
            if not (isinstance(st, dict) and st.get("running")):
                return False
            if not volatile.put(DOCTOR_PAUSED_KEY, {"apply": bool(st.get("apply")), "at": now}, source="scanner"):
                return False
            ok = await client.doctor_stop(revert=True)
            if not ok:
                volatile.put(DOCTOR_PAUSED_KEY, {}, source="scanner")
            log.warning("scanner: scan doctor %s while the scanner can't be heard",
                        "paused" if ok else f"could NOT be paused ({client.last_error})")
            return ok
        except Exception:
            log.warning("scanner: could not pause the scan doctor", exc_info=True)
            return False

    async def _resume_doctor(self) -> bool:
        """Carry the scan doctor on if - and only if - I was the one who paused it."""
        try:
            import volatile
            paused = (volatile.get(DOCTOR_PAUSED_KEY) or {}).get("value") or {}
            if "apply" not in paused:
                return False
            ok = await self._ensure_client().doctor_start(bool(paused["apply"]))
            if ok:
                volatile.put(DOCTOR_PAUSED_KEY, {}, source="scanner")
            log.info("scanner: scan doctor %s now that the scanner can be heard", "resumed" if ok else "NOT resumed")
            return ok
        except Exception:
            log.warning("scanner: could not resume the scan doctor", exc_info=True)
            return False

    async def _check_doctor(self, now: float) -> str:
        """Every half hour: tell him (silently, once) what the scan doctor has
        removed since I last looked, and once a day if his own town has no
        fire channel on the scanner or it has gone silent while the rest of
        the scanner is busy. Returns "removed", "home" or ""."""
        if not _cfg("doctor_watch", True) or self.up is not True or now - self._last_doctor < DOCTOR_EVERY_S:
            return ""
        self._last_doctor = now
        import volatile
        client = self._ensure_client()
        home = home_town().name
        out = ""
        rep = await client.doctor_report()
        if isinstance(rep, dict) and isinstance(rep.get("changes"), list):
            removes = [c for c in rep["changes"] if isinstance(c, dict) and c.get("kind") == "remove"
                       and isinstance(c.get("before"), dict) and c["before"].get("name")]
            newest = max([float(c.get("at") or 0) for c in rep["changes"] if isinstance(c, dict)] + [0.0])
            seen = (volatile.get(DOCTOR_SEEN_KEY) or {}).get("value") or {}
            if "at" not in seen:
                # the first look only marks the place: history is not news
                volatile.put(DOCTOR_SEEN_KEY, {"at": newest}, source="scanner")
            else:
                new = [c for c in removes if float(c.get("at") or 0) > float(seen["at"])]
                if newest > float(seen["at"]) and volatile.put(DOCTOR_SEEN_KEY, {"at": newest}, source="scanner") and new:
                    text = doctor_removed_text(new, home)
                    r = await self._deliver(text, ALERT, key=f"scanner-fyi:doctor:{int(newest)}",
                                            subject="scanner-fyi: doctor")
                    log.warning("scanner: the scan doctor removed %d channel(s) -> %s: %s", len(new),
                                r.get("delivered"), text)
                    out = "removed"
        st = await client.state()
        if isinstance(st, dict) and isinstance(st.get("channels"), list) and st["channels"]:
            mine = home_fire_channels(st["channels"], home)
            gap = ""
            if not mine:
                self._home_watched = None
                gap = (f"Nothing on the scanner is tuned to {home} Fire, sir, so I can't hear fire calls in your "
                       "own town. The channel has been removed - it needs putting back in the console.")
            elif self._home_watched is None:
                self._home_watched = now          # silence is counted from when I began watching it
            elif now - self._home_watched >= HOME_SILENT_S and not self.hearing_now(now) \
                    and not any(store.count_hits(n, now - HOME_SILENT_S) for n in mine):
                others = len(store.hits_since(now - HOME_SILENT_S))
                if others >= HOME_SILENT_OTHERS:
                    gap = (f"I haven't heard {home} Fire in a day and a half, sir, though the scanner logged "
                           f"{others} transmissions elsewhere. Its channel may be mistuned - worth a look in the console.")
            self.home_gap = gap
            day = dt.datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            if gap and (volatile.get(HOME_GAP_KEY) or {}).get("value", {}).get("day") != day \
                    and volatile.put(HOME_GAP_KEY, {"day": day}, source="scanner"):
                r = await self._deliver(gap, ALERT, key=f"scanner-fyi:home-gap:{day}", subject="scanner-fyi: home")
                log.warning("scanner: home town not covered -> %s: %s", r.get("delivered"), gap)
                out = out or "home"
        return out

    def night_summary(self, start: float, end: float) -> tuple[str, str]:
        """(spoken, written) for the night between start and end."""
        home = home_town().name
        head = f"SCANNER - overnight, {_hm(start)} to {_hm(end)}"
        hits = store.hits_since(start, until=end)
        if not hits:
            # "Nothing on the scanner overnight" (2026-11-08, 5:30 AM) - sent
            # while the scanner was unplugged. A night with not one transmission
            # on seventy channels is a scanner that wasn't listening.
            msg = ("The scanner console was offline overnight, so I have nothing from the radio."
                   if self.up is False else
                   "The scanner logged nothing at all overnight, sir - it was off, unplugged or not connected, "
                   "so I can't tell you the night was quiet.")
            return msg, f"{head}\n{msg}"
        note = _hearing_note(deaf_spans(hits))
        gap = _gap_note(_quiet_gaps(hits, start, end))
        # what BEGAN in the night: an afternoon busy spell still being touched
        # at 10:35 PM is not overnight news (the 2026-11-08 brief said it was)
        incs = [i for i in store.incidents_since(start) if start <= float(i["first_ts"]) < end
                and (i.get("types") or i.get("burst"))]
        incs.sort(key=lambda i: float(i["first_ts"]))
        calls = [i for i in incs if i.get("types")]
        busy = [i for i in incs if i.get("burst") and not i.get("types")]
        n = len(hits)
        lines = [head, f"{n} transmissions logged; " + (f"{len(calls)} call{'s' if len(calls) != 1 else ''}:"
                                                         if calls else "nothing worth a message.")]
        for i, extra in enumerate(x for x in (note, gap) if x):
            lines.insert(1 + i, extra)
        for i in calls[:25]:
            town = i.get("town") or "?"
            dist = "home" if town == home else f"{float(i.get('miles') or 0):.0f} mi"
            facts = "; ".join([d for d in (i.get("details") or []) if d != "units on scene"][:3])
            lines.append(f"{_hm(float(i['first_ts']))}  {_cap(i.get('label') or 'a call')}"
                         + (f" - {i['address']}" if i.get("address") else "") + f", {town} ({dist})"
                         + (f" - {facts}" if facts else ""))
        if len(calls) > 25:
            lines.append(f"...and {len(calls) - 25} more")
        if busy:
            lines.append("Busy spells: " + ", ".join(
                f"{rules.parse_label(i.get('channel') or '').spoken} {_hm(float(i['first_ts']))}" for i in busy[:6]))
        if calls:
            said = [f"{i.get('label')} in {i.get('town')}" if i.get("town") != home else f"{i.get('label')} here"
                    for i in calls[:3]]
            joined = said[0] if len(said) == 1 else ", ".join(said[:-1]) + f" and {said[-1]}"
            spoken = f"Overnight on the scanner, sir: {joined}" + (f", and {len(calls) - 3} more" if len(calls) > 3 else "") + "."
            if note:
                spoken += " " + note
        elif note:
            spoken = f"I can't call it a quiet night on the scanner, sir. {note}"
        else:
            spoken = f"A quiet night on the scanner, sir: {n} transmissions, nothing worth a message." + \
                (f" {gap}" if gap else "")
        return spoken, "\n".join(lines)[:self.MAX_WRITTEN]

    # ------------------------------------------------------------ for the briefs
    def _worth(self, inc: dict) -> bool:
        types = set(inc.get("types") or [])
        return (int(inc.get("severity") or 0) >= 5 or bool(inc.get("burst"))
                or bool(inc.get("roads") and types & (rules.TRAFFIC | {"road_closure"})))

    def _item(self, inc: dict) -> str:
        at = _place(inc.get("address") or "")
        if inc.get("burst") and not inc.get("types"):
            s = f"a busy spell on {rules.parse_label(inc.get('channel') or '').spoken} around {_hm(inc['first_ts'])}"
        else:
            s = f"{inc.get('label')}{at}" + (f" in {inc['town']}" if inc.get("town") else "")
        if inc.get("status") == "over":
            s += f" (over by {_hm(inc['last_ts'])})"
        return s

    def brief_line(self, since_ts: float) -> str:
        """One line for a brief: "Overnight on the scanner: 23 calls; worth
        knowing: a working fire at 45 Union Avenue in Framingham (over by
        1:10 AM)." Empty when the scanner heard nothing and was not down."""
        now = self.now()
        hits = store.hits_since(since_ts, until=now + 1)
        if not hits:
            if self.up is False and self.down_since and self.down_since <= since_ts + (now - since_ts) / 2:
                return "The scanner console was offline, so I have nothing from the radio."
            if now - since_ts >= NIGHT_GAP_S:
                return (f"The scanner logged nothing at all since {_hm(since_ts)} - it was off, unplugged "
                        "or not connected, so I can't say it was quiet.")
            return ""
        note = " ".join(x for x in (_hearing_note(deaf_spans(hits)), _gap_note(_quiet_gaps(hits, since_ts, now))) if x)
        start, end = dt.datetime.fromtimestamp(since_ts), dt.datetime.fromtimestamp(now)
        hours = (now - since_ts) / 3600
        if end.hour < 12 and hours >= 5 and (start.date() < end.date() or start.hour < 5):
            lead = "Overnight on the scanner"
        elif hours <= 1.5:
            lead = "In the last hour on the scanner"
        else:
            lead = f"On the scanner since {_hm(since_ts)}"
        # TRANSMISSIONS, NOT CALLS: "Overnight on the scanner: 231 calls"
        # (2026-11-08) was 231 key-ups, most of them one dispatcher; and only
        # what began in the window - that brief also offered yesterday's 3:54 PM
        n = len(hits)
        line = f"{lead}: {n} transmission{'s' if n != 1 else ''}"
        incs = [i for i in store.incidents_since(since_ts)
                if float(i["first_ts"]) >= since_ts and self._worth(i)]
        incs.sort(key=lambda i: (-int(i.get("severity") or 0), float(i.get("miles") or 99)))
        # A BUSY SPELL IS A COUNT, NOT NEWS: the 2026-11-11 morning brief gave
        # two of its three "worth knowing" places to "a busy spell on Natick
        # Fire around 12:27 AM". Calls that could be named are named; spells
        # with no words are counted in five words at the end.
        spells = [i for i in incs if i.get("burst") and not i.get("types")]
        incs = [i for i in incs if not (i.get("burst") and not i.get("types"))]
        busy = f"{len(spells)} busy spell{'s' if len(spells) != 1 else ''} with no words I could make out" \
            if spells else ""
        if incs:
            line += "; worth knowing: " + ", ".join(self._item(i) for i in incs[:3])
            if len(incs) > 3:
                line += f", and {len(incs) - 3} more"
            if busy:
                line += f"; also {busy}"
        elif busy:
            line += f"; nothing I could name, {busy}"
        elif not note:
            busiest = Counter(rules.parse_label(h["name"]).spoken for h in hits).most_common(1)
            line += "; nothing worth stopping for" + (f", busiest was {busiest[0][0]}" if busiest else "")
        return line + "." + (f" {note}" if note else "")

    def status(self) -> dict:
        now = self.now()
        h = self.health or {}
        audio = h.get("audio") if isinstance(h.get("audio"), dict) else {}
        last = store.last_hit()
        day, day_words = store.count_since(now - 86400, until=now + 1)
        return {
            "enabled": bool(_cfg("enabled", True)),
            "console_up": bool(self.up),
            "url": self._ensure_client().base_url,
            "version": h.get("version"),
            "connected": h.get("connected"),
            "scanning": h.get("scanning"),
            "port": h.get("port"),
            "audio": {k: audio.get(k) for k in ("enabled", "device", "recording", "recent_peak_dbfs", "weak_input")},
            "last_hit_ts": (last or {}).get("ts") or h.get("last_hit_ts"),
            "last_hit_channel": (last or {}).get("name"),
            "hits_24h": day,
            "transcribed_24h": day_words,
            "incidents_24h": len(store.incidents_since(now - 86400)),
            "queue": len(self._queue),
            "spawns": self.spawns,
            "last_spawn_ts": self.last_spawn if self.last_spawn > 0 else None,
            "down_since": self.down_since if self.up is False else None,
            "last_error": self._ensure_client().last_error,
            "stats": dict(self.stats),
        }

    # ------------------------------------------------------------ his questions
    async def answer(self, ask: str = "now", town: str = "") -> dict:
        ask = (ask or "now").lower()
        if not _cfg("enabled", True):
            return {"spoken": "The scanner desk is switched off in my settings, sir."}
        if ask == "health":
            return {"spoken": await self._say_health()}
        if ask == "rules":
            return {"spoken": self._say_rules()}
        if ask == "doctor":
            return {"spoken": await self._say_doctor()}
        if ask == "last":
            return {"spoken": self._say_last()}
        if ask == "summary":
            return {"spoken": self._say_summary()}
        if ask == "town":
            return {"spoken": await self._say_town(town)}
        if ask == "more":
            inc = self.latest_told()
            if not inc:
                return {"spoken": "I haven't sent you a scanner call in the last few hours, sir."}
            _, spoken = await self.tell_more(int(inc["id"]))
            return {"spoken": spoken}
        return {"spoken": await self._say_now()}

    def _down_line(self) -> str:
        exe = str(_cfg("console_exe", "") or "")
        if not exe or not os.path.exists(exe):
            return "I can't reach the scanner console, sir, and it isn't installed where I expect it."
        if self.last_spawn > 0:
            return f"I can't reach the scanner console, sir. I last tried starting it {_ago(self.now() - self.last_spawn)}."
        return "I can't reach the scanner console, sir."

    async def _query_card(self, title: str, lines: list[str]) -> None:
        """A card for a question asked at the PC - never for one from his phone
        (remote messages stay remote)."""
        if self.remote_turn():
            return
        if lines:
            await bus.emit("brief", title="Scanner", eyebrow="THE SCANNER",
                           sections=[{"title": title, "lines": lines[:6]}])

    async def _say_now(self) -> str:
        now = self.now()
        hits = store.hits_since(now - 3600, until=now + 1)
        if self.up is False and not hits:
            return self._down_line()
        if not hits:
            return "Nothing on the scanner in the last hour, sir."
        busiest = Counter(rules.parse_label(h["name"]).spoken for h in hits).most_common(1)[0][0]
        incs = [i for i in store.incidents_since(now - 3600) if i.get("types") or i.get("burst")]
        incs.sort(key=lambda i: (-int(i.get("severity") or 0), float(i.get("miles") or 99)))
        n = len(hits)
        if incs:
            items = []
            for i in incs[:3]:
                s = self._item(i)
                if i.get("town") and i["town"] != home_town().name:
                    s += f", {miles_words(i.get('miles'))}"
                items.append(s)
            line = "In the last hour on the scanner: " + "; ".join(items) + \
                f". {n} transmission{'s' if n != 1 else ''} in all, busiest {busiest}."
        else:
            line = f"Quiet on the scanner, sir: {n} transmission{'s' if n != 1 else ''} in the last hour, mostly {busiest}."
        deaf = self.hearing_now(now)
        if deaf:
            # never "quiet" while deaf: the radio may be busy with calls I can't hear
            why = "no sound is reaching the PC" if deaf["silent"] else "the sound is too faint to make out"
            line = (f"{line.replace('Quiet on the scanner, sir: ', 'On the scanner, sir: ')} But I can't hear it "
                    f"properly: {why}, since {_hm(deaf['start'])}.")
        await self._query_card("The last hour", [_cap(self._item(i)) for i in incs[:5]]
                               or [f"{n} transmission{'s' if n != 1 else ''}, mostly {busiest}"])
        return line

    async def _say_town(self, name: str) -> str:
        t = geo.town(name or "")
        if t is None:
            return f"I don't know a town called {name}, sir." if name else "Which town, sir?"
        now = self.now()
        dist = "here at home" if t.name == home_town().name else miles_words(t.miles)
        incs = [i for i in store.incidents_since(now - 12 * 3600, t.name) if i.get("types") or i.get("burst")]
        hits = store.hits_since(now - 3600, t.name, until=now + 1)
        if incs:
            items = []
            for i in incs[:3]:
                at = _place(i.get("address") or "")
                s = (f"{i.get('label')}{at}" if i.get("types") else
                     f"a busy spell on {rules.parse_label(i.get('channel') or '').spoken}") + f", {_hm(i['first_ts'])}"
                if i.get("status") == "over":
                    s += ", now over"
                items.append(s)
            line = f"In {t.name}, {dist}: " + "; and ".join(items) + "."
            # the newest real call's facts (a busy spell has none to give)
            top = next((i for i in incs if i.get("types")), incs[0])
            more = self._spoken_details(top).strip()       # only what was heard, word for word
            if more:
                line += " " + (more if more.endswith(".") else more + ".")
            if hits:
                line += f" {len(hits)} call{'s' if len(hits) != 1 else ''} from there in the last hour."
            card = [_cap(x) for x in items]
            if top.get("units"):
                card.insert(1, "Units: " + ", ".join(top["units"][:6]))
            if top.get("details"):
                card.insert(1, "Known: " + "; ".join(top["details"][:4]))
            await self._query_card(t.name, card)
            return line
        if hits:
            words = [h for h in hits if h.get("transcript") and (h.get("asr_conf") or 0) >= rules.TRUST]
            line = f"Nothing notable in {t.name}, {dist}: {len(hits)} routine call{'s' if len(hits) != 1 else ''} in the last hour."
            if words:
                line += f' The last one said: "{words[-1]["transcript"]}"'
            return line
        if not store.count_since(now - 14 * 86400, t.name, until=now + 1)[0]:
            return f"I'm not hearing any {t.name} channels on the scanner, sir."
        return f"Quiet in {t.name}: nothing from there on the scanner in the last hour."

    def _say_last(self) -> str:
        last = store.last_hit()
        now = self.now()
        if not last or now - float(last.get("end_ts") or last["ts"]) > 900:
            if self.up is False:
                return self._down_line()
            return "I haven't heard anything on the scanner in the last few minutes, sir."
        lab = rules.parse_label(last.get("name") or "").spoken
        ago = _ago(now - float(last.get("end_ts") or last["ts"]))
        dur = int(round(float(last.get("duration") or 0)))
        secs = f"{dur} second{'s' if dur != 1 else ''}"
        text = (last.get("transcript") or "").strip()
        cls = str((last.get("quality") or {}).get("class") or "")
        if text and (last.get("asr_conf") or 0) >= rules.TRUST:
            line = f'That was {lab}, {ago}: "{text}"'
        elif last["id"] == self._in_flight or any(r["id"] == last["id"] for r in self._queue):
            line = f"That was {lab}, {ago}, {secs} long. I'm still working out the words."
        elif cls == "digital":
            line = f"That was {lab}, {ago}, about {secs}. It's a digital channel, so I can't make out the words."
        elif text:
            line = f'That was {lab}, {ago}. The audio was too rough to be sure; it sounded like "{text}"'
        else:
            line = f"That was {lab}, {ago}, about {secs}. I couldn't make out the words."
        if last.get("incident_id"):
            inc = store.incident(int(last["incident_id"]))
            if inc and inc.get("label"):
                line = line.rstrip(".") + (". " if not line.endswith('"') else " ") + \
                    f"It's part of {inc['label']}" + _place(inc.get("address") or "") + "."
        return line

    def _say_rules(self) -> str:
        """How scanner calls reach him, read from the settings decide() uses,
        so it stays true. Asked "when will you notify me about calls"
        (2026-11-08, 8:13 AM), the model promised "right away when a new call
        comes in" - no such rule exists, and he is relying on these."""
        home = home_town().name
        near = float(_cfg("near_miles", 5.0))
        parts = [
            f"Here's how scanner calls reach you, sir. A call I can name within {geo.radius_mi():.0f} miles - "
            "a fire, a crash, a medical or a police call - comes to your phone as a silent message with the "
            "recording, marked unconfirmed until a second transmission backs it up.",
            f"Only active danger makes a sound: a working fire, shots fired, a crash with people trapped and "
            f"the like, here in {home} or within {near:.0f} miles, once two transmissions agree.",
        ]
        night = _night_window()
        if night:
            (sh, sm), (eh, em) = night
            day0 = dt.datetime(2026, 1, 1)
            a = _hm(day0.replace(hour=sh, minute=sm).timestamp())
            b = _hm(day0.replace(hour=eh, minute=em).timestamp())
            if str(_cfg("night", "silent")) == "hold":
                parts.append(f"From {a} to {b} I hold everything but danger in {home} for a summary at {b}.")
            else:
                parts.append(f"From {a} to {b} it's all silent except danger in {home}, and the night "
                             f"comes to you as a summary at {b}.")
        parts.append("If I can't make out what a call is, I can't send it - unless the dispatcher toned a station "
                     "out first: a paged dispatch comes through silently even when I can't name it. A page never "
                     "makes a sound by itself. And I'll tell you if I stop being able to hear the scanner.")
        return " ".join(parts)

    def _say_summary(self) -> str:
        now = self.now()
        line = self.brief_line(now - 12 * 3600)
        return line or "Nothing on the scanner in the last twelve hours, sir."

    async def _say_health(self) -> str:
        client = self._ensure_client()
        h = await client.health()
        if h is None:
            self._mark_down(self.now())
            return self._down_line()
        self._mark_up(h, self.now())
        parts = ["The scanner console is up"]
        if h.get("connected"):
            parts.append("and the scanner is connected" + (" and scanning." if h.get("scanning") else ", but it's holding rather than scanning."))
        else:
            parts.append("but the scanner itself isn't connected.")
        line = " ".join(parts)
        a = h.get("audio") if isinstance(h.get("audio"), dict) else {}
        peak = a.get("recent_peak_dbfs")
        # "The audio level is fine" (2026-11-08, 8:13 AM) - said with no sound
        # reaching the PC at all: the console had nothing to judge, and nothing
        # judged was taken for fine. Hearing is now what the last hour gave me.
        now = self.now()
        deaf = self.hearing_now(now)
        recent = store.hits_since(now - 3600)
        understood = [x for x in recent if (x.get("transcript") or "").strip()]
        if not a.get("enabled"):
            line += " Desktop audio is off, so I'm working from the log alone."
        elif deaf and deaf["silent"]:
            line += (f" But I can't hear it: no sound has reached the PC since {_hm(deaf['start'])}, "
                     f"{deaf['n']} transmissions with nothing. Check the audio cable and the scanner's volume - "
                     f"{GOOD_VOLUME} is what works.")
        elif deaf:
            line += (f" But I can't make it out: the sound has been too faint since {_hm(deaf['start'])}, "
                     f"{deaf['n']} transmissions without a word I understood. Turn the scanner's volume up "
                     f"to {GOOD_VOLUME}.")
        elif understood:
            line += (f" I'm hearing it: I made out words in {len(understood)} of the last hour's "
                     f"{len(recent)} transmissions.")
            if a.get("weak_input"):
                line += " The audio is weak" + (f", peaking around minus {abs(int(round(float(peak))))} decibels"
                                                 if isinstance(peak, (int, float)) else "") + ", so some will be patchy."
        elif recent:
            line += (f" I haven't made out a word in the last hour's {len(recent)} transmissions yet, "
                     "so I can't promise I'm hearing it.")
        else:
            line += " Nothing has been transmitted in the last hour, so I can't judge the sound yet."
        last = store.last_hit()
        if last:
            line += f" The last transmission was {_ago(now - float(last.get('end_ts') or last['ts']))}, on {rules.parse_label(last.get('name') or '').spoken}."
        else:
            line += " I haven't logged a transmission yet."
        if self.home_gap:
            line += " " + self.home_gap.replace(", sir,", ",").replace(", sir.", ".")
        return line

    async def _say_doctor(self) -> str:
        rep = await self._ensure_client().doctor_report()
        if rep == "missing":
            return "The scan doctor hasn't written a report yet, sir."
        if rep is None:
            return self._down_line() if self.up is False else "I couldn't get the scan doctor's report just now, sir."
        return describe_doctor(rep)


desk = ScannerDesk()


def brief_line(since_ts: float) -> str:
    """For the briefing: one line on what the scanner heard since `since_ts`."""
    try:
        return desk.brief_line(since_ts)
    except Exception:
        log.exception("scanner: brief line failed")
        return ""


def status() -> dict:
    """For the briefing and diagnostics: the console, the scanner, the audio, the day."""
    try:
        return desk.status()
    except Exception:
        log.exception("scanner: status failed")
        return {"enabled": bool(_cfg("enabled", True)), "console_up": False, "error": "status failed"}


async def _scanner_tool(ask: str = "now", town: str = "") -> dict:
    try:
        return await desk.answer(ask, town)
    except Exception:
        log.exception("scanner: answer failed")
        return {"error": "I couldn't read the scanner just now, sir."}


def register_all() -> None:
    """The scanner reflexes' tool. Underscore-named: never shown to the model."""
    from tools.registry import Risk, Tool, registry
    registry.register(Tool(
        name="_scanner",
        description="(internal) the scanner desk's spoken answers for the scanner reflexes.",
        parameters={"type": "object", "properties": {
            "ask": {"type": "string", "enum": ["now", "town", "last", "summary", "health", "doctor", "rules",
                                               "more"]},
            "town": {"type": "string"}}, "required": ["ask"]},
        # "more" asks the model for the whole account and may try twice
        risk=Risk.SAFE, handler=_scanner_tool, timeout=45))
