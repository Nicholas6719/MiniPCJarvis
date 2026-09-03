"""Is there internet, right now? One cheap answer everything can share.

Nicholas tried JARVIS with no connection and reported "it really did not work".
He was on a half-installed build at the time, so that is not the whole story —
but looking into it found a real gap underneath: JARVIS had no idea whether it
was online.

`State.OFFLINE` exists in the state machine and does NOT mean this. It is the
state before boot, and its only transition is OFFLINE -> STARTING.

So with no connection, every networked tool failed on its own and the language
model improvised a different explanation for each one. Nothing anywhere could
say the true and useful sentence: "I can't reach the internet, sir." A wrong
explanation per tool is worse than one honest one, because he cannot tell a
broken assistant from an unplugged router.

HOW IT CHECKS. A TCP connect to a public DNS resolver on port 53 — no DNS
lookup of its own (which is the thing most likely to hang when a network is
half up), no HTTP, no third party told anything about him. Two resolvers, so
one being unreachable is not mistaken for the internet being down.

CACHED, because this is asked on the failure path of every web tool and the
answer does not change between one sentence and the next. A positive is held
longer than a negative: being told he is offline when he is not is the more
annoying mistake, so "offline" is re-checked sooner.

NEVER RAISES, NEVER BLOCKS LONG. 1.2 seconds at the very worst, and any
exception at all means "assume online" — refusing to try because a probe failed
would turn a working connection into a broken assistant.
"""
from __future__ import annotations

import logging
import socket
import time

log = logging.getLogger("jarvis.net")

# Port 53 rather than 443: a TCP handshake to a resolver needs no DNS of its
# own, and DNS is what hangs when a network is up but not working.
_PROBES = (("1.1.1.1", 53), ("8.8.8.8", 53))
_TIMEOUT_S = 0.6

# Being wrong about "offline" costs more than being slow to notice it, so a
# good answer is trusted for longer than a bad one.
_TTL_ONLINE_S = 25.0
_TTL_OFFLINE_S = 6.0

_last: tuple[float, bool] | None = None


def online(force: bool = False) -> bool:
    """True if the internet is reachable. Cached; never raises."""
    global _last
    now = time.time()
    if not force and _last is not None:
        when, was = _last
        if now - when < (_TTL_ONLINE_S if was else _TTL_OFFLINE_S):
            return was

    up = False
    for host, port in _PROBES:
        try:
            with socket.create_connection((host, port), _TIMEOUT_S):
                up = True
                break
        except Exception:
            continue

    if _last is None or _last[1] != up:
        log.info("network is %s", "up" if up else "DOWN")
    _last = (now, up)
    return up


def note() -> str:
    """The sentence to add when something failed and the network is why.

    Empty when there IS a connection, so a caller can append it unconditionally
    and it only appears when it is true.
    """
    return "" if online() else "I can't reach the internet at the moment, sir"
