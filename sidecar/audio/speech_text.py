"""Turn model text into something a human would actually say.

The UI shows the raw text; only the spoken form is cleaned.
"""
from __future__ import annotations

import re

_MD_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_ITALIC = re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")
_MD_CODE = re.compile(r"`{1,3}([^`]*)`{1,3}")
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADER = re.compile(r"^\s{0,3}#{1,6}\s*", re.M)
_MD_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_BS = chr(92)
_WIN_PATH = re.compile("[A-Za-z]:" + _BS + _BS + "[^" + _BS + _BS + " ,;:'\"]+(?:" + _BS + _BS + "[^" + _BS + _BS + " ,;:'\"]+)*")
_UNIX_PATH = re.compile(r"(?<![\w/])/(?:[\w.-]+/)+[\w.-]*")
_URL = re.compile(r"https?://([^/\s]+)[^\s]*")
_YEAR = re.compile(r"\b(1[1-9]\d{2}|20\d{2})\b")
_SLASH = re.compile(r"(?<=\w)/(?=\w)")
# Kokoro simply does not voice "." between digits: "1.7 terabytes" comes out as
# "one seven terabytes". Verified by synthesizing and transcribing it back
# (tests/speech_symbols.py). JARVIS reports free disk space on every status
# question, so he was mis-stating it out loud constantly.
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
# "2:04 PM" is voiced "two hundred four PM" — measured against known spellings, the
# clock reading is 2.05 s and "two hundred four" is 2.03 s while "two oh four" is 1.88 s.
# Every time from :01 to :09 was wrong, and "what time is it" is the most common thing
# he is ever asked. Minutes of 10 or more already read correctly, so they are left alone.
_CLOCK = re.compile(r"\b(\d{1,2}):([0-5]\d)(\s*[ap]\.?m\.?)?", re.I)


def _clock_words(m: re.Match) -> str:
    hour, minute, meridiem = m.group(1), m.group(2), m.group(3) or ""
    if minute == "00":
        # "six o'clock PM" is not something anyone says; with AM/PM the hour stands alone
        return f"{hour}{meridiem}" if meridiem else f"{hour} o'clock"
    if minute[0] == "0":
        return f"{hour} oh {minute[1]}{meridiem}"
    return m.group(0)
# "$40" is voiced "dollar forty" — right words, wrong order.
_MONEY = re.compile(r"([$£€])\s?(\d[\d,]*(?:\.\d+)?)")
_CURRENCY = {"$": ("dollar", "dollars"), "£": ("pound", "pounds"), "€": ("euro", "euros")}
_DEGREE_UNIT = re.compile(r"°\s?([CF])\b")
_DEGREE = re.compile(r"°")


def _money_words(m: re.Match) -> str:
    one, many = _CURRENCY[m.group(1)]
    amount = m.group(2)
    return f"{amount} {one if amount in ('1', '1.00') else many}"

_LEFTOVER = re.compile(r"[*_`#\|~^<>{}\[\]]")
_WS = re.compile(r"[ \t]+")
_SPACE_PUNCT = re.compile(r"\s+([,.;:!?])")   # "45 degrees ." after symbol expansion


def _year_words(m: re.Match) -> str:
    try:
        from num2words import num2words
        return num2words(int(m.group(1)), to="year")
    except Exception:
        return m.group(0)


def strip_markdown(text: str) -> str:
    """Markdown removed, everything else untouched — for the transcript on screen.

    The model is told never to use markdown and mostly obeys, but it still slips out
    ("Steven Spielberg directed *Jaws*"). Speech never suffered, because clean_for_speech
    strips it; the HUD showed the raw asterisks. This deliberately does NOT apply the
    spoken transforms — nobody wants to read "1 point 7 terabytes".
    """
    t = _MD_LINK.sub(r"\1", text or "")
    t = _MD_CODE.sub(r"\1", t)
    t = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_HEADER.sub("", t)
    return _WS.sub(" ", t).strip()


def clean_for_speech(text: str) -> str:
    t = text
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_CODE.sub(r"\1", t)
    t = _MD_BOLD.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_ITALIC.sub(lambda m: m.group(1) or m.group(2), t)
    t = _MD_HEADER.sub("", t)
    t = _MD_BULLET.sub("", t)
    t = _URL.sub(r"\1", t)                      # "example.com" instead of the URL
    t = _WIN_PATH.sub(lambda m: "the saved location" + ("." if m.group(0).endswith(".") else ""), t)
    t = _UNIX_PATH.sub("the saved location", t)
    t = _SLASH.sub(" ", t)                        # and/or -> and or, km/h -> km h
    t = _MONEY.sub(_money_words, t)               # $40 -> 40 dollars
    t = _DEGREE_UNIT.sub(lambda m: " degrees " + ("Celsius" if m.group(1) == "C" else "Fahrenheit"), t)
    t = _DEGREE.sub(" degrees ", t)
    t = _CLOCK.sub(_clock_words, t)               # 2:04 -> 2 oh 4 (before the decimal rule)
    t = _DECIMAL.sub(" point ", t)                # 1.7 -> 1 point 7 (must run AFTER money)
    t = _YEAR.sub(_year_words, t)                 # 1946 -> nineteen forty-six
    t = _LEFTOVER.sub(" ", t)
    t = _WS.sub(" ", t)
    t = _SPACE_PUNCT.sub(r"\1", t).strip()
    return t
