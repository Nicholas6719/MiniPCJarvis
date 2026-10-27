"""A test's turn must never enter his history.

Every live suite drives POST /text, which runs a full turn and filed it
exactly as if he had typed it: transcript rows, a turn_stats row, and from
those his day memory and "how did I do" numbers. Hundreds of exchanges he
never had were in his transcript ("what time is it" eighty times a night).

Now /text takes `record: false`, the flag rides a contextvar into the spawned
turn, and MemoryStore skips the writes under it. The test mute
(/debug/silence) counts as "not recorded" on its own, so a suite that forgot
the field still leaves nothing behind.

Offline: no app, no audio, no network. The database is a throwaway (JARVIS_DB)
shared with the other gates, so every check counts what IT added.
Run: python tests/test_recording.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "recording.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from memory.store import memory

    def counts() -> tuple[int, int]:
        t = memory.db.execute("SELECT COUNT(*) FROM transcript").fetchone()[0]
        s = memory.db.execute("SELECT COUNT(*) FROM turn_stats").fetchone()[0]
        return int(t), int(s)

    def one_turn(tag: str) -> None:
        memory.log_turn("user", f"recording gate {tag}")
        memory.log_turn("assistant", f"noted, {tag}")
        memory.log_turn_stat("reflex", "time", 120, first_ms=90)

    # The gates share one database. This one judges what it added, and takes
    # it back out at the end: test_faster reads the median first_ms of every
    # reflex row, and the rows planted here would move it.
    high = {t: int(memory.db.execute(f"SELECT COALESCE(MAX(rowid), 0) FROM {t}").fetchone()[0])
            for t in ("transcript", "turn_stats")}
    try:
        return _checks(memory, counts, one_turn)
    finally:
        for t, rowid in high.items():
            memory.db.execute(f"DELETE FROM {t} WHERE rowid > ?", (rowid,))
        memory.db.commit()


def _checks(memory, counts, one_turn) -> int:
    import recording

    print("\n-- his own turn is filed, both sides and the numbers --")
    before = counts()
    one_turn("his")
    after = counts()
    check("a recorded turn adds two transcript rows",
          after[0] - before[0] == 2, (before, after))
    check("...and one turn_stats row", after[1] - before[1] == 1, (before, after))

    print("\n-- a turn marked record:false leaves no trace --")
    before = counts()
    tok = recording.set_recording(False)
    try:
        check("the flag reads back off", recording.recording() is False)
        one_turn("a test's")
    finally:
        recording.reset(tok)
    after = counts()
    check("transcript count unchanged", after == before, (before, after))
    check("...and the flag is back on afterwards", recording.recording() is True)
    # ...but the suite that asked can still read its own answer back
    scratch = memory.recent_transcript(10, scratch=True)
    check("the unrecorded turn is readable from the scratch ring",
          len(scratch) >= 2 and scratch[-1]["content"] == "noted, a test's"
          and scratch[-1]["id"] > 10 ** 9 and scratch[-2]["role"] == "user", scratch[-2:])
    check("...and the real transcript does not hold it",
          not any(r["content"] == "noted, a test's" for r in memory.recent_transcript(50)))

    print("\n-- the flag crosses into the spawned turn, the way /text uses it --")
    import events

    async def spawned_turn():
        seen = []
        before = counts()
        tok = recording.set_recording(False)
        try:
            async def turn():
                seen.append(recording.recording())
                one_turn("spawned under record:false")
            t = events.spawn(turn(), name="gate-turn")
        finally:
            recording.reset(tok)
        await t
        return seen, before, counts()
    seen, before, after = asyncio.run(spawned_turn())
    check("the task inherited 'not recorded'", seen == [False], seen)
    check("...and wrote nothing", after == before, (before, after))

    async def spawned_recorded():
        before = counts()
        async def turn():
            one_turn("spawned, recorded")
        await events.spawn(turn(), name="gate-turn-2")
        return before, counts()
    before, after = asyncio.run(spawned_recorded())
    check("a spawned turn without the flag is filed as usual",
          after[0] - before[0] == 2 and after[1] - before[1] == 1, (before, after))

    print("\n-- the test mute alone is enough --")
    from audio.io import speaker
    was = speaker.silent_until
    try:
        speaker.silent_until = time.time() + 60
        check("under /debug/silence nothing is recorded", recording.recording() is False)
        before = counts()
        one_turn("under the test mute")
        check("...and nothing was written", counts() == before, (before, counts()))
        speaker.silent_until = 0.0
        check("the mute over, recording resumes", recording.recording() is True)
    finally:
        speaker.silent_until = was

    print("\n-- /text carries the field into the turn --")
    import main as app_main
    seen = []

    class FakeOrchestrator:
        class sm:
            state = None
        async def wake_if_sleeping(self):
            pass
        async def try_voice_confirmation(self, text):
            return False
        async def run_text_turn(self, text):
            seen.append((text, recording.recording()))

    real_orc, real_auth = app_main.orchestrator, app_main._auth
    app_main.orchestrator = FakeOrchestrator()
    app_main._auth = lambda token: None

    async def drive():
        await app_main.text_input({"text": "what time is it", "record": False})
        await app_main.text_input({"text": "and now for real"})
        await app_main.text_input({"text": "typed by a script", "record": "false"})
        await asyncio.sleep(0.05)
    try:
        asyncio.run(drive())
    finally:
        app_main.orchestrator, app_main._auth = real_orc, real_auth
    check("record:false reaches the turn as 'not recorded'",
          ("what time is it", False) in seen, seen)
    check("no field means his, recorded", ("and now for real", True) in seen, seen)
    check("the string 'false' counts too", ("typed by a script", False) in seen, seen)
    check("the handler's own context is clean after each call", recording.recording() is True)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
