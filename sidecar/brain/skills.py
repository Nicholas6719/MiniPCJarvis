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
    # "put on some music" is a media request, not an app called "some music"
    if re.fullmatch(r"(?:some\s+|the\s+)?(?:music|tunes|songs?|a\s+song)", name):
        return None
    # a bare generic word or a phrase that reads like a sentence, not an app name
    if name.startswith(("down ", "off ", "the ", "all ")) or len(name.split()) > 3:
        return None
    if re.search(r"https?://|\b\w+\.(?:com|org|net|io|gov|edu|co|tv|ai)\b", name):
        return None  # a website, not an app -> the LLM routes it to open_url
    if re.search(r"\.(?:xlsx?|docx?|pptx?|pdf|txt|md|csv|png|jpe?g|gif|mp[34]|zip|json|log)$", name):
        return None  # a document -> the LLM finds/opens the file instead of launching an app
    # And finally: IS it an app? The router canonicalises "open|launch|start|run|
    # put on ..." onto one seed sentence, so it matches at cosine 1.00 and the
    # threshold never gets a say - "open a bank account", "run a diagnostic" and
    # "put on a movie" all arrived here as open_app. Worse, open_app speaks
    # first, so he heard "Opening a bank account." before anything was tried.
    # A name that matches nothing installed is not an app; refusing the slot
    # hands the turn to the LLM, which can answer it properly.
    try:
        from tools.builtin import looks_launchable
        if not looks_launchable(name):
            return None
    except Exception:
        pass                      # never let the check itself break the skill
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


def say_bin(_s: dict, res: dict) -> str:
    if "error" in res:
        return res["error"] if res["error"].startswith("nothing") else "I couldn't read the recycle bin."
    n = res.get("count", 0)
    if n == 0:
        return "Your recycle bin is empty."
    items = res.get("items", [])
    names = ", ".join(i["name"] for i in items[:3])
    if n <= 3:
        return f"The recycle bin has {n} item{'s' if n != 1 else ''}: {names}."
    return f"The recycle bin has {n} items. The most recent are {names}."


def say_restore(slots: dict, res: dict) -> str:
    if "error" in res:
        return res["error"] + "."
    return f"Restored {res.get('restored')}."


_RESTORE = re.compile(r"\b(?:restore|undelete|put\s+back|bring\s+back|recover|undo\s+the\s+delete)\b"
                      r"(?:\s+(?:the|my|that)?\s*)?(.{2,60}?)?(?:\s+from\s+(?:the\s+)?(?:recycle\s*bin|trash))?[.!?]*$")


def slots_restore(t: str) -> dict | None:
    m = _RESTORE.search(t)
    if not m:
        return None
    name = (m.group(1) or "").strip(" '\"")
    name = re.sub(r"^(?:file|folder|document)s?\s+(?:called|named)\s+", "", name)
    return {"name": name} if len(name) >= 2 else None



# ---- markets and news (realm 2: live every time, never cached) ---------------
_TICKER_LEAD = re.compile(
    r"\b(?:price|quote|stock|share[s]?|trading at|worth|cost of|how much is|"
    r"how'?s|hows|what'?s|whats|is)\b", re.I)
# A company can appear after a preposition ("price OF apple", "say about apple")
# or straddled by the question itself ("what's APPLE trading at", "is NVIDIA a buy").
_CO_PATTERNS = [
    re.compile(r"\b(?:price|quote|target|rating|ratings|recommendations?)\s+(?:of|for|on)\s+(?P<n>.+?)(?:\s+stock|\s+shares?)?[.!?]*$", re.I),
    re.compile(r"\b(?:about|regarding|on)\s+(?P<n>.+?)[.!?]*$", re.I),
    re.compile(r"\b(?:what'?s|whats|how'?s|hows|how is|what is|how are)\s+(?P<n>.+?)\s+(?:trading|doing|stock|shares?|at|priced)\b", re.I),
    re.compile(r"^\s*(?:is|are)\s+(?P<n>.+?)\s+(?:a|an)\s+(?:good\s+)?(?:buy|sell|hold)\b", re.I),
    re.compile(r"\b(?:of|for)\s+(?P<n>.+?)[.!?]*$", re.I),
]
# trailing words that are never part of a company name
_CO_TAIL = re.compile(
    r"\s*\b(?:stock|stocks|shares?|share price|stock price|price|quote|doing|today|"
    r"right now|now|at|a buy|a good buy|a sell|a hold|currently|this morning)\b\s*$", re.I)
# Checked BEFORE and AFTER the article strip: "the market" and the "market" it
# becomes must both be refused, or "how's the market doing" asks Finnhub for a
# company called Market instead of falling through to the market-wide skill.
_STOP_CO = {"market", "markets", "stock market", "economy", "news", "it", "that",
            "them", "things", "stocks", "shares", "portfolio", "my portfolio",
            "everything", "analysts", "wall street", "s and p", "sp 500", "dow",
            "nasdaq", "the market", "the markets", "the stock market", "the economy",
            "the news", "the analysts"}


def _company_from(t: str) -> str | None:
    for pat in _CO_PATTERNS:
        m = pat.search(t)
        if not m:
            continue
        name = re.sub(r"\s+", " ", m.group("n")).strip(" .?!'\"")
        for _ in range(3):                     # "nvidia stock price" -> "nvidia"
            trimmed = _CO_TAIL.sub("", name).strip()
            if trimmed == name:
                break
            name = trimmed
        if name.lower() in _STOP_CO:
            return None
        name = re.sub(r"^(?:the|a|an)\s+", "", name, flags=re.I).strip()
        if (name and name.lower() not in _STOP_CO and 1 < len(name) <= 34
                and name.lower() not in ("the", "a", "an", "this", "that", "my")):
            return name
    return None


def slots_quote(t: str) -> dict | None:
    """"what's apple trading at" -> {'symbol': 'apple'}. Refuses when no company is
    named, so "how's the market doing" falls through to the market-wide skill."""
    if not _TICKER_LEAD.search(t):
        return None
    name = _company_from(t)
    return {"symbol": name} if name else None


def say_quote(slots: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    d, pct = res.get("change", 0), res.get("percent", 0)
    way = "up" if d > 0 else "down" if d < 0 else "flat"
    if way == "flat":
        return f"{res['name']} is at {res['price']} dollars, flat on the day."
    return (f"{res['name']} is at {res['price']} dollars, {way} "
            f"{abs(d)} or {abs(pct)} percent today.")


def slots_analyst(t: str) -> dict | None:
    name = _company_from(t)
    return {"symbol": name} if name else None


def say_analyst(slots: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    n = res.get("analysts", 0)
    line = (f"Of {n} analysts covering {res['name']}, {res['buy']} say buy, "
            f"{res['hold']} hold and {res['sell']} sell.")
    if res.get("target_mean"):
        line += f" Their average price target is {res['target_mean']} dollars."
    return line


def say_to_phone(_s: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    return "Sent to your phone, sir."


def say_article(_s: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    title = (res.get("title") or "").strip()
    return f"Opening it: {title}." if title else "Opening it now."


def say_take(_s: dict, res: dict) -> str:
    """The market view. Already composed as speech by analyst.py - do not rebuild
    it here, or the judgement decays back into the list of prices he rejected."""
    if "error" in res:
        return res["error"]
    return res.get("spoken") or "I couldn't get a read on the market right now."


def say_watchlist(_s: dict, res: dict) -> str:
    """His own names, biggest mover first — that is the one he wants to hear."""
    if "error" in res:
        return res["error"]
    rows = res.get("stocks") or []
    if not rows:
        return "None of your stocks came back just now."
    parts = []
    for r in rows[:5]:
        pct = r.get("percent") or 0
        way = "up" if pct > 0 else "down" if pct < 0 else "flat"
        parts.append(f"{r['name']} is {way} {abs(pct)} percent at {r['price']} dollars"
                     if way != "flat" else
                     f"{r['name']} is flat at {r['price']} dollars")
    return "; ".join(parts) + "."


def say_markets(_s: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    parts = []
    for m in res.get("markets", []):
        p = m["percent"]
        way = "up" if p > 0 else "down" if p < 0 else "flat"
        parts.append(f"{m['name']} is {way} {abs(p)} percent" if way != "flat"
                     else f"{m['name']} is flat")
    return ("; ".join(parts) + ".") if parts else "No market data came back."


_NEWS_TOPICS = {
    "world": "world", "international": "world", "global": "world",
    "us": "us", "national": "us", "america": "us", "american": "us",
    "business": "business", "market": "business", "markets": "business",
    "financial": "business", "finance": "business", "economy": "business",
    "tech": "technology", "technology": "technology",
    "science": "science", "space": "science",
    "sport": "sports", "sports": "sports",
    "local": "local", "boston": "local", "massachusetts": "local",
}
_NEWS_ABOUT = re.compile(r"\b(?:about|on|regarding|around|to do with)\s+(.{2,40}?)[.!?]*$", re.I)


def slots_news(t: str) -> dict | None:
    out: dict = {}
    for word, topic in _NEWS_TOPICS.items():
        if re.search(r"\b" + word + r"\b", t):
            out["topic"] = topic
            break
    m = _NEWS_ABOUT.search(t)
    if m:
        q = m.group(1).strip(" .?!'\"")
        q = re.sub(r"^(?:the|a|an)\s+", "", q, flags=re.I).strip()
        # "news about technology" is a topic, not a keyword search
        if q and q.lower() not in _NEWS_TOPICS and len(q) > 2:
            out["query"] = q
    return out


def _headline_lines(items: list, limit: int) -> str:
    return " ".join(f"{i['headline']}, from {i['source']}, {i['when']}."
                    for i in items[:limit])


def say_news(slots: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    items = res.get("items") or []
    if not items:
        return "Nothing came back just now."
    what = slots.get("query") or ("top" if res.get("topic") == "top" else res.get("topic", "top"))
    head = f"Here's the latest on {what}." if slots.get("query") else f"Here's the {what} news."
    return head + " " + _headline_lines(items, 3)


def say_breaking(_s: dict, res: dict) -> str:
    if "error" in res:
        return res["error"]
    if res.get("nothing_breaking"):
        latest = res.get("latest") or []
        return ("Nothing breaking in the last few hours. The most recent is: "
                + _headline_lines(latest, 1)) if latest else "Nothing breaking right now."
    return "Breaking in the last few hours. " + _headline_lines(res.get("items") or [], 3)


_SWITCH = re.compile(r"\b(?:switch (?:over )?to|focus on|focus|go back to|jump to|bring me to|show me the)\s+(?:the\s+|my\s+)?(.+?)(?:\s+window|\s+app)?[.!?]*$")


def slots_switch(t: str) -> dict | None:
    m = _SWITCH.search(t)
    if not m:
        return None
    name = m.group(1).strip()
    # "switch to a british voice" is about HIS voice, not a window title
    if re.search(r"\b(?:voice|accent|language|tone)\b", name):
        return None
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
    # "minimize everything" is about the WINDOWS; "be quieter" is about volume;
    # "go to sleep in an hour" is a timer we don't have — all better honest than wrong
    if re.search(r"\b(?:minimize|close|hide)\b.*\b(?:everything|all)\b", t):
        return None
    if re.search(r"\bquieter\b|\bquiet(?:er)?\s+down\b|\blower your voice\b", t):
        return None
    if re.search(r"\bin\s+(?:a|an|one|\d+)\s*(?:hours?|minutes?|min)\b", t):
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
    # "screenshot with the grid" is the REMOTE click-grid capture, a different tool
    if re.search(r"\bgrid\b", t):
        return None
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
    # "pictures FROM my trip" are the user's own photos, not a web search
    if re.search(r"\bfrom\s+(?:my|our|the)\b", t):
        return None
    q, count = clean_image_query(t.strip())
    if not q or q == t.strip().strip(" .?!"):
        return None
    out: dict = {"query": q}
    if count:
        out["count"] = count
    return out


# "every night at 9" is a STANDING reminder, not one Sunday evening. Without
# this it was set once, for the next 9 pm, and the confirmation said "Sunday" —
# so the only way to get a daily reminder was to notice and argue about it.
_RECURRING = (
    (re.compile(r"\bevery\s+(?:week)?day\b|\bevery\s+(?:night|evening|morning|"
                r"afternoon)\b|\b(?:daily|each\s+day|each\s+night|every\s+single\s+day)\b"
                r"|\bevery\s+day\b", re.I), "daily"),
    (re.compile(r"\bevery\s+week\b|\bweekly\b|\bevery\s+(?:monday|tuesday|wednesday|"
                r"thursday|friday|saturday|sunday)\b", re.I), "weekly"),
    (re.compile(r"\bevery\s+weekday\b|\bon\s+weekdays\b|\bweekdays\b|"
                r"\bmonday\s+(?:to|through)\s+friday\b", re.I), "weekdays"),
)


def _recurrence_in(t: str) -> str:
    # weekdays is checked last because "every weekday" also matches the daily
    # pattern's "every day"; the most specific reading should win
    found = "none"
    for pat, name in _RECURRING:
        if pat.search(t):
            found = name
    return found


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
        else:
            # "every Monday at 9" has to START on a Monday, or the weekly
            # repeat lands on whatever day it happened to be set — it read
            # back "every Sunday" for a reminder asked for on Mondays.
            days = ("monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday")
            named = next((i for i, d in enumerate(days)
                          if re.search(r"\b" + d + r"s?\b", t, re.I)), None)
            if named is not None:
                today = dt.date.today()
                ahead = (named - today.weekday()) % 7
                if ahead == 0 and (h, mi) <= (dt.datetime.now().hour,
                                              dt.datetime.now().minute):
                    ahead = 7                      # today's has already gone
                out["date"] = (today + dt.timedelta(days=ahead)).isoformat()
    m = re.search(r"\b(?:to|that)\s+(.+?)(?:\s+(?:in|at|for|by)\s+\d\S*.*)?[.!?]*$", t)
    text = m.group(1).strip() if m else ""
    text = re.sub(r"^(?:remind me to|remind me|to)\s+", "", text).strip()
    # "every night" belongs to the schedule, not to the thing being remembered
    text = re.sub(r"\b(?:every|each)\s+(?:single\s+)?"
                  r"(?:day|night|evening|morning|afternoon|week|weekday)\b\s*", "",
                  text, flags=re.I).strip(" ,")
    # politeness is addressed to him, not part of what he is holding on to
    text = re.sub(r"\s*\b(?:please|thanks|thank you)\b\s*$", "", text, flags=re.I).strip(" ,")
    if not text:
        return None
    out["text"] = text
    rec = _recurrence_in(t)
    if rec != "none":
        out["recurrence"] = rec
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


def say_look(slots: dict, res: dict) -> str:
    """What he hears after asking JARVIS to look.

    It reports what the model actually returned, including nothing. Eighty
    nouns is a narrow window on a room, and pretending otherwise is how he
    stops trusting the answer.
    """
    if res.get("error"):
        return f"I couldn't look, sir — {res['error']}."
    said = res.get("said") or "nothing I recognise"
    if said == "nothing I recognise":
        return "Nothing I recognise, sir."
    return f"I can see {said}, sir."


def say_camera_sees(slots: dict, res: dict) -> str:
    """Answer "can you see me" honestly, including when the camera is shut."""
    # .get(), NOT `in`. camera.status() ALWAYS carries an "error" key, set to
    # None when everything is fine, so `if "error" in res` was true every single
    # time and "can you see me" answered "I can't tell, sir." even with the
    # camera open and his face in the frame.
    if res.get("error"):
        return "I can't tell, sir."
    if not res.get("on"):
        return "The camera is off, sir, so I can't see anything."
    pres = res.get("presence") or {}
    if pres.get("error"):
        return "The camera is on, sir, but I can't make out faces."
    if pres.get("present"):
        n = pres.get("faces") or 1
        return "I can see you, sir." if n == 1 else f"I can see {n} people, sir."
    return "The camera is on, sir, but I don't see anyone."


def say_camera(slots: dict, res: dict) -> str:
    """What he hears after asking for the camera.

    It reports what the device ACTUALLY did rather than what was asked for. A
    camera that says "camera on" while the handle never opened is the worst
    possible answer — he would believe it was watching when it was not, and the
    reverse when it was.
    """
    if res.get("error"):
        return f"I couldn't open the camera, sir — {res['error']}."
    return "Camera on, sir." if res.get("camera") == "on" else "Camera off, sir."


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
    # Say what was STORED, never what was asked for. He was once told "9:00 PM
    # daily" for a reminder actually sitting at 3:46 PM, which is worse than
    # setting it wrong: he had no reason to check.
    if res.get("spoken"):
        return str(res["spoken"])
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


_STOP_REMIND = re.compile(
    r"\b(?:stop|quit|cancel|don'?t|do not|no more)\b[^.]*?\b(?:remind(?:ing|er|ers)?|alerts?|nag(?:ging)?)\b"
    r"(?:\s*(?:me|us))?(?:\s*(?:to|about|for|on)\s+(?P<what>.+?))?(?:\s+anymore|\s+any more|\s+again)?[.!?]*$")
_CANCEL_ALL = re.compile(r"\b(?:cancel|clear|delete|remove)\b.*\b(?:all\s+)?(?:my\s+)?"
                         r"(?:remind(?:ers?|ing)|alarms?)\b")


def slots_unremind(t: str) -> dict | None:
    """'don't remind me to stretch anymore' -> {'query': 'stretch'};
    'cancel my reminders' -> {'query': ''} (all)."""
    m = _STOP_REMIND.search(t)
    if m:
        what = (m.group("what") or "").strip(" .!?'\"")
        what = re.sub(r"\s+(?:anymore|any more|again|please|ever)$", "", what).strip()
        return {"query": what}
    if _CANCEL_ALL.search(t):
        return {"query": ""}
    return None


def say_unremind(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't change your reminders."
    if res.get("none_pending"):
        return "You have no reminders set."
    n = res.get("cancelled", 0)
    if n == 0:
        q = slots.get("query")
        return f"I don't have a reminder about {q}." if q else "You have no reminders set."
    if n == 1:
        return f"Done. I won't remind you about {res['texts'][0]} again."
    return f"Done. I've cancelled {n} reminders."


def say_reminders(_s: dict, res: dict) -> str:
    rem = res.get("reminders") or []
    if not rem:
        return "You have no reminders set."
    if len(rem) == 1:
        r = rem[0]
        return f"One reminder: {r['text']}, at {r['due'][11:]}."
    head = "; ".join(f"{r['text']} at {r['due'][11:]}" for r in rem[:3])
    return f"You have {len(rem)} reminders: {head}."


_VOL_UP = re.compile(r"\b(?:turn|crank|bump|pump)\s*(?:it|the volume|the sound)?\s*up\b|"
                     r"\b(?:louder|loud er|more volume|volume up|speak up)\b")
_VOL_DOWN = re.compile(r"\b(?:turn|bring|knock)\s*(?:it|the volume|the sound)?\s*down\b|"
                       r"\b(?:quieter|quiet down|softer|lower the volume|volume down|"
                       r"not so loud|too loud|keep it down)\b")
_VOL_STEP = re.compile(r"\b(?:a lot|a bit|a little|slightly|way)\b")


def slots_volume_rel(t: str) -> dict | None:
    up, down = bool(_VOL_UP.search(t)), bool(_VOL_DOWN.search(t))
    if up == down:
        return None                      # neither, or a contradictory both
    m = _VOL_STEP.search(t)
    step = 25 if m and m.group(0) in ("a lot", "way") else 8 if m else 15
    return {"direction": "up" if up else "down", "step": step}


def say_volume_rel(slots: dict, res: dict) -> str:
    if "error" in res:
        return "I couldn't change the volume."
    return f"{'Louder' if slots.get('direction') == 'up' else 'Quieter'}, now {res.get('volume_percent')} percent."


def say_desktop(_s: dict, res: dict) -> str:
    return "I couldn't do that." if "error" in res else "Desktop cleared."


def say_restore_win(_s: dict, res: dict) -> str:
    return "I couldn't do that." if "error" in res else "Windows are back."


_FIRST_TO_SECOND = [
    (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bi am\b", "you are"),
    (r"\bi'm\b", "you're"), (r"\bi\b", "you"), (r"\bme\b", "you"),
    (r"\bmyself\b", "yourself"), (r"\bwe\b", "we"),
]


def _to_second_person(s: str) -> str:
    """Memories are stored in the user's own words ("my desk lamp is on the left");
    speaking that back verbatim sounds like JARVIS owns the lamp."""
    out = s.strip()
    for pat, rep in _FIRST_TO_SECOND:
        out = re.sub(pat, rep, out, flags=re.I)
    return out[0].upper() + out[1:] if out else out


def say_recall(slots: dict, res: dict) -> str:
    """Answers from memory WITHOUT the LLM when one memory clearly matches —
    recall was the slowest thing he did (11 s to speak a sentence already on disk)."""
    if "error" in res:
        return "I couldn't search my memory."
    mems = res.get("memories") or []
    if not mems:
        return "I don't have anything about that."
    one = res.get("direct") or (mems[0]["content"] if len(mems) == 1 else None)
    if one:
        said = _to_second_person(one)
        said = said[0].lower() + said[1:]
        return f"You told me {said.rstrip('.')}."
    head = "; ".join(_to_second_person(m["content"]).rstrip(".") for m in mems[:3])
    return f"A few things: {head}."


_THANKS_BUT_DISMISSAL = re.compile(
    r"\bthat'?s (?:all|it|everything)\b|\bfor now\b|\bwe'?re (?:done|finished)\b|"
    r"\bgood ?night\b|\bnothing (?:else|more)\b|\bi'?m (?:done|good|set)\b|"
    r"\bsee you\b|\btalk (?:to you )?later\b|\bstand down\b|\bthat is all\b")


def slots_thanks(t: str) -> dict | None:
    """A courtesy is only a courtesy. "Thanks, that's all for now" is a DISMISSAL
    with a thank-you attached — adding this skill made it answer "Of course."
    and stay awake, which is a worse answer than the one it replaced."""
    return None if _THANKS_BUT_DISMISSAL.search(t) else {}


def say_thanks(_s: dict, _r: dict) -> str:
    """He used to hand a bare 'thank you' to the model, which once answered by
    repeating the user's own words back ('Thank you Jarvis, sir.')."""
    import random as _r2
    return _r2.choice(["Of course.", "My pleasure.", "Anytime.", "Of course, sir."])


def say_go_ahead(_s: dict, _r: dict) -> str:
    """He has announced a question, not asked one.

    "I have a question for you!" came back "Spiders have eight legs." - an answer
    to a question he had not asked yet, pulled out of nowhere. A person hearing
    someone wind up to ask something says "go ahead", and waits.
    """
    import random as _r3
    return _r3.choice(["Of course, sir.", "Go ahead, sir.", "Ask away, sir.",
                       "I'm listening, sir."])


def say_story_time(_s: dict, _r: dict) -> str:
    """"When was that?" about the thing he was just told.

    He asked it straight after an alert and got "That question came up earlier
    today." - the router had matched it to PROVENANCE, which answers "when did
    YOU learn this", so "that" resolved to his own question instead of the news.

    Two honesty rules here:
      * this reports when the story was PUBLISHED, not when the event happened.
        Those are different, often by hours, and claiming the second when only
        the first is known is exactly the kind of confident wrong answer that
        makes an assistant untrustworthy about news.
      * if the subject has gone stale, or was never a story, say so plainly and
        let provenance answer instead of guessing.
    """
    from lastseen import last_seen
    if last_seen.stale or not last_seen.links:
        return say_provenance(_s, _r)
    link = last_seen.links[0]
    title = str(link.get("title") or "").strip()
    src = str(link.get("source") or "").strip()
    age = link.get("age_minutes")
    when = str(link.get("when") or "").strip()

    if isinstance(age, (int, float)) and age >= 0:
        mins = int(age)
        if mins < 90:
            ago = f"about {max(1, mins)} minutes ago"
        elif mins < 48 * 60:
            hours = round(mins / 60)
            ago = "about an hour ago" if hours == 1 else f"about {hours} hours ago"
        else:
            ago = f"about {round(mins / 1440)} days ago"
    elif when:
        ago = when
    else:
        return (f"I don't have a time on that one, sir - only that {src} carried it."
                if src else "I don't have a time on that one, sir.")

    lead = f"That was published {ago}"
    if title:
        lead += f": {title.rstrip('.')}"
    return f"{lead}." + (f" That's {src}." if src else "")


def say_provenance(_s: dict, _r: dict) -> str:
    """He never volunteers sources (user's rule) — but must answer for them."""
    from brain.facts import facts as _facts
    f = _facts.last_served
    if not f:
        return "That came from my own reasoning this turn, not a stored fact."
    try:
        host = re.sub(r"^www\.", "", (f["sources"][0]["url"].split("/")[2]))
    except Exception:
        host = "the web"
    when = dt.date.fromtimestamp(f["verified_ts"]).strftime("%B %d")
    return f"From {host}, last verified {when}."


SKILLS: list[Skill] = [
    Skill("go_ahead", None, [
        "i have a question for you", "i have a question", "can i ask you something",
        "question for you", "quick question", "i want to ask you something",
        "got a question for you", "let me ask you something", "can i ask you a question",
        "i need to ask you something", "i wanted to ask you something"],
        # NOT "are you there" / "you awake" / "you around" - those are asking
        # whether he is PRESENT, which is wakeack's job, and adding them here
        # stole the phrase from it. Caught by tests/test_brain.py, which is
        # exactly the seed-collision risk a new skill carries.
        speak=say_go_ahead),
    Skill("story_time", None, [
        "when was that", "when did that happen", "when did this happen",
        "how long ago was that", "when was this", "what time did that happen",
        "how recent is that", "when did that come out", "how old is that story",
        "when was that reported"],
        speak=say_story_time),
    Skill("provenance", None, [
        "how do you know that", "what's your source", "where did you get that",
        "says who", "where did that come from", "what is your source for that",
        "how do you know this", "who told you that", "can you cite that"],
        speak=say_provenance),
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
    # The camera. Three skills, not one, because "toggle" and "turn it off" mean
    # different things and he should not have to guess which word works. The
    # toggle seeds are his own phrasing: "toggle camera view mode".
    Skill("camera_toggle", "set_camera", [
        "toggle camera view mode", "toggle camera view", "toggle the camera",
        "camera view mode", "toggle camera mode", "switch camera view mode",
        "flip the camera view", "camera view"],
        speak=say_camera),
    # NOTE: "open the camera", "bring up the camera", "close the camera",
    # "close the webcam" and "hide the camera" are deliberately ABSENT. _CANON
    # rewrites them to "open APP" / "close APP" / "hide everything" — the exact
    # canonical strings that open_app, close_app and the UI skill own. Seeding
    # them here made every "open spotify" and "close notepad" match the camera
    # at cosine 1.000 and stole app launching outright. Any new seed goes
    # through _norm() first; if it comes back changed, it belongs to something
    # else.
    Skill("camera_on", "set_camera", [
        "turn the camera on", "show me the camera", "camera on",
        "turn on the webcam", "pull up the camera", "put the camera up",
        "let me see the camera", "show me the webcam",
        "i want to see the camera", "let me see myself"],
        fixed_args={"on": True}, speak=say_camera),
    Skill("camera_off", "set_camera", [
        "turn the camera off", "camera off", "turn off the webcam",
        "shut the camera off", "stop the camera", "put the camera away",
        "i'm done with the camera", "switch the camera off"],
        fixed_args={"on": False}, speak=say_camera),
    Skill("look_at", "look", [
        # "look at this" is deliberately ABSENT: the `screen` skill owns it, and
        # at a desk "look at this" means the monitor far more often than the
        # webcam. The seed-collision gate caught it; camera phrasings here all
        # name the camera or ask what is in FRONT of him.
        "what do you see", "what can you see", "look through the camera",
        "what's in front of you", "what am i holding", "what's on the camera",
        "tell me what you see through the camera", "describe what you see",
        "what do you see right now", "look and tell me what's there"],
        speak=say_look),
    Skill("camera_sees", "camera_status", [
        "can you see me", "do you see me", "am i on camera",
        "can you see anything", "what do you see on the camera",
        "is anyone there", "do you see anyone", "can you see my face"],
        speak=say_camera_sees),
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
    Skill("grid_shot", "screenshot_grid", [
        # the remote-control capture: a labelled A1..F8 overlay to click by name
        "take a grid screenshot", "screenshot with the grid", "send me a grid screenshot",
        "take a screenshot with the click grid on it", "grid screenshot",
        "screenshot with the click grid", "show me the screen with the grid",
        "screenshot the screen with a grid so i can click"],
        slots=lambda t: {}, speak=lambda _s, r: (
            "I couldn't take that." if "error" in r else
            'There you are. Say a cell, like "click C4".')),
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
        # NOT "put on some music": "put on" canonicalizes to the open-APP form and
        # clashes with open_app; slots_app rejects music-words so it falls to the LLM.
        "pause spotify", "resume playback", "unpause", "pause that", "play pause",
        "play some music"],
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
    Skill("volume_rel", "adjust_volume", [
        # relative volume — nobody speaks in percentages
        "turn it up", "turn it down", "turn the volume up", "turn the volume down",
        "a bit louder", "a little quieter", "be quieter", "too loud", "keep it down",
        "louder please", "quiet down", "turn it down a bit", "crank it up"],
        slots=slots_volume_rel, speak=say_volume_rel),
    Skill("show_desktop", "show_desktop", [
        # NOT "show me my desktop" (that means the Desktop FOLDER, and always has)
        # and NOT "hide all my windows"/"clear my screen" (they canonicalize onto
        # the ui skill's "hide everything", which dismisses the HUD stage).
        "minimize everything", "minimize all my windows", "minimize all windows",
        "get everything out of the way", "minimize my windows",
        "minimize all the windows on my screen"],
        slots=lambda t: {}, speak=say_desktop),
    Skill("restore_windows", "restore_windows", [
        "bring my windows back", "restore my windows", "undo that minimize",
        "put the windows back", "unminimize everything"],
        slots=lambda t: {}, speak=say_restore_win),
    Skill("unremind", "cancel_reminders_matching", [
        # NOT "clear my reminders" (canonicalizes onto the ui skill's "hide
        # everything") and NOT "no more reminders" (collides with corrections).
        "don't remind me to stretch anymore", "stop reminding me to stretch",
        "cancel my reminders", "cancel all my reminders",
        "stop reminding me about the laundry", "delete my reminders",
        "don't remind me about that anymore", "stop the reminders",
        "turn off my reminders"],
        slots=slots_unremind, speak=say_unremind),
    Skill("reminders", "list_reminders", [
        "what reminders do i have", "list my reminders", "what am i being reminded about",
        "do i have any reminders", "show me my reminders", "what's on my reminder list",
        "what reminders are set"],
        speak=say_reminders),
    Skill("thanks", None, [
        "thank you", "thanks", "thank you jarvis", "thanks jarvis", "cheers",
        "much appreciated", "appreciate it", "thanks a lot", "thank you very much",
        "nice work", "good job", "well done"],
        slots=slots_thanks, speak=say_thanks),
    Skill("quote", "get_stock_quote", [
        "what's apple trading at", "what's the price of nvidia", "how's tesla stock doing",
        "what is microsoft stock at", "price of amazon shares", "how much is google stock",
        "what's nvidia at right now", "quote for apple", "how is apple stock doing today",
        "how is nvidia stock doing", "how are apple shares doing", "what is tesla at today",
        "how's amazon stock", "what's meta trading at today",
        # the bare shapes a ticker canonicalises INTO ("price of RIVN" becomes
        # "price of apple") — the target of a rewrite has to be a seed itself
        "price of apple", "what is apple at"],
        # Tickers need no seeds of their own: _ticker_to_company in the router
        # rewrites "what's AAPL trading at" to this shape before it is embedded.
        # Seeding them individually taught it nothing about the NEXT ticker.
        slots=slots_quote, speak=say_quote),
    Skill("analyst", "get_analyst_view", [
        "what do analysts say about apple", "is nvidia a buy", "analyst ratings for tesla",
        "what's the price target on microsoft", "do analysts like amazon",
        "what are the analyst recommendations for google", "is tesla a good buy according to analysts"],
        slots=slots_analyst, speak=say_analyst),
    Skill("to_phone", "send_to_phone", [
        "send it to my phone", "send that to my phone", "send it to me",
        "send that to me", "send it through telegram", "text it to me",
        "send me that on telegram", "put that on my phone", "send it over"],
        speak=say_to_phone),
    Skill("article", "open_article", [
        "give me the article", "open the article", "pull up the article",
        "show me the source", "open that story", "pull it up",
        "let me read it", "open the link", "show me the full story"],
        speak=say_article),
    Skill("watchlist", "get_watchlist", [
        "how are my stocks doing", "how's my portfolio", "how is my portfolio doing",
        "check my stocks", "how are my stocks", "what are my stocks doing",
        "how are my positions", "how's my watchlist", "check my portfolio",
        "how are my shares doing"],
        speak=say_watchlist),
    Skill("market_take", "market_take", [
        "what are experts saying about the market", "what stocks should i watch",
        "what should i be watching today", "what are people talking about in the market",
        "what stocks are experts talking about", "what's moving today",
        "give me your market take", "what do analysts like right now",
        "any stocks worth looking at", "is now a good time to buy",
        "what's worth buying", "what are the top stocks today"],
        speak=say_take),
    Skill("markets", "get_market_movers", [
        "how's the market doing", "how are the markets", "how did the market close",
        "what's the market doing today", "how's the stock market", "are the markets up",
        "how's the s and p doing", "market check"],
        speak=say_markets),
    Skill("news", "get_news", [
        "what's in the news", "give me the news", "what's happening in the world",
        "catch me up on the news", "any news today", "what's the latest news",
        "tell me the tech news", "what's happening in business", "sports news",
        "what's the local news", "read me the headlines", "news about the election"],
        slots=slots_news, speak=say_news),
    Skill("breaking", "get_breaking_news", [
        "any breaking news", "what's breaking", "anything breaking right now",
        "has anything happened", "anything urgent in the news"],
        speak=say_breaking),
    Skill("recycle_bin", "list_recycle_bin", [
        "what's in the recycle bin", "what files are in the recycle bin",
        "show me the recycle bin", "check the recycle bin", "what's in the trash",
        "show me the trash", "what did i delete", "what's in my bin",
        "list the recycle bin", "anything in the recycle bin"],
        speak=say_bin),
    Skill("restore_file", "restore_from_recycle_bin", [
        # NOT "recover the file called X": that canonicalizes to find_file's
        # 'find the file called NAME' form and steals real searches.
        "restore the budget file", "put back the notes file", "undelete report.docx",
        "bring back the screenshot i deleted", "restore that file from the recycle bin",
        "put back the plan document", "undelete the invoice", "restore it from the trash"],
        slots=slots_restore, speak=say_restore),
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
        # "wake ME up at 7" is an alarm for the USER, not a greeting for him
        slots=lambda t: None if re.search(r"\bwake\s+(?:me|us)\b", t) else {},
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
        "goodnight", "good night", "rest now jarvis", "you can switch off",
        # a thank-you wrapped around a dismissal is still a dismissal
        "that's it thanks", "thanks that's all", "okay thanks that's all",
        "thanks that is all", "alright thanks that's everything",
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
        "what do you remember about my project", "what's my wifi password",
        # phrasings that were missing the reflex and paying an 11-second LLM round
        "what do you remember about my desk lamp", "what did i tell you about my desk",
        "do you remember what i said about the garage", "what do you remember about that",
        "what have i told you about my routine"],
        slots=lambda t: {"query": t}, speak=say_recall),
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
