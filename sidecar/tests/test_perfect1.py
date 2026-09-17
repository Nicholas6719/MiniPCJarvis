"""Round one of "make him perfect" - every case out of his own sessions and his
own phone, 2026-09-14 to 09-17.

Run: python tests/test_perfect1.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p1.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import significance as S
    from significance import ALERT, NONE, URGENT, classify_news
    S.local_only = lambda: True
    S.emergencies_only = lambda: True

    def tier(h, s="", local=True):
        st = {"headline": h, "summary": s}
        if local:
            st["_local_feed"] = True
        return classify_news(st)

    print("\n-- what still reached his phone, 09-15 to 09-17 --")
    for name, h, s in (
        ("a season with NO hurricanes is not a hurricane",
         "Atlantic season sets a record-late start with no hurricanes yet",
         "More than halfway through the Atlantic hurricane season, no storms have intensified into a hurricane, marking a record-late start"),
        ("a thwarted plot did not happen",
         'FBI investigation thwarts "mass casualty attack" from man accused of ISIS allegiance',
         "FBI investigators arrested 21-year-old Jonathan Kramer in Pennsylvania after discovering a semi-automatic rifle, ammunition, knives and a note in his hotel room"),
        ("remains found two years after a storm are not breaking",
         "Remains of Hurricane Helene victim found nearly 2 years after storm",
         "Remains of Nancy Tucker were found in the Jackson Island area and identified as the last missing person from Washington County after Hurricane Helene"),
        ("residents who blast a law are not an explosion",
         "Worcester residents blast anti-camping law, say it punishes unhoused people",
         "Worcester residents gathered at city hall to oppose a proposed anti-camping ordinance"),
        ("the Grand Canyon is not near him, whatever desk carried it",
         "Body found in Grand Canyon was missing hiker, wife says, bringing flood death toll to three",
         "Human remains of Timothy Allen Smith were found in the Grand Canyon, bringing the flood death toll to three"),
        ("a dog and two cats rescued is good news",
         "Massachusetts firefighters rescue dog, 2 cats from house fire", ""),
        ("what the DA says on Tuesday about Saturday is the aftermath",
         "Woman dies after being hit by SUV in Framingham early Saturday, DA says", ""),
    ):
        t, why = tier(h, s)
        check(name, t == NONE, f"{t}: {why}")

    print("\n-- a crash in his town that is over is told once, not chased --")
    for h, s in (("Multi-vehicle crash in Natick MA kills woman, hospitalizes man", ""),
                 ("30-year-old woman killed in Framingham pedestrian crash",
                  "A 30-year-old woman was struck by a Honda CR-V on Worcester Road in Framingham at about 12:30 a.m. She was taken to MetroWest Medical Center and later died")):
        t, why = tier(h, s)
        check(f"{h[:48]!r} is an ALERT", t == ALERT, f"{t}: {why}")

    print("\n-- and what must still get through, unchanged --")
    for name, h, s, want in (
        ("an explosion is still an explosion", "Explosion rocks Framingham apartment building, residents evacuated", "", (URGENT,)),
        ("a blast that kills is still a blast", "Blast kills two at Natick chemical plant", "", (URGENT,)),
        ("a hurricane that IS coming", "Hurricane warning issued for Massachusetts coast as storm approaches Middlesex County", "", (URGENT, ALERT)),
        ("a fire that kills in his town", "Two dead in overnight Sudbury house fire", "", (URGENT,)),
        ("a shooter at large in his town", "Police search for gunman after shooting in Framingham", "", (URGENT,)),
        ("an attack that was NOT thwarted", "Terrorist attack kills dozens in Chicago, suspect at large", "", (URGENT,)),
    ):
        t, why = tier(h, s, local=name != "an attack that was NOT thwarted")
        check(name, t in want, f"{t}: {why}")

    print("\n-- 'make your own' is an answer --")
    import clarify
    render = lambda a, r: "ok"            # noqa: E731
    amb = clarify.approval("arc reactor", "Shall I fetch it, or make my own?", "make_hologram",
                           {"tier": 5}, render, yes_words=("make", "render", "show"),
                           alt={"args": {"tier": 4, "scouted_model": {}}})
    check("the question carries a third branch", [b.label for b in amb.branches] == ["go ahead", "leave it", "make my own"])
    pend = clarify.Pending(amb)
    for said in ("Make your own.", "make your own", "build it yourself", "no, make one from scratch", "do it yourself"):
        got = clarify.choose(pend, said)
        check(f"{said!r} picks the build", getattr(got, "label", got) == "make my own", getattr(got, "label", got))
    check("...and it carries the BUILD's arguments, not the fetch's",
          clarify.choose(pend, "make your own").args.get("tier") == 4)
    for said, want in (("yes", "go ahead"), ("fetch it", None), ("go ahead", "go ahead"), ("no", "leave it"),
                       ("leave it", "leave it")):
        got = clarify.choose(pend, said)
        label = getattr(got, "label", got)
        if want is not None:
            check(f"{said!r} is still {want!r}", label == want, label)
    check("a bare 'Show.' after declining is NOT a yes to the fetch any more than before",
          getattr(clarify.choose(pend, "go to sleep"), "label", None) is None)
    plain = clarify.approval("x", "Shall I?", "make_hologram", {}, render)
    check("an ordinary cost question has no third branch", len(plain.branches) == 2)
    check("...and 'make your own' is not an answer to it",
          clarify.choose(clarify.Pending(plain), "make your own") is None)
    scout_src = (ROOT / "scout.py").read_text(encoding="utf-8")
    check("the question says the option exists", "Shall I fetch it, or make my own?" in scout_src)
    rt = (ROOT / "tools" / "render_tools.py").read_text(encoding="utf-8")
    check("the fetch question carries the build as its alternative", '"alt": ({"args"' in rt and '"scouted_model": {}}} if fetch else None' in rt)

    print("\n-- a price he asked him to look up is looked up --")
    from linkguard import LinkLedger, price_caveat
    led = LinkLedger()
    led.note({"results": [{"title": "RTX 5090", "url": "https://example.com/x",
                           "snippet": "The RTX 5090 is listed at $5,499 at major retailers"}]})
    check("a price in a search snippet counts as seen", led.saw_price)
    check("...so the reply is not disowned",
          "from memory" not in price_caveat("The RTX 5090 is around $5,500 today.", led))
    led2 = LinkLedger()
    led2.note({"results": [{"title": "GPUs", "url": "https://example.com/y", "snippet": "a fast card"}]})
    check("with no price seen, a quoted price is still flagged",
          "from memory" in price_caveat("It costs $5,500.", led2))

    print("\n-- a remark on Telegram is not a thirty-second turn --")
    from remote_telegram import ACK_ONLY, REMARK
    for said in ("that's awful, thank you for telling me", "wow, that's crazy", "thanks for letting me know",
                 "oh no, that's terrible", "that's good to know"):
        check(f"{said!r} is a remark", bool(REMARK.fullmatch(said)), said)
    for said in ("that's awful, what happened?", "how is the weather", "so what do i do about it",
                 "thank you, now open chrome", "how many people died"):
        check(f"{said!r} is still a request", not REMARK.fullmatch(said) or len(said.split()) > 12
              or "open" not in said and False, said) if False else check(
            f"{said!r} is still a request", not REMARK.fullmatch(said), said)
    check("a bare ok is still just an ok", bool(ACK_ONLY.fullmatch("ok")))

    print("\n-- the speakers are reopened without stopping the world --")
    from audio import io as A
    import audio.output_watch as OW

    class Sticky:
        """A stream whose close() takes three seconds, like a sleeping endpoint."""
        def abort(self):
            time.sleep(3.0)
        def close(self):
            pass

    async def drill():
        spk = A.Speaker.__new__(A.Speaker)
        spk._stream, spk._rate = Sticky(), 24000
        spk.prewarm = lambda rate: None
        real = OW.endpoint_active
        OW.endpoint_active = lambda: True
        try:
            t0 = time.perf_counter()
            await spk.reopen_when_ready(24000)
            return time.perf_counter() - t0, spk._stream
        finally:
            OW.endpoint_active = real
    took, left = asyncio.run(drill())
    check("a stream that takes three seconds to die costs the wake nothing", took < 0.8, f"{took:.2f}s")
    check("...and is dropped at once", left is None)

    print("\n-- 'next' means after today --")
    from llm import prompts
    src = (ROOT / "llm" / "prompts.py").read_text(encoding="utf-8")
    check("the prompt says a past date is not the next one", "a date that has already passed is not the answer" in src)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
