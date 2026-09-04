"""Protocols: the Iron Man idiom over the routines he already teaches.

"When I say lockdown protocol, lock the pc and mute" teaches a routine, as it
always did. What is new is that the routine answers to the way the films say
it — "initiate the lockdown protocol", "engage protocol lockdown", "lockdown
protocol, now" — that a protocol he never taught gets an offer to set one up
rather than a shrug, and that "what protocols do I have" lists them.

Offline; the brain runs on a temp DB re-seeded from SKILLS.
Run: python tests/test_protocols.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "protocols.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    from brain import protocols as P
    from brain.router import brain
    await brain.load()

    print("\n-- the name inside the phrasing --")
    for said, want in (
            ("initiate the lockdown protocol", "lockdown"),
            ("engage protocol lockdown", "lockdown"),
            ("run the house party protocol", "house party"),
            ("execute protocol clean slate", "clean slate"),
            ("lockdown protocol", "lockdown"),
            ("lockdown protocol, now", "lockdown"),
            ("jarvis, activate the good night protocol", "good night"),
            ("begin the morning protocol please", "morning"),
            ("what's the internet protocol for", None),
            ("open the protocol document", None),
            ("what protocols do i have", None),
            ("protocol", None)):
        got = P.protocol_name(said)
        check(f"{said!r} -> {want!r}", got == want, got)

    print("\n-- a taught protocol answers to every phrasing --")
    steps = [{"skill": "lock", "args": {}}, {"skill": "mute", "args": {}}]
    await brain.teach_command("lockdown protocol", steps)
    for said in ("lockdown protocol", "initiate the lockdown protocol",
                 "engage protocol lockdown", "run lockdown protocol now",
                 "jarvis initiate lockdown protocol"):
        got = await brain.match_command(said)
        check(f"{said!r} runs it", got == steps, got)
    check("a different protocol does not",
          await brain.match_command("initiate the party protocol") is None)
    check("...and plain sentences still do not",
          await brain.match_command("what time is it") is None)

    print("\n-- one he never taught gets an offer, not a shrug --")
    line = P.missing_line("party")
    check("names the protocol", "party protocol" in line.lower(), line)
    check("...and says how to make one", "when i say" in line.lower(), line)
    check("...as a question, not a lecture", len(line) < 200, len(line))

    print("\n-- and he can ask what he has --")
    await brain.teach_command("good night protocol", [{"skill": "mute", "args": {}}])
    names = P.taught(brain)
    check("both protocols are listed", names == ["good night", "lockdown"], names)
    spoken = P.list_line(names)
    check("...in one spoken line", "good night" in spoken and "lockdown" in spoken
          and "\n" not in spoken, spoken)
    check("no protocols is an offer too",
          "when i say" in P.list_line([]).lower(), P.list_line([]))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
