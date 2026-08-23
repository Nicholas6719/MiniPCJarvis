"""JARVIS reflex skills: things JARVIS can do by itself, without the LLM.

Each skill has seed phrasings (the brain learns more from real use), a slot
extractor that pulls arguments out of the utterance, and a speak() template.
If the extractor can't find what it needs, the request falls through to the LLM.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Callable

# ---------- slot extractors ----------

_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20,
              "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
              "eighty": 80, "ninety": 90, "hundred": 100, "half": 50, "max": 100, "full": 100}


def _number(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", text)
    if m:
        return int(m.group(1))
    for w, n in _NUM_WORDS.items():
        if re.search(rf"\b{w}\b", text):
            return n
    return None


def slots_volume(t: str) -> dict | None:
    n = _number(t)
    return {"percent": max(0, min(100, n))} if n is not None else None


def slots_app(t: str) -> dict | None:
    m = re.search(r"\b(?:open|launch|start|run|fire up|bring up|put on|close|quit|exit|kill|shut)\s+(?:up\s+)?(?:the\s+|my\s+)?([a-z0-9 .+#-]{2,40}?)(?:\s+(?:for me|please|now|app|application))*[.!?]*$", t)
    if not m:
        return None
    name = m.group(1).strip()
    if not name or name in ("it", "that", "this"):
        return None
    if re.search(r"https?://|\b\w+\.(?:com|org|net|io|gov|edu|co|tv|ai)\b", name):
        return None  # a website, not an app -> the LLM routes it to open_url
    return {"name": name}


def slots_site(t: str) -> dict | None:
    m = re.search(r"(https?://\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|org|net|io|gov|edu|co|tv|ai|uk|ca)\b(?:/\S*)?)", t)
    return {"url": m.group(1)} if m else None


def say_site(slots: dict, res: dict) -> str:
    url = str(slots.get("url", "")).replace("https://", "").replace("http://", "").rstrip("/")
    return f"Opening {url}." if "error" not in res else f"I couldn't open {url}."


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
    r"^(?:nope|no no no|not that one|not that|that's not it|that's not what i meant|that is wrong|that's wrong|wrong|no)\b"
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
    return {"query": q} if len(q) >= 3 and q != t.strip() else None


_IMG_LEAD = re.compile(
    r"^(?:hey\s+)?(?:jarvis[,.]?\s+)?(?:can you\s+|could you\s+|please\s+)?"
    r"(?:show(?:\s+me)?|find(?:\s+me)?|pull\s+up|get(?:\s+me)?|display|bring\s+up)\s+"
    r"(?:a\s+|some\s+|me\s+)?(?:picture|pictures|photo|photos|image|images|pic|pics)\s+of\s+", re.I)


def slots_images(t: str) -> dict | None:
    q = _IMG_LEAD.sub("", t.strip(), count=1).strip(" .?!")
    return {"query": q} if q and q != t.strip() else None


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
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        out["at_time"] = f"{h:02d}:{mi:02d}"
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


_LABELS = {"weather": "check the weather", "watch": "watch the system", "unwatch": "stop watching", "switch": "switch windows", "folder": "open the folder", "find_file": "find the file", "volume_set": "set the volume", "screenshot": "take a screenshot", "open_app": "open the app",
           "close_app": "close the app", "open_site": "open the site", "images": "find pictures",
           "search": "search the web", "screen": "look at the screen", "reminder": "set a reminder",
           "remember": "remember it", "stats": "check the system", "windows": "list your windows",
           "media_pause": "play or pause", "media_next": "skip the track", "media_previous": "go back a track",
           "clipboard": "read the clipboard", "lock": "lock the computer", "time": "tell the time",
           "date": "tell the date"}


SKILLS: list[Skill] = [
    Skill("time", None, [
        "what time is it", "what's the time", "tell me the time", "do you have the time",
        "current time", "time check", "what time is it right now", "got the time",
        "what's the clock say", "time please", "whats the time now", "hey what time is it"],
        speak=say_time),
    Skill("date", None, [
        "what's the date", "what day is it", "what is today's date", "what day is it today",
        "what's today", "date please", "which day is it", "what day of the week is it",
        "what is the date today", "tell me the date", "what's the day today"],
        speak=say_date),
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
        "show me a pic of iron man", "bring up pictures of a lamborghini"],
        slots=slots_images, speak=say_images),
    Skill("screen", "analyze_screen", [
        "look at my screen", "what's on my screen", "take a look at my screen",
        "what do you see on my screen", "describe my screen", "what am i looking at",
        "can you see my screen", "what's on the screen right now", "look at this",
        "tell me what you see on screen", "what's wrong with this screen"],
        speak=say_screen),
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
        "open example.com and tell me what the page says", "take me to github.com",
        "open up netflix.com", "go to the website espn.com", "load bbc.com", "bring up espn.com"],
        slots=slots_site, speak=say_site, llm_after=True, direct_if=site_direct, speak_first=True),
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
