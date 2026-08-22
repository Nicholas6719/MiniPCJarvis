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
_LEFTOVER = re.compile(r"[*_`#\|~^<>{}\[\]]")
_WS = re.compile(r"[ \t]+")


def _year_words(m: re.Match) -> str:
    try:
        from num2words import num2words
        return num2words(int(m.group(1)), to="year")
    except Exception:
        return m.group(0)


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
    t = _YEAR.sub(_year_words, t)                 # 1946 -> nineteen forty-six
    t = _LEFTOVER.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t
