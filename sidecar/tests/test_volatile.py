"""Facts with a shelf life, and the weather that now uses them.

Phase 1 of the Evolution. The single property this exists to protect: **a reading
is never handed back without its age, and a stale one is not handed back at all.**

That is not pedantry. The same class of mistake shipped this morning in the
camera: presence held him "present" for twelve seconds after he left, "can you
see me" borrowed that, and JARVIS said "I can see someone, sir" about an empty
frame. A location fix from four hours ago is the same lie with a different
subject, so `fresh()` returns None rather than a value a caller might use without
looking at the clock.

Also gated: weather prefers a fresh phone fix over the configured home, ignores a
stale one, and a failed write never leaves SQLite's write lock held.

Offline: no network, no camera. Run: python tests/test_volatile.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(), "volatile.db")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import volatile as V

    check("an unknown key is None, not an empty reading", V.get("nope") is None)

    V.put("location", {"lat": 42.28, "lon": -71.42, "label": "Framingham"}, source="telegram")
    got = V.get("location")
    check("a stored reading comes back", got and got["value"]["lat"] == 42.28, got)
    check("...carrying its age", got and got["age_s"] is not None and got["age_s"] < 5, got)
    check("...and where it came from", got and got["source"] == "telegram", got)

    V.put("location", {"lat": 1.0, "lon": 2.0, "label": "elsewhere"})
    check("a newer reading replaces the old one — only the latest is kept",
          V.get("location")["value"]["lat"] == 1.0)
    rows = V._conn().execute("SELECT COUNT(*) FROM volatile_facts WHERE key='location'").fetchone()[0]
    check("...without accumulating rows", rows == 1, rows)

    # --- the property that matters -------------------------------------------
    check("a fresh reading passes the freshness check",
          V.fresh("location", 120) is not None)
    V._conn().execute("UPDATE volatile_facts SET ts=? WHERE key='location'",
                      (time.time() - 5 * 3600,))
    V._conn().commit()
    check("a five-hour-old fix is NOT returned as current",
          V.fresh("location", 120) is None,
          "stale data handed back is the 'I can see someone' bug with a new subject")
    check("...though it is still readable WITH its age when asked plainly",
          (V.get("location") or {}).get("age_minutes", 0) > 250)

    # --- speaking an age like a person ---------------------------------------
    for mins, want in ((0.5, "just now"), (7, "7 minutes ago"), (75, "about an hour ago"),
                       (400, "about 7 hours ago"), (60 * 30, "yesterday")):
        said = V.spoken_age(mins)
        check(f"{mins} minutes is said as {want!r}", said == want, said)

    # --- failures are survivable ---------------------------------------------
    ok = V.put("weird", {"x": object()})       # not JSON-serialisable
    check("an unserialisable value fails cleanly rather than raising", ok is False)
    check("...and the connection still works afterwards",
          V.put("after", {"fine": True}) is True,
          "a held write lock here would kill every turn in the process")

    check("forget removes it", V.forget("after") and V.get("after") is None)

    # --- weather now prefers the phone --------------------------------------
    from tools import weather as W
    V.put("location", {"lat": 42.3, "lon": -71.1, "label": "Boston"})
    got = W._phone_location()
    check("weather uses a fresh phone fix", got and got[2] == "Boston", got)
    V._conn().execute("UPDATE volatile_facts SET ts=? WHERE key='location'",
                      (time.time() - 9 * 3600,))
    V._conn().commit()
    check("...and ignores a stale one, falling back to home",
          W._phone_location() is None)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
