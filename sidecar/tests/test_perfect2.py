"""Round two of "make him perfect": the brain, on what had never been tested.

130 everyday requests were routed offline on 2026-09-17. This gate holds the
ones that were wrong, the ones that were dangerous, and the manners.

Run: python tests/test_perfect2.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p2.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain import skills as K

    print("\n-- a named reminder is never 'all of them' --")
    check("'cancel my stretch reminder' names it", K.slots_unremind("cancel my stretch reminder") == {"query": "stretch"},
          K.slots_unremind("cancel my stretch reminder"))
    check("'cancel the reminder about laundry' names it",
          K.slots_unremind("cancel the reminder about laundry") == {"query": "laundry"})
    check("'delete the homework reminder' names it", K.slots_unremind("delete the homework reminder") == {"query": "homework"})
    check("'cancel my reminders' is still all of them", K.slots_unremind("cancel my reminders") == {"query": ""})

    print("\n-- timers and alarms are reminders by another name --")
    check("a timer", K.slots_reminder_any("set a timer for ten minutes") == {"minutes_from_now": 10, "text": "your timer is up"},
          K.slots_reminder_any("set a timer for ten minutes"))
    check("a timer in hours", K.slots_reminder_any("set a timer for 2 hours")["minutes_from_now"] == 120)
    check("a timer in seconds rounds up to a minute", K.slots_reminder_any("start a timer for 30 seconds")["minutes_from_now"] == 1)
    a = K.slots_reminder_any("set an alarm for 7 am")
    check("an alarm", a and a.get("at_time") == "07:00" and a.get("text") == "wake up", a)
    w = K.slots_reminder_any("wake me up at six thirty tomorrow")
    check("'wake me up at six thirty tomorrow'", w and w.get("at_time") == "06:30" and w.get("date") and w.get("text") == "wake up", w)
    r = K.slots_reminder_any("remind me to call mom at 5 pm")
    check("an ordinary reminder is untouched", r and r.get("at_time") == "17:00" and r.get("text") == "call mom", r)

    print("\n-- places, files and windows --")
    check("'go to youtube' is a place", K.slots_site("go to youtube") == {"url": "youtube.com"}, K.slots_site("go to youtube"))
    check("'take me to reddit'", K.slots_site("take me to reddit") == {"url": "reddit.com"})
    check("a URL still works", K.slots_site("open youtube.com") == {"url": "youtube.com"})
    check("'go to the kitchen' is not a website", K.slots_site("go to the kitchen") is None)
    check("creating a document is not finding one",
          K.slots_find("create a word document with my notes on photosynthesis") is None)
    check("...but finding one still is", K.slots_find("find my resume") == {"query": "resume"}, K.slots_find("find my resume"))
    check("'full screen' is not a window", K.slots_switch("switch to full screen") is None)
    check("...but notepad is", (K.slots_switch("switch to notepad") or {}).get("title") == "notepad")
    check("the next class is NEXT, not today's list", K.slots_agenda("how long until my next class") == {"day": "next"})

    print("\n-- the near-miss question has manners --")
    import orchestrator as O
    for said in ("help me study for my biology test", "write me a one page essay outline on the causes of world war one",
                 "give me a study plan for this week", "summarize this document", "fix the grammar in this paragraph",
                 "explain the pythagorean theorem", "quiz me on the periodic table", "what should i work on first"):
        check(f"{said[:46]!r} is work for the model, never 'did you mean'", bool(O._THINKING_WORK.search(said)), said)
    for said in ("make me a duck", "rotate it", "open the thing", "show me the bracket"):
        check(f"{said!r} may still be asked about", not O._THINKING_WORK.search(said), said)
    check("a guess that shares no word with what he said is not a guess",
          not (O._content_words("learn my face") & O._content_words("help me study for my biology test")))
    check("...one that does, is", bool(O._content_words("make me a hologram of a duck") & O._content_words("make me a duck")))
    src = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    ask = src[src.index("async def _ask_if_unsure"):][:7000]
    check("both guards run before the question is asked",
          ask.index("_THINKING_WORK.search") < ask.index('line = f"Did you mean') and ask.index("_content_words(seed)") < ask.index('line = f"Did you mean'))

    print("\n-- 'say that again' says it again --")
    for said in ("say that again", "repeat that", "what did you just say", "sorry, come again", "I didn't catch that", "pardon?"):
        check(f"{said!r}", bool(O.REPEAT_RE.match(said)), said)
    for said in ("say hello to mom", "repeat after me", "what did you say about tesla yesterday", "again with the volume up"):
        check(f"{said!r} is not a repeat", not O.REPEAT_RE.match(said), said)
    conv = src[src.index("async def _converse"):]
    check("it is answered before the brain or the model is asked",
          conv.index("REPEAT_RE.match") < conv.index("reflex = await brain.decide("))

    print("\n-- and the routes themselves --")
    from brain.router import brain

    async def route():
        await brain.load()
        out = {}
        for text in WANT:
            d = await brain.decide(text, context={})
            out[text] = d[0].name if d else None
        return out
    WANT = {
        "create a word document with my notes on photosynthesis": None,
        "go to youtube": "open_site", "go back to full screen": "companion_off",
        "get out of my way": "companion_on", "help me with this essay": "companion_on",
        "make me a logo for my robotics club": "image_make",
        "set an alarm for 7 am": "reminder", "set a timer for ten minutes": "reminder",
        "wake me up at six thirty tomorrow": "reminder", "cancel my stretch reminder": "unremind",
        "how long until my next class": "agenda", "any emergencies near me": "breaking",
        "text me the summary": "to_phone", "help": "capabilities", "show me the bracket": "holo_show",
        "start a new project called robotics arm": "project_start",
        "search youtube for calculus derivatives": "video", "summarize the news for me": "news",
        "what does this cell say": "office_selection", "what are we working on": "project_status",
        "what am i working on": "office_what",
        # and nothing that worked before moved
        "pause the music": "media_pause", "switch to chrome": "switch", "go back to notepad": "switch",
        "find my resume": "find_file", "open notepad": "open_app", "open youtube.com": "open_site",
        "show me pictures of the eiffel tower": "images", "what reminders do i have": "reminders",
        "remind me to call mom at 5 pm": "reminder", "go to sleep": "sleep",
    }
    got = asyncio.run(route())
    for text, want in WANT.items():
        check(f"{text!r} -> {want or 'the model'}", got.get(text) == want, got.get(text))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
