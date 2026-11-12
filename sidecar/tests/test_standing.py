"""Event watches: "tell me when the download / HandBrake / the build finishes".

A request that outlives the sentence which created it. The properties that
matter, each gated here:

  * a process watch arms only once it has SEEN the program running, so a watch
    set before the build starts does not fire at once ("finished" is true of a
    thing that never began, and useless);
  * a watch fires ONCE and is gone before the sentence is even returned, so a
    slow announcement cannot report it twice;
  * JARVIS never matches himself, the model server or the shell;
  * a watch older than a week is dropped;
  * a fired watch reaches delivery directly, NOT through proactive._fire — a
    requested report is not an unsolicited remark, and quiet hours must not
    turn "tell me when the build is done" into silence.

Offline, hermetic: a fake psutil, a temp folder, the gate database.
Run: python tests/test_standing.py
"""
import asyncio
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "standing.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class _Proc:
    def __init__(self, name):
        self.info = {"name": name}


class _Batt:
    def __init__(self, percent, plugged=True):
        self.percent, self.power_plugged = percent, plugged


def _fake_psutil(names, battery=None):
    m = types.ModuleType("psutil")
    m.process_iter = lambda attrs=None: [_Proc(n) for n in names]
    m.sensors_battery = lambda: battery
    return m


def main() -> int:
    import standing as S
    from tools import watch_tools as W
    # imported BEFORE psutil is faked, so their module-level `import psutil`
    # binds the real one and the fake only reaches the late imports in standing
    import awareness as A
    import delivery as D
    from proactive import proactive as P

    real_psutil = sys.modules.get("psutil")
    for w in S.all_watches():                       # judge only what THIS gate adds
        S._delete([w["id"]])
    mine: list[int] = []

    def ids():
        return [w["id"] for w in S.all_watches() if w["id"] in mine]

    try:
        print("\n-- JARVIS never matches himself --")
        sys.modules["psutil"] = _fake_psutil(["jarvis-sidecar.exe", "llama-server.exe", "JARVIS.exe",
                                              "HandBrake.exe", "explorer.exe"])
        running = S.running_processes()
        check("the sidecar, the model server and the shell are invisible",
              not any(n in running for n in ("jarvis-sidecar.exe", "llama-server.exe", "jarvis.exe", "jarvis-sidecar", "llama-server")),
              sorted(running))
        check("...but a real program is seen, case-insensitively, by stem",
              S.process_running("handbrake", running) and S.process_running("HandBrake.exe", running))
        check("...and by substring", S.process_running("brake", running))
        check("'server' does not arm on llama-server", not S.process_running("server", running))
        r = W.watch_for_process("jarvis")
        check("a watch on JARVIS himself is refused", "error" in r, r)

        print("\n-- a process watch arms only once it has seen the program --")
        sys.modules["psutil"] = _fake_psutil(["explorer.exe"])
        r = W.watch_for_process("HandBrake")
        mine.append(r["id"])
        check("set before it starts: the confirmation says so",
              r.get("running") is False and "don't see" in r["watching"], r)
        check("...and it is not armed", not S.all_watches()[-1]["armed"])
        check("nothing running, nothing fired", S.check([], processes=set()) == [])
        check("...and the watch is still there", ids() == [r["id"]])
        S.check([], processes={"handbrake.exe", "handbrake"})
        check("seen running: now armed", S.all_watches()[-1]["armed"])
        check("...and seeing it run is not 'finished'", ids() == [r["id"]])
        fired = S.check([], processes=set())
        check("it exits: fired, as a sentence and a key",
              len(fired) == 1 and "handbrake has finished" in fired[0][0].lower()
              and fired[0][1] == f"watch:{r['id']}", fired)
        check("...and the row was gone BEFORE check returned", ids() == [])
        check("...so a second look fires nothing", S.check([], processes=set()) == [])

        sys.modules["psutil"] = _fake_psutil(["blender.exe"])
        r = W.watch_for_process("blender")
        mine.append(r["id"])
        check("set while it runs: armed at once and the confirmation is plain",
              r.get("running") is True and r["watching"].startswith("I'll tell you"), r)
        S.cancel("blender")

        print("\n-- downloads --")
        r = W.watch_for_download("")
        mine.append(r["id"])
        check("no downloads yet: nothing", S.check([], processes=set()) == [])
        fired = S.check(["ubuntu-24.04.iso"], processes=set())
        check("the next download fires it", len(fired) == 1 and "ubuntu-24.04.iso has finished downloading" in fired[0][0], fired)
        check("...once", S.check(["ubuntu-24.04.iso"], processes=set()) == [])
        r = W.watch_for_download("report")
        mine.append(r["id"])
        check("a named download ignores other files", S.check(["cat.jpg"], processes=set()) == [])
        fired = S.check(["Q3-Report.pdf"], processes=set())
        check("...and fires on a case-insensitive substring", len(fired) == 1 and "Q3-Report.pdf" in fired[0][0], fired)

        print("\n-- a file appearing in a folder --")
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "old.stl"), "w") as f:
            f.write("x")
        old_mtime = time.time() - 3600
        os.utime(os.path.join(folder, "old.stl"), (old_mtime, old_mtime))
        time.sleep(1.1)          # the clock has to move past the file's creation
        r = W.watch_for_file("stl", folder)
        mine.append(r["id"])
        check("a file that was already there is not 'appeared'", S.check([], processes=set()) == [])
        with open(os.path.join(folder, "new-part.stl"), "w") as f:
            f.write("y")
        fired = S.check([], processes=set())
        check("a new one is", len(fired) == 1 and "new-part.stl has appeared" in fired[0][0], fired)
        r = W.watch_for_file("", "nowhere-" + folder)
        check("an unknown folder is refused", "error" in r, r)
        r = W.watch_for_file("", folder)
        mine.append(r["id"])
        check("no pattern means any new file", "a new file" in r["watching"], r)
        S.cancel("a new file")

        print("\n-- the battery --")
        sys.modules["psutil"] = _fake_psutil([], battery=_Batt(40))
        r = W.watch_for_battery(80)
        mine.append(r["id"])
        check("at 40, an 80 watch waits", S.check([], battery=_Batt(40), processes=set()) == [])
        fired = S.check([], battery=_Batt(81), processes=set())
        check("at 81 it fires", len(fired) == 1 and "81 percent" in fired[0][0], fired)
        r = W.watch_for_battery(100)
        mine.append(r["id"])
        fired = S.check([], battery=_Batt(100), processes=set())
        check("'charged' is said as charged", len(fired) == 1 and "fully charged" in fired[0][0], fired)
        sys.modules["psutil"] = _fake_psutil([], battery=None)
        r = W.watch_for_battery(80)
        check("no battery: refused, not promised", "error" in r, r)

        print("\n-- listing and cancelling --")
        sys.modules["psutil"] = _fake_psutil(["explorer.exe"])
        a = W.watch_for_process("handbrake"); mine.append(a["id"])
        b = W.watch_for_download("iso"); mine.append(b["id"])
        spoken = W.list_watches()["spoken"]
        check("the list is one sentence in English",
              "handbrake finishes" in spoken and "iso finishes downloading" in spoken and "not started yet" in spoken, spoken)
        r = W.cancel_watch("handbrake")
        check("cancelling by name drops one", r["removed"] == 1 and "handbrake" in r["dropped"][0], r)
        r = W.cancel_watch("")
        check("cancelling with nothing named drops the rest", r["removed"] >= 1 and S.all_watches() == [], r)
        check("an empty list says so", "not watching for anything" in W.list_watches()["spoken"])

        print("\n-- a week is long enough --")
        w = S.add("download", "stale", description="when stale finishes downloading")
        mine.append(w["id"])
        S._conn().execute("UPDATE standing_watches SET created_ts=? WHERE id=?",
                          (time.time() - 8 * 86400, w["id"]))
        S._conn().commit()
        S.expire()
        check("a watch older than a week is dropped", w["id"] not in [x["id"] for x in S.all_watches()])

        print("\n-- a fired watch reaches delivery, not proactive's suppression --")
        delivered = []

        async def fake_deliver(text, tier="notable", **kw):
            delivered.append((text, tier, kw.get("key")))
            return {"delivered": "spoken", "tier": tier}

        real_deliver, real_quiet = D.delivery.deliver, P.in_quiet_hours
        D.delivery.deliver = fake_deliver
        P.in_quiet_hours = lambda now=None: True          # the middle of the night
        try:
            r = W.watch_for_download("movie"); mine.append(r["id"])
            A.awareness.arrived = ["movie.mkv"]
            asyncio.run(P.run_standing())
            check("inside quiet hours the report he asked for still goes out",
                  len(delivered) == 1 and "movie.mkv" in delivered[0][0], delivered)
            check("...at ALERT, keyed by the watch", delivered[0][1] == D.ALERT and delivered[0][2] == f"watch:{r['id']}", delivered)
            check("...and the arrivals list was consumed", A.awareness.arrived == [], A.awareness.arrived)
            check("...and the two-an-hour cap was not charged", P._hour_window == [] or len(P._hour_window) == 0)
        finally:
            D.delivery.deliver, P.in_quiet_hours = real_deliver, real_quiet
    finally:
        if real_psutil is not None:
            sys.modules["psutil"] = real_psutil
        else:
            sys.modules.pop("psutil", None)
        S.cancel("")

    print("\n-- a news story he asked to be kept posted on (2026-11-12) --")
    # "please let me know if you hear more about that" (2026-09-30) was
    # answered "I'll keep an eye on it" - and nothing was watching.
    import tools.watch_tools as WT
    from lastseen import last_seen
    S.cancel("")
    saved_links, saved_at = list(last_seen.links), last_seen.at
    try:
        first = "Framingham house fire displaces 3, sends 1 to hospital"
        last_seen.links, last_seen.at = [{"title": first, "source": "MetroWest Daily News"}], time.time()
        res = WT.watch_for_story("")
        check("'more about that' follows the story just discussed, and says which",
              "watching" in res and "Framingham house fire" in res["watching"], res)
        check("...as a standing watch he can ask about", "more news on" in WT.list_watches()["spoken"],
              WT.list_watches()["spoken"])
        check("the same article again is not 'more'",
              S.story_hits([{"headline": first.upper(), "source": "Patch", "age_minutes": 5}]) == [])
        check("...nor is an unrelated story", S.story_hits([
            {"headline": "Natick approves a new library", "source": "Patch", "age_minutes": 5}]) == [])
        check("...nor one published before he asked", S.story_hits([
            {"headline": "Cause of Framingham house fire under investigation", "source": "WCVB",
             "age_minutes": 600}]) == [])
        hits = S.story_hits([{"headline": "Natick approves a new library", "source": "Patch", "age_minutes": 5},
                             {"headline": "Cause of Framingham house fire under investigation", "source": "WCVB",
                              "age_minutes": 5}])
        check("a new headline about the same thing is told to him, with its source",
              len(hits) == 1 and hits[0][0] == "More on the story you asked me to follow, sir: Cause of "
              "Framingham house fire under investigation (WCVB)." and hits[0][1].startswith("watch:"), hits)
        check("...once: the watch is gone", S.story_hits([
            {"headline": "Framingham house fire victims identified", "source": "WCVB", "age_minutes": 1}]) == []
            and not [w for w in S.all_watches() if w["kind"] == "story"])
        res = WT.watch_for_story("the Nvidia chief executive leaving")
        check("he can name the story instead", "watching" in res and "Nvidia" in res["watching"], res)
        check("...and it matches on what it is about", len(S.story_hits([
            {"headline": "Nvidia names new chief executive after Huang exit", "source": "CNBC", "age_minutes": 2}])) == 1)
        last_seen.links, last_seen.at = [], 0.0
        check("with no story in mind he is asked which, not promised anything",
              "error" in WT.watch_for_story("") and not [w for w in S.all_watches() if w["kind"] == "story"])
        from brain.skills import slots_watch_story
        check("'that' is the story just discussed; a named subject is passed on",
              slots_watch_story("please let me know if you hear more about that") == {"about": ""}
              and slots_watch_story("let me know if there's more news about the framingham fire")
              == {"about": "the framingham fire"})
        import inspect
        import briefing
        check("the news scan checks the story watches every time it reads the feeds",
              "standing.story_hits" in inspect.getsource(briefing.Briefing.scan))
    finally:
        last_seen.links, last_seen.at = saved_links, saved_at
        S.cancel("")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
