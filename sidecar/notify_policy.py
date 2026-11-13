"""What is allowed to make his phone SOUND.

2026-11-07, his words: *"I am mainly ignoring his notifications, which is not
what I built him for."* Ten days of his phone (10-27..11-06) - every one of the
ninety-odd messages arrived with a sound: four briefs a day, price moves, memory
warnings about his other apps, and news that was neither urgent nor local. A
phone that buzzes eight times a day for nothing teaches a man to stop looking,
and then the one buzz that mattered is lost with the rest.

So by default everything arrives SILENTLY (Telegram's `disable_notification`):
it is there when he looks. Only two things make a sound:

  * an URGENT - since this date, only a local, current, dangerous news story
    the local model has confirmed, or a deadly-weather WARNING for his point
    (price moves are capped below urgent: his watchlist, kept, but quiet);
  * his OWN reminders - the ones he set, at the time he set them - and the
    event watches he asked for ("tell me when the build finishes"), whose whole
    point is the moment.

And a hard daily budget on the first kind (reminders excluded), for the bug not
yet written: past it, the rest arrive silently and the log says so once.

One table, here, so the policy can be read in one place and changed in one line.
"""
from __future__ import annotations

from config import config

URGENT = "urgent"

# kinds that never sound, whatever their tier. "scanner_fyi" is the scanner's
# quiet feed: a crash or a reported fire in his radius, there when he looks.
QUIET_KINDS = frozenset({"brief", "market", "memory", "held", "render", "scanner_fyi"})
# ...and that never cut into what he is doing at the PC either
NO_INTERRUPT = frozenset({"brief", "market", "memory", "scanner_fyi"})
# kinds that sound at any tier: he asked for them, at a time he chose. The
# nudge is his reminder again, half an hour on with no Done: it sounds like
# the reminder (his words, 2026-11-07: "I don't want that, why would it be
# silent?" - the silent follow-up came in that morning's overhaul)
# "scanner_local" is the scanner in EMERGENCY MODE (his words, 2026-11-13,
# outages and grounded planes: "if anything is happening locally I don't want
# those notifications to be silent and I need to know imminently"): a call in
# his town or within near_miles, or danger anywhere in his radius, sounds on
# its first transmission, day and night, and is never rationed by the daily
# sound budget. It exists only while scanner.emergency_mode is on.
HIS_OWN = frozenset({"reminder", "nudge", "watch", "scanner_local"})
# kinds counted against their OWN hourly ceiling, never the shared one: six
# scanner calls in an hour must not spend the room his reminders need
# (delivery._over_budget; review of 2026-11-07)
OWN_CEILING = frozenset({"scanner_fyi", "scanner_local"})
DEFAULT_QUIET_PER_HOUR = 12
# kinds never SPOKEN inside his quiet hours, even with him at the PC: the
# scanner's night calls go to his phone silently (his answer, 2026-11-07:
# "send silently plus in morning summary")
NIGHT_SILENT = frozenset({"scanner_fyi"})
DEFAULT_DAILY_SOUNDS = 5


def kind_of(subject: str = "", key: str = "") -> str:
    """What sort of message this is, from what the caller called it."""
    s = (subject or "").strip().lower()
    k = (key or "").strip().lower()
    if k.startswith("scanner-local:") or s.startswith("scanner-local"):
        return "scanner_local"
    if s == "reminder" or k.startswith("task:"):
        return "reminder"
    if s == "nudge" or k.startswith("nudge:"):
        return "nudge"
    if s == "watch" or k.startswith("watch:"):
        return "watch"
    if s == "news" or k.startswith("news:"):
        return "news"
    if s == "weather" or k.startswith("nws:"):
        return "weather"
    if s == "market" or k.startswith(("move:", "index:")):
        return "market"
    if s == "brief" or k.startswith("brief:"):
        return "brief"
    if s == "memory" or k.startswith("memory"):
        return "memory"
    if s == "held" or k.startswith("held:"):
        return "held"
    if k.startswith("render-done:"):
        return "render"
    if s.startswith("scanner-fyi") or k.startswith("scanner-fyi:"):
        return "scanner_fyi"
    if s.startswith("scanner:") or k.startswith("scanner:"):
        return "scanner"
    return s or "other"


def audible(tier: str, kind: str) -> bool:
    """Would this make a sound, before the daily budget is consulted?"""
    if kind in HIS_OWN:
        return True
    if kind in QUIET_KINDS:
        return False
    return tier == URGENT


def counts_against_budget(kind: str) -> bool:
    """His own reminders, and their one follow-up, are never rationed;
    everything else that sounds is."""
    return kind not in ("reminder", "nudge", "scanner_local")


def daily_sound_cap() -> int:
    try:
        return max(0, int(config.get("proactive", "max_sounding_per_day",
                                     default=DEFAULT_DAILY_SOUNDS)))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_SOUNDS


def interrupts(tier: str, kind: str) -> bool:
    """At the PC: may it cut into what he is doing? URGENT and what he asked
    for; a price move or a memory note waits for a quiet moment."""
    if kind in HIS_OWN or tier == URGENT:
        return True
    return tier == "alert" and kind not in NO_INTERRUPT
