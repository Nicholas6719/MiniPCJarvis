"""Round three of "make him perfect": what the LIVE battery showed.

Fifty things no suite had asked were put to the installed build on 2026-09-17
and the audit log said what each really did: "call mom" texted HIS OWN phone
and said "Message sent"; "email my professor" drafted to an address it made
up; "save that as a document" searched for a file; "take a note" remembered a
fact and said "Note saved"; "focus on tonight" went looking for a window;
"how far apart are they" measured the distance to his car; Tokyo's time took
27 seconds and was wrong; "any emergencies near me" read out politics.

Run: python tests/test_perfect3.py
"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p3.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain import skills as K
    import orchestrator as O

    print("\n-- the world clock is arithmetic, not a web search --")
    s = K.slots_time_in("what time is it in tokyo")
    check("Tokyo is a time zone", s == {"place": "tokyo", "tz": "Asia/Tokyo"}, s)
    said = K.say_time_in(s, {})
    there = dt.datetime.now(ZoneInfo("Asia/Tokyo"))
    want = f"{there.hour % 12 or 12}:{there.minute:02d} {'AM' if there.hour < 12 else 'PM'}"
    check("...and the answer is the time there", want in said and "Tokyo" in said, (said, want))
    check("...with the day when it is not today's", ("tomorrow" in said) == (there.date() > dt.date.today()), said)
    check("'what time is it in the kitchen' is not a place", K.slots_time_in("what time is it in the kitchen") is None)
    check("a bare 'what time is it' is not this skill", K.slots_time_in("what time is it") is None)
    for city, tz in K._CITY_TZ.items():
        try:
            ZoneInfo(tz)
        except Exception as e:
            check(f"{city} has a real zone", False, e)

    print("\n-- a note is a file --")
    check("named, with its body", K.slots_note_take("take a note called groceries: milk and eggs")
          == {"name": "groceries", "content": "Milk and eggs"}, K.slots_note_take("take a note called groceries: milk and eggs"))
    n = K.slots_note_take("take a note: the midterm covers chapters one through five")
    check("unnamed gets today's date", n and n["name"].startswith("Note 20") and n["content"].startswith("The midterm"), n)
    check("'take a note' alone asks what it should say", K.slots_note_take("take a note") is None)
    check("'remember that...' is still memory, not a file", K.slots_note_take("remember that i like my coffee black") is None)

    print("\n-- 'focus on tonight' is not a window --")
    check("a question about what to focus on", K.slots_switch("what do you think i should focus on tonight") is None)
    check("'focus on my homework'", K.slots_switch("focus on my homework") is None)
    check("...but 'focus on spotify' still is", (K.slots_switch("focus on spotify") or {}).get("title") == "spotify")

    print("\n-- 'save that as a document' saves what he just said --")
    for said, kind, name in (("save that as a word document called JARVIS probe outline", "document", "JARVIS probe outline"),
                             ("save that as a document", "document", None),
                             ("put that into a note called study plan", "note", "study plan"),
                             ("can you save this as a doc named essay outline", "doc", "essay outline")):
        m = O.SAVE_THAT_RE.match(said)
        check(f"{said!r}", bool(m) and m.group("kind").lower() == kind and (m.group("name") or None) == name,
              m.groupdict() if m else None)
    for said in ("save the file", "find the document called outline", "save that for later", "open a document"):
        check(f"{said!r} is not this", not O.SAVE_THAT_RE.match(said), said)
    src = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    conv = src[src.index("async def _converse"):]
    check("it runs before the router, with his LAST ANSWER as the content",
          conv.index("SAVE_THAT_RE.match") < conv.index("reflex = await brain.decide(")
          and '"content": prev' in conv[conv.index("SAVE_THAT_RE.match"):][:1500])

    print("\n-- an order for a thing must start with a tool --")
    for said in ("create me a 3d rendering of the arc reactor", "make a spreadsheet of my classes",
                 "write a document about photosynthesis", "generate an image of a castle", "set a reminder for 5 pm"):
        check(f"{said!r}", bool(O.ARTEFACT_ORDER.search(said)), said)
    for said in ("make me laugh", "write me a poem about autumn", "what makes a good essay", "create a plan for my week"):
        check(f"{said!r} is not an artefact order (or is creative)",
              not O.ARTEFACT_ORDER.search(said) or bool(O.CREATIVE_INTENT.search(said)), said)
    check("it feeds must_use_tool", "ARTEFACT_ORDER.search(raw_user" in src)

    print("\n-- a pronoun points at the conversation, not at his car --")
    check("'how far apart are they'", bool(O.PRONOUN_FOLLOW.search("how far apart are they")))
    check("the hint carries the last exchange and forbids the detour",
          "resolve them from" in src and "not from his memories or his location" in src)

    print("\n-- what he cannot do, he says --")
    prompt = (ROOT / "llm" / "prompts.py").read_text(encoding="utf-8")
    for must in ("place phone calls", "never invent an address", "send_to_phone reaches HIS phone only",
                 "control lights", "offer the nearest thing you really can"):
        check(f"the prompt says: {must}", must in prompt)

    print("\n-- 'any emergencies near me' uses HIS rules --")
    from tools import news_tools as N
    import briefing as br
    import significance as S
    S.local_only = lambda: True
    S.emergencies_only = lambda: True

    async def fake():
        return [{"headline": "Trump ordered DOJ not to appeal ruling loosening gun limits, sources say", "age_minutes": 4},
                {"headline": "Gas leak forces evacuation of homes on Main Street in Natick", "age_minutes": 9, "_local_feed": True},
                {"headline": "New gelato cafe opens in Sudbury", "age_minutes": 30, "_local_feed": True}]
    br.briefing._fresh_stories = fake
    res = asyncio.run(N.local_emergencies())
    check("politics is not an emergency near him; a gas leak in Natick is",
          [e["text"] for e in res.get("emergencies", [])] == ["Gas leak forces evacuation of homes on Main Street in Natick."], res)
    check("a quiet day is said plainly", K.say_emergencies({}, {"emergencies": []}) == "Nothing near you right now, sir.")
    N.register_all()
    from tools.registry import registry
    check("the tool is registered", registry.get("local_emergencies") is not None)

    print("\n-- small things he would have heard --")
    from tools import health as H
    out = asyncio.run(H.get_health("heart_rate"))
    check("no underscores aloud", "heart_rate" not in str(out.get("error", "")) and "heart rate" in str(out.get("error", "")), out)
    check("a timer is confirmed as a timer",
          K.say_reminder({"text": "your timer is up", "minutes_from_now": 10}, {"due": "x"}) == "Timer set for 10 minutes.")
    check("...and cancelled as one", K.say_unremind({"query": "timer"}, {"cancelled": 1, "texts": ["your timer is up"]}) == "Timer cancelled, sir.")

    print("\n-- the routes --")
    from brain.router import brain

    async def route():
        await brain.load()
        return {t: ((await brain.decide(t, context={})) or (None,))[0] for t in WANT}
    WANT = {"what time is it in tokyo": "time_in", "what time is it": "time", "any emergencies near me": "emergencies",
            "what's the news": "news", "take a note: the midterm covers chapters one through five": "note_take",
            "remember that i like my coffee black": "remember", "focus on spotify": "switch",
            "what do you think i should focus on tonight": None}
    got = asyncio.run(route())
    for t, want in WANT.items():
        name = getattr(got[t], "name", None)
        check(f"{t!r} -> {want or 'the model'}", name == want, name)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
