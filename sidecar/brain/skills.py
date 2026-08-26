"""JARVIS reflex skills: things JARVIS can do by itself, without the LLM.

Each skill has seed phrasings (the brain learns more from real use), a slot
extractor that pulls arguments out of the utterance, and a speak() template.
If the extractor can't find what it needs, the request falls through to the LLM.
"""
from __future__ import annotations

import datetime as dt
import random
import re
from dataclasses import dataclass, field
from typing import Callable

# ---------- persona ----------

_last_honorific = [False]     # never two lines running


_REGRET = re.compile(r"^I (?:couldn't|could not|can't|cannot)\b")


def polish(line: str, kind: str = "ack") -> str:
    """The single place every spoken reflex line gets its voice.

    Bad news in the films is almost never blunt — it is softened first ("I'm afraid
    the suit isn't ready, sir"), so a flat "I couldn't open Spotify" gets the same
    treatment about half the time, and leans harder on the honorific when it does.
    """
    from config import config
    line = (line or "").strip()
    if _REGRET.search(line):
        if random.random() < 0.5:
            line = "I'm afraid " + line   # the pattern always starts with the pronoun
        # bad news leans on the honorific harder than a routine acknowledgement does,
        # so this tracks the configured rate rather than fixing its own number
        base = float(config.get("persona", "honorific_rate", default=0.55))
        return honorific(line, kind, rate=min(0.95, base * 1.5))
    return honorific(line, kind)


def want_honorific(rate: float | None = None) -> bool:
    """Decide whether THIS reply should carry the honorific.

    The frequency decision lives here, not in the language model — asked to manage its
    own rate the model either ignored it (11%) or ran away with it (60%, seven replies
    in a row). Deciding here and telling it plainly for the turn gives the reflex path
    and the LLM path one shared rhythm, including "never two lines running".
    """
    from config import config
    if rate is None:
        rate = float(config.get("persona", "honorific_rate", default=0.55))
    if _last_honorific[0] or random.random() >= rate:
        _last_honorific[0] = False
        return False
    _last_honorific[0] = True
    return True


def without_honorific(line: str) -> str:
    """Strip the honorific out of a reply before it goes back into the model's context.

    Left in, it snowballs: the model sees three replies ending in "sir", concludes that
    is the register, and then ends every single reply that way (measured 60% and climbing,
    with seven back-to-back runs). It cannot hold "don't use it twice running" on its own.
    Scrubbing its own history breaks the feedback loop at the source, so each turn is
    decided fresh from the prompt — and nothing the user sees or hears is changed.
    """
    line = re.sub(r",\s*sir\b", "", line or "", flags=re.I)
    line = re.sub(r"^sir,\s*", "", line, flags=re.I).strip()
    return line[:1].upper() + line[1:] if line else line


def honorific(line: str, kind: str = "ack", rate: float | None = None) -> str:
    """Address him the way JARVIS actually does.

    Measured over 97 JARVIS lines from the films: 37% carry "sir", almost always either
    opening something he raises himself ("Sir, the city is taking fire.") or closing an
    acknowledgement ("Very good, sir."). Reflex replies never went through the language
    model, which is why he had effectively stopped saying it at all.
    """
    from config import config
    word = str(config.get("persona", "honorific", default="sir")).strip()
    if rate is None:
        rate = float(config.get("persona", "honorific_rate", default=0.55))
    line = (line or "").strip()
    if not word or rate <= 0 or not line:
        return line
    if re.search(rf"\b{re.escape(word)}\b", line, re.I):
        _last_honorific[0] = True         # already addressed him; don't do it again next line
        return line
    if len(line) > 120 or line.count(".") > 2:
        _last_honorific[0] = False        # long report: an honorific reads as clutter
        return line
    if _last_honorific[0] or random.random() >= rate:
        _last_honorific[0] = False
        return line
    _last_honorific[0] = True
    if kind == "alert":
        return f"{word.capitalize()}, {line[0].lower()}{line[1:]}"
    if line.endswith(("?", "!")):
        return f"{line[:-1]}, {word}{line[-1]}"
    if line.endswith("."):
        return f"{line[:-1]}, {word}."
    return f"{line}, {word}."


# ---------- slot extractors ----------

_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
              "eighty": 80, "ninety": 90, "hundred": 100, "half": 50, "max": 100, "full": 100}


_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_WORD_ALIAS = {"half": 50, "max": 100, "maximum": 100, "full": 100, "hundred": 100}


def _number(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    toks = re.findall(r"[a-z]+", text.lower())
    # combine an adjacent tens + ones ("twenty five" -> 25); else take the first number word
    for i, w in enumerate(toks):
        if w in _TENS:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            return _TENS[w] + (_ONES[nxt] if nxt in _ONES and _ONES[nxt] < 10 else 0)
        if w in _ONES:
            return _ONES[w]
        if w in _WORD_ALIAS:
            return _WORD_ALIAS[w]
    return None


def slots_volume(t: str) -> dict | None:
    n = _number(t)
    return {"percent": max(0, min(100, n))} if n is not None else None


_NOT_AN_APP = {"it", "that", "this", "the pc", "the computer", "computer", "pc", "everything",
               "all windows", "all the windows", "all", "the window", "windows", "down the pc",
               "down the computer", "up", "down", "the tab", "this tab", "the browser",
               # "run along" is a dismissal, not a request to launch an app called "along";
               # the launch verbs are common English words and catch these by accident.
               "along", "over", "away", "off", "out", "back", "ahead", "again", "late",
               "yourself", "himself", "jarvis", "there", "here", "now", "quiet", "dark"}


def slots_app(t: str) -> dict | None:
    m = re.search(r"\b(?:open|launch|start|run|fire up|bring up|put on|close|quit|exit|kill)\s+(?:up\s+)?(?:the\s+|my\s+)?([a-z0-9 .+#-]{2,40}?)(?:\s+(?:for me|please|now|app|application))*[.!?]*$", t)
    if not m:
        return None
    name = m.group(1).strip()
    if not name or name in _NOT_AN_APP:
        return None
    # a bare generic word or a phrase that reads like a sentence, not an app name
    if name.startswith(("down ", "off ", "the ", "all ")) or len(name.split()) > 3:
        return None
    if re.search(r"https?://|\b\w+\.(?:com|org|net|io|gov|edu|co|tv|ai)\b", name):
        return None  # a website, not an app -> the LLM routes it to open_url
    if re.search(r"\.(?:xlsx?|docx?|pptx?|pdf|txt|md|csv|png|jpe?g|gif|mp[34]|zip|json|log)$", name):
        return None  # a document -> the LLM finds/opens the file instead of launching an app
    return {"name": name}


def slots_site(t: str) -> dict | None:
    m = re.search(r"(https?://\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b(?:/\S*)?)", t)
    return {"url": m.group(1)} if m else None


def say_site(slots: dict, res: dict) -> str:
    url = str(slots.get("url", "")).replace("https://", "").replace("http://", "").rstrip("/")
    return f"Opening {url} in your browser." if "error" not in res else f"I couldn't open {url}."


_SITE_WANTS_ANSWER = re.compile(r"\b(and|then|tell|what|read|summar\w*|say|says|find|look|check|show me)\b")


def site_direct(text: str) -> bool:
    """'open youtube.com' -> just open it; 'open x and tell me ...' -> let the LLM read it."""
    return not _SITE_WANTS_ANSWER.search(text.lower())


_TEACH_HEAD = re.compile(
    r"^(?:from now on[, ]*|ok[, ]*|okay[, ]*)?(?:when(?:ever)? i say|if i say|teach you(?: that)?(?: when i say)?)\s+",
    re.I)
_TEACH_CONNECTOR = re.compile(r"\s*(?:,|\bthen\b|\byou should\b|\byou\b|\bi want you to\b|\bthat means\b|\bit means\b|\bdo\b)\s*", re.I)


_ACTION_VERBS = {"mute", "unmute", "open", "close", "launch", "start", "quit", "set", "take", "lock",
                 "play", "pause", "skip", "search", "show", "remind", "turn", "go", "put", "lower",
                 "raise", "grab", "screenshot", "remember", "silence", "crank", "fire", "bring", "load"}


def slots_teach(t: str) -> dict | None:
    m = _TEACH_HEAD.search(t)
    if not m:
        return None
    rest = t[m.end():].strip(" ,.!?\"'")
    # "lights out, mute and open spotify" | "good night then lock the computer"
    parts = _TEACH_CONNECTOR.split(rest, maxsplit=1)
    if len(parts) != 2:
        # no comma/connector survived speech-to-text: split where the action verb starts
        words = rest.split()
        idx = next((i for i in range(1, len(words)) if words[i] in _ACTION_VERBS), None)
        if idx is None:
            return None
        parts = [" ".join(words[:idx]), " ".join(words[idx:])]
    phrase, action = parts[0].strip(" ,\"'"), parts[1].strip(" ,\"'")
    if len(phrase) < 2 or len(action) < 3:
        return None
    return {"phrase": phrase, "action": action}


_CORRECTION = re.compile(
    r"^(?:nope|no no no|not that one|not that|that's not it|that's not what i meant|that is wrong|that's wrong|wrong|no)\b(?!\s+(?:thanks?|thank you))"
    r"[,.!\s]*(?:i meant|i said|i wanted|i want|actually|do|it's|its)?[,\s]*(?P<rest>.*)$", re.I)


def slots_correction(t: str) -> dict | None:
    m = _CORRECTION.search(t)
    if not m:
        return None
    rest = m.group("rest").strip(" ,.")
    if re.fullmatch(r"(?:that's wrong|that is wrong|wrong|that|it|no|nope)?", rest):
        rest = ""
    return {"rest": rest}


_FOLDER_WORDS = {"desktop": "desktop", "documents": "documents", "document": "documents", "docs": "documents",
                 "downloads": "downloads", "download": "downloads", "pictures": "pictures", "photos": "pictures",
                 "picture": "pictures", "images": "pictures"}


def slots_folder(t: str) -> dict | None:
    for w, root in _FOLDER_WORDS.items():
        if re.search(rf"\b{w}\b", t):
            return {"path": root}
    return None


def say_folder(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't open that folder."
    n = res.get("count", 0)
    return f"Here's your {slots['path']}: {n} item{'s' if n != 1 else ''}."


_FIND_A = re.compile(r"(?:file|folder|document)s?\s+(?:called|named|with|containing)\s+(.+?)"
                     r"(?:\s+(?:in|on|under)\s+(?:my\s+)?(desktop|documents|downloads|pictures))?[.!?]*$")
_FIND_B = re.compile(r"\b(?:find|search for|look for|locate|where is|where's)\s+(?:my\s+|the\s+|a\s+)?(.+?)"
                     r"(?:\s+(?:in|on|under)\s+(?:my\s+)?(desktop|documents|downloads|pictures))?[.!?]*$")


_FIND_C = re.compile(r"\bsearch\s+(?:my\s+)?(desktop|documents|downloads|pictures)\s+for\s+(.+?)[.!?]*$")


def slots_find(t: str) -> dict | None:
    mc = _FIND_C.search(t)
    if mc:
        return {"query": mc.group(2).strip(" '\""), "folder": mc.group(1)}
    m = _FIND_A.search(t) or _FIND_B.search(t)
    if not m:
        return None
    q = re.sub(r"^(?:file|folder|document)s?\s+", "", m.group(1).strip(" '\""))
    q = re.sub(r"\s+(?:file|folder|document)s?$", "", q)
    q = re.sub(r"\s+(?:in the name|from earlier|on my computer|on this pc)$", "", q)
    if not q or len(q) < 2:
        return None
    out = {"query": q}
    if m.group(2):
        out["folder"] = m.group(2)
    return out


_FOLDER_ONLY = re.compile(r"^(?:my\s+|the\s+)?(?:desktop|documents?|downloads?|pictures?|files?|folders?)$")


def say_find(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't search for that."
    n = res.get("count", 0)
    if n == 0:
        return f"I couldn't find anything called {slots['query']}."
    first = res.get("results", [{}])[0]
    if n == 1:
        return f"Found it: {first.get('name')} in your {first.get('where')}."
    return f"I found {n} matches; the closest is {first.get('name')} in your {first.get('where')}."


_SWITCH = re.compile(r"\b(?:switch (?:over )?to|focus on|focus|go back to|jump to|bring me to|show me the)\s+(?:the\s+|my\s+)?(.+?)(?:\s+window|\s+app)?[.!?]*$")


def slots_switch(t: str) -> dict | None:
    m = _SWITCH.search(t)
    if not m:
        return None
    name = m.group(1).strip()
    return {"title": name} if 1 < len(name) <= 40 else None


def say_switch(slots: dict, res: dict) -> str:
    if "error" in res:
        return f"I don't see a {slots['title']} window open."
    return f"Switching to {res.get('focused') or slots['title']}."


_WATCH_METRIC = [("cpu", r"\bcpu\b|\bprocessor\b"), ("ram", r"\bram\b|\bmemory\b"),
                 ("disk_free_gb", r"\bdisk\b|\bstorage\b|\bdrive\b|\bspace\b"), ("battery", r"\bbattery\b")]
_WATCH_HEAD = re.compile(r"\b(?:tell me|let me know|warn me|alert me|notify me|ping me|say something)\b")


def slots_watch(t: str) -> dict | None:
    if not _WATCH_HEAD.search(t) and not re.search(r"\bwatch\b|\bkeep an eye on\b", t):
        return None
    metric = next((m for m, pat in _WATCH_METRIC if re.search(pat, t)), None)
    if not metric:
        return None
    m = re.search(r"\b(above|over|exceeds|more than|higher than|hits|reaches|below|under|drops below|less than|lower than|falls under)\b\s*(\d{1,4})", t)
    if not m:
        return None
    op = "<" if m.group(1) in ("below", "under", "drops below", "less than", "lower than", "falls under") else ">"
    out = {"metric": metric, "op": op, "value": int(m.group(2))}
    h = re.search(r"\bfor\s+(\d{1,3})\s*(?:minutes?|mins?)\b", t)
    if h:
        out["for_min"] = int(h.group(1))
    return out


def say_watch(slots: dict, res: dict) -> str:
    return "Okay. " + str(res.get("watching", "I'll keep an eye on it.")) if "error" not in res else "I couldn't set that up."


def slots_unwatch(t: str) -> dict | None:
    if not re.search(r"\b(?:stop|quit|cancel|forget about)\b.*\b(?:watching|monitoring|alert|alerts|warning|telling me|rule|rules)\b", t) and \
            not re.search(r"\bstop (?:watching|monitoring)\b", t):
        return None
    metric = next((m for m, pat in _WATCH_METRIC if re.search(pat, t)), None)
    return {"metric": metric}


def say_unwatch(slots: dict, res: dict) -> str:
    n = res.get("removed", 0)
    return "Done, I've stopped watching that." if n else "I wasn't watching anything like that."


_WX_PLACE = re.compile(r"\b(?:in|for|at|around)\s+(?!the\b)([a-z][a-z .'-]{2,40}?)(?:\s+(?:right now|today|tomorrow|tonight|this (?:morning|afternoon|evening)|now))?[.!?]*$")


def slots_weather(t: str) -> dict | None:
    if not re.search(r"\b(?:weather|forecast|temperature|rain|raining|snow|snowing|hot|cold|humid|umbrella|degrees)\b", t):
        return None
    if re.search(r"\bon\s+(?:mars|the moon|venus|jupiter|saturn|pluto|mercury|neptune|uranus|the sun)\b", t):
        return None   # astronomy trivia, not a forecast
    out: dict = {"when": "tomorrow" if re.search(r"\btomorrow\b", t) else "now"}
    t = re.sub(r"\b(?:tomorrow|today|tonight|right now|now|this (?:morning|afternoon|evening|weekend))\b", " ", t)
    t = re.sub(r"\b(?:for|in|at|around)\s+(?=(?:for|in|at|around)\b)", "", t)   # "for tomorrow in X" -> "in X"
    t = re.sub(r"\s+", " ", t).strip()
    m = _WX_PLACE.search(t)
    if m:
        place = m.group(1).strip()
        if place not in ("my area", "my town", "my city", "here", "home"):
            out["location"] = place
    return out


def say_weather(slots: dict, res: dict) -> str:
    if "error" in res:
        return res["error"] + "."
    loc = res.get("location", "your area").split(",")[0]
    now = res.get("now", {})
    if slots.get("when") == "tomorrow" and "tomorrow" in res:
        d = res["tomorrow"]
        rain = f", with a {d['rain_chance']} percent chance of rain" if d.get("rain_chance") not in (None, 0) else ""
        return f"Tomorrow in {loc}: {d['conditions']}, high of {d['high']} and low of {d['low']}{rain}."
    d = res.get("today", {})
    feels = f", feels like {now['feels_like']}" if abs(now.get("feels_like", now.get("temp", 0)) - now.get("temp", 0)) >= 3 else ""
    rain = f" There's a {d['rain_chance']} percent chance of rain today." if d.get("rain_chance", 0) and d["rain_chance"] >= 30 else ""
    return f"It's {now.get('temp')} degrees and {now.get('conditions')} in {loc}{feels}. Today's high is {d.get('high')}, low {d.get('low')}.{rain}"


_UI_VIEWS = {"files": "files", "file": "files", "apps": "apps", "app": "apps", "windows": "apps", "system": "system",
             "browser": "browser", "web": "browser", "memory": "memory", "memories": "memory", "tasks": "tasks",
             "reminders": "tasks", "diagnostics": "diagnostics", "settings": "settings", "conversation": "conversation",
             "chat": "conversation", "media": "media", "pictures": "media", "research": "research",
             "history": "history", "about": "about", "voice": "settings", "options": "settings"}

_ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4, "sixth": 5,
             "seventh": 6, "eighth": 7, "1st": 0, "2nd": 1, "3rd": 2, "4th": 3,
             "one": 0, "two": 1, "three": 2, "four": 3}

_KEEP_FOR = re.compile(r"\b(?:keep|pin|hold)\b.*?\bfor\s+(?:the next\s+)?(an?\s+|\d+\s*|one\s+|two\s+|five\s+|ten\s+|fifteen\s+|twenty\s+|thirty\s+)?(hour|hours|minutes?|min)\b")
_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "five": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30}


def slots_ui(t: str) -> dict | None:
    if re.search(r"\b(?:hide|close|clear|dismiss)\b.*\b(?:everything|all|the panels?|the tabs?|the stage|that|this|it)\b|^(?:hide|dismiss)\b", t):
        return {"action": "hide"}
    # "bring that back" / "bring back the pictures" — restore the last stage (§6.3)
    if re.search(r"\bbring\b.*\bback\b|\brestore\b.*\b(?:that|it|the stage|the panel)\b|\bput (?:that|it) back\b", t):
        return {"action": "restore"}
    # "keep it for ten minutes" — a timed pin
    mk = _KEEP_FOR.search(t)
    if mk:
        amount, unit = (mk.group(1) or "").strip(), mk.group(2)
        n = int(amount) if amount.isdigit() else _NUM_WORDS.get(amount, 10)
        return {"action": "pin", "minutes": n * 60 if unit.startswith("hour") else n}
    if re.search(r"\b(?:pin|keep)\b.*\b(?:that|this|it|the panel|the tab|open|up)\b", t):
        return {"action": "pin"}
    if re.search(r"\bunpin\b|\bstop pinning\b|\blet it (?:go|fade)\b", t):
        return {"action": "unpin"}
    # image focus: "bigger", "zoom in on the third one", "show me the second one"
    if re.search(r"\b(?:bigger|enlarge|zoom in|blow (?:it|that) up|full ?size)\b", t) or \
       re.search(r"\b(?:show|focus|zoom)\b.*\bthe\s+(?:first|second|third|fourth|fifth|sixth|seventh|eighth|1st|2nd|3rd|4th)\s+(?:one|image|picture|photo|pic)\b", t):
        mo = re.search(r"\b(first|second|third|fourth|fifth|sixth|seventh|eighth|1st|2nd|3rd|4th)\b", t)
        return {"action": "focus", "index": _ORDINALS.get(mo.group(1), 0) if mo else 0}
    if re.search(r"\b(?:smaller|zoom out|back to the grid|show (?:them|the grid|all of them)( again)?)\b", t):
        return {"action": "focus", "index": None}
    # old tab-era phrasings land on the settings stage — the nearest designed surface
    if re.search(r"\b(?:show|bring up|pull up|open|display)\b.*\b(?:tabs|menu|navigation|nav|the bar|panels|hidden)\b", t):
        return {"action": "show", "view": "settings"}
    # "show settings", "open the history", "pull up diagnostics" — suffix optional now
    m = re.search(r"\b(?:show|bring up|pull up|open|go to|display|switch to)\b\s+(?:me\s+)?(?:the\s+|my\s+|your\s+)?([a-z]+)\s*(?:tab|panel|view|screen|page)?\b", t)
    if m and m.group(1) in _UI_VIEWS:
        return {"action": "show", "view": _UI_VIEWS[m.group(1)]}
    # "settings, history" / bare "settings" — land on a section directly
    ms = re.search(r"^(?:settings|options)\b[,:]?\s*([a-z]+)?$", t)
    if ms:
        sec = ms.group(1)
        return {"action": "show", "view": _UI_VIEWS.get(sec or "settings", "settings")}
    return None


def say_ui(slots: dict, res: dict) -> str:
    a = slots.get("action")
    if a == "pin" and slots.get("minutes"):
        n = slots["minutes"]
        return f"I'll keep it up for {n // 60} hour{'s' if n >= 120 else ''}." if n >= 60 else f"I'll keep it up for {n} minutes."
    return {"hide": "Done.", "pin": "Pinned.", "unpin": "Unpinned.", "restore": "Bringing it back.",
            "focus": "There."}.get(a, "Here you go.")


_PC_NOT_JARVIS = re.compile(r"\b(computer|pc|laptop|machine|desktop|workstation|windows|system)\b", re.I)


# "what time should i go to bed" is a question, not an order to stand down.
_SLEEP_QUESTION = re.compile(
    r"^\s*(?:what|when|how|why|where|which|should|do|does|did|can|could|is|are|tell me|give me)\b", re.I)


def slots_sleep(t: str) -> dict | None:
    """'go to sleep' is him; 'put the COMPUTER to sleep' is the machine (power_action),
    and 'how many hours should I sleep' is a question about sleep, not a dismissal.
    'wake up' embeds NEAR the sleep seeds (same topic) — without the guard he answers
    a wake request by going back to sleep."""
    if _PC_NOT_JARVIS.search(t) or _SLEEP_QUESTION.search(t):
        return None
    if re.search(r"\bwake\b|\bget up\b|\bcome back\b|\bi'?m back\b|\bmorning\b", t):
        return None
    return {}


def say_sleep(slots: dict, res: dict) -> str:
    return "Standing by." if "error" not in res else "I couldn't step aside."


def say_lock(_: dict, res: dict) -> str:
    return "Locking." if "error" not in res else "I couldn't lock the computer."


def say_media(slots: dict, res: dict) -> str:
    return {"play_pause": "Done.", "next": "Skipping.", "previous": "Going back.",
            "stop": "Stopped."}.get(slots.get("action", ""), "Done.") if "error" not in res else "I couldn't control playback."


def say_clipboard(_: dict, res: dict) -> str:
    text = str(res.get("text") or "").strip()
    if not text:
        return "Your clipboard is empty."
    return "Your clipboard has: " + (text[:200] + ("..." if len(text) > 200 else ""))


def slots_screenshot(t: str) -> dict | None:
    out: dict = {}
    m = re.search(r"\b(?:to|in|on|into)\s+(?:my\s+|the\s+)?(desktop|documents?|downloads?|pictures?)\b", t)
    if m:
        out["destination"] = m.group(1)
    m = re.search(r"\b(?:as|named?|called?)\s+['\"]?([a-z0-9 _-]{1,40}?)['\"]?[.!?]*$", t)
    if m:
        out["filename"] = m.group(1).strip()
    return out  # empty dict = defaults


_QUERY_LEAD = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,.]?\s+)?(?:can you\s+|could you\s+|please\s+|would you\s+)?"
    r"(?:search(?:\s+the\s+web|\s+online|\s+google)?\s+for|search(?:\s+the\s+web)?|look\s+up|google|"
    r"find(?:\s+me)?(?:\s+online)?|web\s+search(?:\s+for)?|research)\s+", re.I)


def slots_search(t: str) -> dict | None:
    q = _QUERY_LEAD.sub("", t.strip(), count=1).strip(" .?!")
    q = re.sub(r"\b(please|for me)\b", "", q).strip(" .?!")
    if _FOLDER_ONLY.match(q):
        return None   # "search my documents" means the user's files, not the web
    return {"query": q} if len(q) >= 3 and q != t.strip() else None


def slots_images(t: str) -> dict | None:
    """Shares the tools' cleaner so "show me iron man" and "show me 5 images of
    spiderman" both become keywords (+count). Only fires when something was
    actually command phrasing — a kNN misroute of plain prose stays None."""
    from tools.query_clean import clean_image_query
    q, count = clean_image_query(t.strip())
    if not q or q == t.strip().strip(" .?!"):
        return None
    out: dict = {"query": q}
    if count:
        out["count"] = count
    return out


def slots_reminder(t: str) -> dict | None:
    out: dict = {}
    m = re.search(r"\bin\s+(\d{1,3}|[a-z]+)\s+(minute|minutes|min|mins|hour|hours|hr)\b", t)
    if m:
        n = _number(m.group(1))
        if n is None:
            return None
        out["minutes_from_now"] = n * (60 if m.group(2).startswith(("hour", "hr")) else 1)
    else:
        m = re.search(r"\b(?:at|for|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", t)
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        ap = (m.group(3) or "").replace(".", "")
        evening = bool(re.search(r"\b(tonight|this evening|this afternoon)\b", t))
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap and evening and 1 <= h <= 11:
            h += 12                       # "at 9 tonight" -> 21:00
        out["at_time"] = f"{h:02d}:{mi:02d}"
        if re.search(r"\btomorrow\b", t):
            out["date"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    m = re.search(r"\b(?:to|that)\s+(.+?)(?:\s+(?:in|at|for|by)\s+\d\S*.*)?[.!?]*$", t)
    text = m.group(1).strip() if m else ""
    text = re.sub(r"^(?:remind me to|remind me|to)\s+", "", text).strip()
    if not text:
        return None
    out["text"] = text
    return out


def slots_remember(t: str) -> dict | None:
    m = re.search(r"\bremember\s+(?:that\s+)?(.+?)[.!?]*$", t)
    return {"content": m.group(1).strip()} if m and len(m.group(1)) > 3 else None


# ---------- speak templates ----------

def say_time(_: dict, __: dict) -> str:
    return "It's " + dt.datetime.now().strftime("%I:%M %p").lstrip("0") + "."


def say_date(_: dict, __: dict) -> str:
    d = dt.datetime.now()
    suf = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
    return f"It's {d.strftime('%A, %B')} {d.day}{suf}."


def say_volume(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't change the volume."
    return f"Volume set to {slots['percent']} percent."


def say_mute(slots: dict, res: dict) -> str:
    return "Muted." if slots.get("muted") else "Unmuted."


def say_open(slots: dict, res: dict) -> str:
    if "error" in res:
        return f"I couldn't open {slots['name']}."
    return f"Opening {slots['name']}."


def say_close(slots: dict, res: dict) -> str:
    name = slots["name"]
    if res.get("declined"):
        return "Alright, leaving it open."
    if res.get("unconfirmed"):
        return f"I needed a yes before closing {name}, so I left it alone."
    if "asked_to_close" in res:
        return f"I asked {name} to close, but it's still up - it may be waiting on you to save."
    if "error" in res:
        return f"{name.capitalize()} doesn't seem to be running."
    return f"Closing {name}."


def say_screenshot(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't take the screenshot."
    where = slots.get("destination")
    return f"Screenshot saved to your {where}." if where else "Screenshot saved."


def say_images(slots: dict, res: dict) -> str:
    if "error" in res:
        return f"I couldn't find pictures of {slots['query']}."
    return f"Here are some pictures of {slots['query']}."


def say_screen(_: dict, res: dict) -> str:
    return res.get("analysis") or "I couldn't get a look at the screen."


def screen_direct(text: str) -> bool:
    """Vision answers are final text (speak as-is); OCR results need the LLM to compose."""
    return False


def say_reminder(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't set that reminder."
    due = str(res.get("due", ""))          # "Saturday 18:00" from set_reminder
    try:
        day, hm = due.split()
        t = dt.datetime.strptime(hm, "%H:%M")
        when = t.strftime("%I:%M %p").lstrip("0")
        if "minutes_from_now" in slots:
            return f"Reminder set for {when}."
        return f"Reminder set for {when} {day}."
    except ValueError:
        return "Reminder set."


def say_remember(slots: dict, res: dict) -> str:
    return "Noted." if "error" not in res else "I couldn't save that."


def say_stats(_: dict, res: dict) -> str:
    free = float(res.get("disk_c_free_gb", 0) or 0)
    space = f"{free / 1000:.1f} terabytes" if free >= 1000 else f"{round(free)} gigabytes"
    return (f"CPU is at {round(res.get('cpu_percent', 0))} percent, memory at "
            f"{round(res.get('ram_percent', 0))} percent, with about {space} free.")


def say_windows(_: dict, res: dict) -> str:
    w = [x for x in res.get("windows", []) if x != "JARVIS"][:6]
    return ("You have " + ", ".join(w) + " open.") if w else "Nothing else is open."


# ---------- skill table ----------

@dataclass
class Skill:
    name: str
    tool: str | None                 # registry tool to run (None = no tool, e.g. time)
    seeds: list[str]
    slots: Callable[[str], dict | None] = lambda t: {}
    speak: Callable[[dict, dict], str] = lambda s, r: "Done."
    fixed_args: dict = field(default_factory=dict)
    llm_after: bool = False          # run the tool, then let the LLM compose the answer
    direct_if: Callable[[str], bool] | None = None   # llm_after skill may still go direct
    speak_first: bool = False        # announce ("Opening X.") before running the tool

    @property
    def label(self) -> str:
        """Human phrase for 'I'll <label>' (teach confirmations)."""
        return _LABELS.get(self.name, self.name.replace("_", " "))


_LABELS = {"ui": "change the view", "read_site": "read the site", "weather": "check the weather", "watch": "watch the system", "unwatch": "stop watching", "switch": "switch windows", "folder": "open the folder", "find_file": "find the file", "volume_set": "set the volume", "screenshot": "take a screenshot", "open_app": "open the app",
           "close_app": "close the app", "open_site": "open the site", "images": "find pictures",
           "search": "search the web", "screen": "look at the screen", "reminder": "set a reminder",
           "remember": "remember it", "stats": "check the system", "windows": "list your windows",
           "media_pause": "play or pause", "media_next": "skip the track", "media_previous": "go back a track",
           "clipboard": "read the clipboard", "lock": "lock the computer", "time": "tell the time",
           "date": "tell the date"}


_ELSEWHERE = re.compile(r"\bin\s+(?!the\s+(?:morning|afternoon|evening)\b)[a-z][a-z .'-]{2,}$|"
                       r"\b(?:time\s?zone|utc|gmt|est|pst|cst)\b")


def slots_clock(t: str) -> dict | None:
    """Local time only. 'what time is it in london' is a different question - let the
    model answer that one rather than confidently speaking the wrong clock."""
    return None if _ELSEWHERE.search(t) else {}


SKILLS: list[Skill] = [
    Skill("time", None, [
        "what time is it", "what's the time", "tell me the time", "do you have the time",
        "current time", "time check", "what time is it right now", "got the time",
        "what's the clock say", "time please", "whats the time now", "hey what time is it"],
        slots=slots_clock, speak=say_time),
    Skill("date", None, [
        "what's the date", "what day is it", "what is today's date", "what day is it today",
        "what's today", "date please", "which day is it", "what day of the week is it",
        "what is the date today", "tell me the date", "what's the day today"],
        slots=slots_clock, speak=say_date),
    Skill("volume_set", "set_volume", [
        "set the volume to 50 percent", "volume 30", "turn the volume to 40",
        "set volume at 70 percent", "make the volume 20", "change the volume to 80",
        "volume to sixty", "turn it up to 90", "turn it down to 25", "put the volume at 10",
        "set system volume to 45 percent", "lower the volume to 15"],
        slots=slots_volume, speak=say_volume),
    Skill("mute", "set_mute", [
        "mute", "mute the sound", "mute the audio", "silence the speakers", "mute everything",
        "shut the sound off", "turn the sound off", "kill the audio", "mute the pc"],
        slots=lambda t: {"muted": True}, speak=say_mute),
    Skill("unmute", "set_mute", [
        "unmute", "unmute the sound", "sound back on", "turn the sound back on",
        "unmute the speakers", "bring the audio back", "unmute the pc"],
        slots=lambda t: {"muted": False}, speak=say_mute),
    Skill("open_app", "open_application", [
        "open spotify", "launch chrome", "start notepad", "open the calculator",
        "fire up steam", "open vs code", "bring up task manager", "launch discord",
        "open file explorer", "start brave", "open settings", "put on spotify",
        "can you open notepad", "open up chrome for me", "run terminal"],
        slots=slots_app, speak=say_open, speak_first=True),
    Skill("close_app", "close_application", [
        "close notepad", "quit spotify", "close chrome", "exit steam", "kill discord",
        "shut down notepad", "close the calculator", "close brave", "quit vs code",
        "can you close notepad", "close task manager"],
        slots=slots_app, speak=say_close),
    Skill("screenshot", "take_screenshot", [
        "take a screenshot", "screenshot", "capture the screen", "grab a screenshot",
        "take a screenshot and save it to my desktop", "screenshot to my documents folder",
        "take a screen capture", "snap a screenshot", "take a screenshot of my screen",
        "save a screenshot to downloads", "take a screenshot called bug report"],
        slots=slots_screenshot, speak=say_screenshot),
    Skill("search", "web_search", [
        "search the web for the best mini pc", "look up the population of tokyo",
        "google the latest nvidia drivers", "search for cheap flights to denver",
        "find me reviews of the logitech c920", "search online for python tutorials",
        "look up who won the game last night", "web search for ryzen 8845hs benchmarks",
        "search the web for spider man release date", "research local llm benchmarks"],
        slots=slots_search, llm_after=True),
    Skill("images", "show_images", [
        "show me a picture of spider-man", "show me pictures of a nebula",
        "find me a photo of a golden retriever", "pull up images of the eiffel tower",
        "show me some pictures of worms", "show me an image of a black hole",
        "get me pictures of the northern lights", "display photos of mount everest",
        "show me a pic of iron man", "bring up pictures of a lamborghini",
        # bare forms — no media noun, or a spoken count ("show me iron man").
        # NOT "show me spiderman pictures": trailing "pictures" canonicalizes into
        # the Pictures-FOLDER pattern and clashes with the folder skill.
        "show me iron man", "show me the aurora borealis", "show me 5 images of spiderman",
        "show me three pictures of puppies"],
        slots=slots_images, speak=say_images),
    Skill("screen", "analyze_screen", [
        "look at my screen", "what's on my screen", "take a look at my screen",
        "what do you see on my screen", "describe my screen", "what am i looking at",
        "can you see my screen", "what's on the screen right now", "look at this",
        "tell me what you see on screen", "what's wrong with this screen"],
        speak=say_screen, llm_after=True),
    Skill("reminder", "set_reminder", [
        "remind me in 10 minutes to stretch", "set a reminder for 5 pm to call mom",
        "remind me in an hour to check the oven", "remind me at 9 to take my meds",
        "set a reminder in 20 minutes to drink water", "remind me in two hours to leave",
        "reminder at 3 pm to join the meeting", "remind me in 15 minutes that the laundry is done",
        "remind me at 7:30 pm to feed the cat"],
        slots=slots_reminder, speak=say_reminder),
    Skill("remember", "remember_fact", [
        "remember that i drink my coffee black", "remember my favorite color is blue",
        "remember that my wifi password is on the fridge", "remember i park in spot 12",
        "remember that my dentist is doctor lee", "note that i prefer short answers",
        "remember my sister's birthday is in june", "keep in mind that i work from home on fridays"],
        slots=slots_remember, speak=say_remember),
    Skill("stats", "get_system_stats", [
        "how's the system doing", "how much ram am i using", "what's my cpu usage",
        "system status", "how is the pc doing", "check system resources",
        "how much memory is free", "what's the cpu at", "give me a system report",
        "how much disk space do i have"],
        speak=say_stats),
    Skill("windows", "list_windows", [
        "what windows are open", "what do i have open", "list my open windows",
        "what apps are running", "which windows are open right now", "what's open",
        "show me what's open", "what programs are open"],
        speak=say_windows),
    Skill("media_pause", "media_control", [
        "pause", "pause the music", "pause playback", "play", "resume the music", "play the music",
        "pause spotify", "resume playback", "unpause", "pause that", "play pause"],
        fixed_args={"action": "play_pause"}, speak=say_media),
    Skill("media_next", "media_control", [
        "next song", "skip this song", "next track", "skip", "play the next one", "skip this track"],
        fixed_args={"action": "next"}, speak=say_media),
    Skill("media_previous", "media_control", [
        "previous song", "go back a song", "previous track", "play the last song again", "back one track"],
        fixed_args={"action": "previous"}, speak=say_media),
    Skill("clipboard", "get_clipboard", [
        "what's on my clipboard", "read my clipboard", "what did i copy", "what's in the clipboard",
        "read me what i just copied", "show me my clipboard"],
        speak=say_clipboard),
    Skill("open_site", "open_url", [
        "open youtube.com", "go to wikipedia.org", "pull up amazon.com", "open the website reddit.com",
        "take me to github.com", "open up netflix.com", "go to the website espn.com", "load bbc.com",
        "bring up espn.com", "open twitch.tv for me"],
        slots=slots_site, speak=say_site, speak_first=True),
    Skill("read_site", "browser_open", [
        "open example.com and tell me what the page says", "read me what's on wikipedia.org",
        "go to bbc.com and summarize the headlines", "what does example.com say",
        "look at reddit.com and tell me what's trending", "check espn.com and tell me the scores",
        "read the page at python.org", "pull up github.com and tell me what you see"],
        slots=slots_site, llm_after=True),
    Skill("folder", "list_folder", [
        "open my downloads folder", "show me my desktop", "show my documents", "open downloads",
        "what's in my downloads", "show me my pictures", "open the documents folder", "what's on my desktop",
        "list my downloads", "show me the files on my desktop", "browse my documents", "go to my downloads"],
        slots=slots_folder, speak=say_folder),
    Skill("find_file", "find_files", [
        "find the file called budget", "find my resume", "where is the file named invoice",
        "look for a file called notes", "search my documents for taxes", "find files with report in the name",
        "locate the folder called projects", "where's my screenshot from earlier", "find the document named plan"],
        slots=slots_find, speak=say_find),
    Skill("switch", "focus_window", [
        "switch to discord", "switch over to chrome", "focus on spotify", "go back to notepad",
        "jump to the browser", "switch to the settings window", "bring me to discord", "focus steam",
        "switch to visual studio code", "show me the spotify window"],
        slots=slots_switch, speak=say_switch),
    Skill("watch", "watch_metric", [
        "tell me if the cpu goes above 90 percent", "let me know when memory is over 90",
        "warn me if disk space drops below 100 gigabytes", "tell me when the battery is under 20 percent",
        "alert me if cpu stays above 95 for 5 minutes", "keep an eye on memory and tell me if it passes 95",
        "ping me when ram exceeds 90 percent", "let me know if the battery falls under 15"],
        slots=slots_watch, speak=say_watch),
    Skill("unwatch", "unwatch_metric", [
        "stop watching the cpu", "stop monitoring memory", "cancel the disk space alert",
        "stop telling me about the battery", "forget about the cpu rule", "stop watching everything"],
        slots=slots_unwatch, speak=say_unwatch),
    Skill("weather", "get_weather", [
        "what's the weather", "what's the weather like right now", "how's the weather today",
        "what's the weather in boston", "is it going to rain today", "what's the temperature outside",
        "do i need an umbrella", "what's the forecast for tomorrow", "how hot is it in phoenix",
        "is it cold out", "what's the weather like in london right now", "weather for tomorrow in framingham",
        "will it rain tonight", "is it raining right now", "will it snow tomorrow", "is it going to be hot today"],
        slots=slots_weather, speak=say_weather),
    Skill("ui", None, [
        "show me the files tab", "show the apps tab", "bring up the system panel", "open the settings tab",
        "show me the tabs", "show the hidden tabs", "bring up the menu", "pin that", "keep that panel up",
        "unpin it", "hide everything", "clear the panels", "dismiss that", "show the browser tab",
        "pull up the diagnostics panel", "show me the memory tab", "go to the tasks tab",
        # the stage era: sections by name, restore, timed pin, image focus (§6.3, §6.5).
        # "open settings" stays with open_app (canon "open APP") — show = stage, open = app.
        "show settings", "show your settings", "show the history", "show our conversation history",
        "settings history", "show diagnostics", "bring that back", "bring the pictures back",
        "keep it", "keep it for ten minutes", "keep that up for an hour",
        "make it bigger", "bigger", "zoom in on the third one", "show me the second one bigger",
        "back to the grid"],
        slots=slots_ui, speak=say_ui),
    Skill("wakeack", None, [
        # "wake up" embeds near the sleep cluster — without its own skill the guard in
        # slots_sleep sends it to the LLM, which answers a wake request with whatever
        # the history suggests. /text and the wake word already woke him; just answer.
        "wake up", "wake up jarvis", "are you awake", "you up", "good morning jarvis",
        "morning jarvis", "rise and shine", "time to wake up", "you there",
        "are you there jarvis"],
        speak=lambda _s, _r: "At your service."),
    Skill("sleep", "enter_sleep_mode", [
        # He gets dismissed in a lot of different moods, so the seeds cover the clusters:
        # explicit sleep, "we're finished", military stand-down, goodbyes, and get-lost.
        "go to sleep", "enter sleep mode", "sleep mode", "go to sleep mode",
        "activate sleep mode", "time to sleep",
        "that's all for now", "that will be all", "that's all", "nothing else for now",
        "that's everything for now", "that's it for now", "we're finished",
        "i'm finished for now", "i'm set for now",
        "that's enough for now", "nothing further",
        "stand down", "stand by", "dismissed", "you're dismissed", "you can go now",
        "take a break", "you can rest", "get some rest", "take a rest",
        "goodnight jarvis", "good night jarvis", "night jarvis",
        "see you later", "talk to you later", "catch you later", "bye for now",
        "minimize yourself", "hide yourself", "make yourself scarce",
        "get out of the way", "go away for now", "i'm done for now",
        "out of the way please", "step out of the way", "leave me alone for now",
        "i don't need you at the moment", "go quiet for now", "go dormant",
        "power down", "shut yourself down for now",
        "that's all i need", "that's all i needed", "that's all i wanted",
        "take five", "take ten", "leave me be", "leave me to it",
        "out of sight", "later jarvis", "see you jarvis", "adios jarvis",
        "go to standby", "standby mode", "enter standby", "back to standby",
        "you're off duty", "off duty", "take the night off", "clock out",
        "i'm good for now", "i'm done talking", "we can stop here", "that's a wrap",
        "run along", "off you go", "tuck yourself away", "hold off for now"],
        slots=slots_sleep, speak=say_sleep, speak_first=True),
    Skill("lock", "lock_computer", [
        "lock the computer", "lock my pc", "lock the screen", "lock it", "lock my computer",
        "lock the workstation", "lock windows", "lock up"],
        speak=say_lock),
    Skill("teach", None, [
        "when i say lights out, mute and open spotify", "from now on when i say good night, lock the computer",
        "teach you a command", "if i say movie time, set the volume to 70 and open netflix.com",
        "when i say focus mode, mute and close discord", "whenever i say wrap it up, take a screenshot",
        "when i say bedtime do mute", "teach you that when i say quiet time you mute"],
        slots=slots_teach),
    Skill("correction", None, [
        "no i meant open spotify", "no that's wrong", "not that", "nope i said mute",
        "that's not what i meant", "wrong, i wanted the volume at fifty", "no no no", "not that one"],
        slots=slots_correction),
    Skill("recall", "recall", [
        "what did i tell you about my coffee", "do you remember my favorite color",
        "what do you know about me", "what did i say my dentist's name was",
        "remind me what i told you about my car", "do you remember where i park",
        "what do you remember about my project", "what's my wifi password"],
        slots=lambda t: {"query": t}, llm_after=True),
]

SKILLS.append(Skill("general", None, [
    "tell me about the history of rome", "what's the difference between ram and vram",
    "explain how a transistor works", "write me a haiku about rain", "who is the president",
    "why is the sky blue", "what should i have for dinner", "give me a fun fact",
    "how do i boil an egg", "what do you think about electric cars", "summarize the plot of dune",
    "tell me a joke", "what does gpu stand for", "how are you doing today",
    "can you help me plan a trip", "translate hello to spanish", "what's a good name for a dog",
    "open the pod bay doors", "what is quantum computing", "who directed the matrix",
    "how old is the universe", "recommend a book", "what's the capital of peru",
    "compare python and rust", "what happened in 1969",
    "how many legs does a spider have", "how many moons does jupiter have", "how far is the moon",
    "give me a tip for sleeping better", "any advice for a job interview", "tell me a fun fact about space",
    "what's the tallest mountain", "who wrote hamlet", "what year did world war two end",
    "how do airplanes fly", "what's the speed of light", "is a tomato a fruit",
    "what rhymes with orange", "write a short poem", "tell me a story", "what's a synonym for happy",
    "how do you spell necessary", "what is 15 percent of 80", "how many ounces in a pound",
    "what does carpe diem mean", "why do cats purr", "how long do elephants live"]))

SKILL_BY_NAME = {s.name: s for s in SKILLS}
# tools that map to exactly one skill are safe to learn from (set_mute is mute/unmute)
_owners: dict[str, set[str]] = {}
for _s in SKILLS:
    if _s.tool:
        _owners.setdefault(_s.tool, set()).add(_s.name)
TOOL_TO_SKILL = {t: next(iter(n)) for t, n in _owners.items() if len(n) == 1}
