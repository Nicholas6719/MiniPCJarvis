"""Arithmetic is a reflex. "What's 17 times 23" took the model seventeen
seconds; he said "that should be instant". Offline, no model.

Run: python tests/test_math.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "math.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain import mathskill as M

    print("\n-- the sums he actually says --")
    for said, want in (
            ("what's 17 times 23", 391), ("17 times 23", 391), ("what is 17 x 23", 391),
            ("seventeen times twenty three", 391), ("what's 12 plus 5", 17),
            ("what's 100 divided by 7", 100 / 7), ("100 minus 37", 63),
            ("what's 20 percent of 150", 30), ("what is 15% of 80", 12),
            ("what's the square root of 81", 9), ("square root of 2", 2 ** 0.5),
            ("what's 2 to the power of 10", 1024), ("2 to the 10th", 1024),
            ("what's 9 squared", 81), ("3 cubed", 27), ("half of 30", 15),
            ("double 21", 42), ("what's 3 point 5 times 2", 7),
            ("what's two hundred and five plus ten", 215),
            ("one thousand two hundred divided by four", 300),
            ("what's 2 plus 3 times 4", 14), ("what's 10 minus 2 minus 3", 5),
            ("what's 2 to the power of 3 to the power of 2", 512),
            ("hey jarvis, what's 45 plus 55?", 100), ("calculate 7 times 8", 56),
            ("how much is 14 times 12", 168), ("what does 6 times 7 make", 42),
            ("what's 17 mod 5", 2), ("1.5 plus 2.25", 3.75)):
        got = M.parse(said)
        ok = got is not None and "value" in got and abs(got["value"] - want) < 1e-6
        check(f"{said!r} -> {want}", ok, got)

    print("\n-- what it says --")
    check("17 times 23", M.parse("what's 17 times 23")["said"] == "17 times 23 is 391.",
          M.parse("what's 17 times 23")["said"])
    check("division rounds", M.parse("100 divided by 7")["said"] == "100 divided by 7 is 14.2857.",
          M.parse("100 divided by 7")["said"])
    check("big numbers get commas", M.parse("1000 times 1000")["said"].endswith("is 1,000,000."),
          M.parse("1000 times 1000")["said"])
    check("square root reads naturally",
          M.parse("square root of 81")["said"] == "the square root of 81 is 9.",
          M.parse("square root of 81")["said"])
    d = M.parse("what's 5 divided by 0")
    check("division by zero is a sentence, not a crash",
          d is not None and "zero" in d["said"], d)

    print("\n-- and what is NOT a sum --")
    for said in ("set the volume to 50 percent", "remind me in 5 minutes", "what time is it",
                 "open spotify", "how many legs does a spider have", "what's the weather",
                 "turn it up by 10", "give me 5 to 8", "what happened in 1969",
                 "how far is the moon", "what's 5", "count to 10", "play track 3",
                 "what's the time in 2 hours", "call me at 5", "wake me at 7 30",
                 "is 17 a prime number", "two plus two is four right"):
        got = M.parse(said)
        check(f"{said!r} is not a sum", got is None, got)

    print("\n-- unit conversions are arithmetic with a table --")
    # "how many milliliters in a US cup" was answered with "Did you mean render
    # that in 3D, sir?" on 2026-09-05. A conversion is the math reflex's job.
    for said, want in (("how many milliliters in a US cup", "1 cup is about 236.6 milliliters."),
                       ("How many milliliters are in a cup?", "1 cup is about 236.6 milliliters."),
                       ("convert 5 miles to kilometers", "5 miles is about 8.05 kilometers."),
                       ("what's 30 celsius in fahrenheit", "30 degrees celsius is 86 degrees fahrenheit."),
                       ("what's 70 fahrenheit in celsius", "70 degrees fahrenheit is 21.1 degrees celsius."),
                       ("how many feet in a mile", "1 mile is 5,280 feet."),
                       ("how many ounces in a pound", "1 pound is 16 ounces."),
                       ("how many cups in 2 quarts", "2 quarts is 8 cups."),
                       ("10 kilometers in miles", "10 kilometers is about 6.21 miles."),
                       ("how many gigs in a terabyte", "1 terabyte is 1,000 gigabytes."),
                       ("convert 3 pounds to liters",
                        "I'm afraid pounds and liters measure different things, sir.")):
        got = M.parse(said)
        check(f"{said!r}", bool(got) and got.get("said") == want, got and got.get("said"))
    check("a unit the table does not know is left alone", M.parse("how many furlongs in a mile") is None)

    print("\n-- nothing he says can run as code --")
    for said in ("what's __import__('os').system('dir')", "1 + 1; import os", "2 ** 100000000",
                 "what's 9 to the power of 9 to the power of 9"):
        got = M.parse(said)
        check(f"{said[:40]!r} is refused or harmless", got is None or "value" in got and
              abs(got["value"]) < 1e18, got)

    print("\n-- and the brain routes it --")

    async def routed():
        from brain.router import brain
        await brain.load()
        out = {}
        for said in ("what's 17 times 23", "what's 20 percent of 150", "square root of 81",
                     "what time is it", "set the volume to 50 percent"):
            d = await brain.decide(said)
            out[said] = d[0].name if d else None
        return out
    r = asyncio.run(routed())
    for said in ("what's 17 times 23", "what's 20 percent of 150", "square root of 81"):
        check(f"{said!r} -> math", r[said] == "math", r[said])
    check("time still goes to time", r["what time is it"] == "time", r["what time is it"])
    check("volume still goes to volume", r["set the volume to 50 percent"] in ("volume", "volume_set"),
          r["set the volume to 50 percent"])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
