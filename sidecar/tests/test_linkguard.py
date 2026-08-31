"""No link reaches him that a tool did not return.

The reply below is the real one, from 2026-08-31 at 06:18. He had asked for
Amazon links for a 3D-printer build. JARVIS ran six searches, got sixty results,
ignored all of them and invented the ASINs - "B08XYZ1234" among them, which is a
placeholder wearing a product's clothes.

Prompting a model to "only use real URLs" is a request. This is a gate, so these
tests are the specification: a URL that no tool returned does not go out, and a
URL that a tool DID return is never damaged on the way.

Run: python tests/test_linkguard.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linkguard import (LinkLedger, check, explain, price_caveat,  # noqa: E402
                       supply, wanted_links)

fails = []


def check_(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# what the searches really returned that morning
REAL = {"results": [
    {"title": "Creality Ender 3 V2 3D Printer", "url": "https://www.amazon.com/dp/B08663TXWS"},
    {"title": "Official Creality Store", "url": "https://www.creality.com/products/ender-3-v2"},
    {"title": "Cura slicer", "url": "https://ultimaker.com/software/ultimaker-cura"},
]}

# what he was actually sent
INVENTED = (
    "Creality Ender 3 V2 – $200, FDM printer, good for beginners: "
    "https://www.amazon.com/creality-ender-3-v2/s?k=creality+ender+3+v2\n"
    "Ultimaker 2+ – $1,500, professional-grade: "
    "https://www.amazon.com/Ultimaker-2-Plus-Printer/dp/B07V4ZK5YB\n"
    "PLA filament (1kg) – $30: https://www.amazon.com/PLA-Filament-1kg/dp/B07Q8J9W7M\n"
    "Build-plate adhesive sheet – $10: "
    "https://www.amazon.com/Adhesive-Sheet-3D-Printer/dp/B08XYZ1234\n"
    "Cura slicer software – free download: https://ultimaker.com/software/ultimaker-cura, sir."
)


def main() -> int:
    led = LinkLedger()
    led.note(REAL)
    check_("the ledger recorded what the tools returned", led.count == 3, led.count)

    cleaned, removed = check(INVENTED, led)

    # --- the four invented ones must not survive -----------------------------
    for fake in ("B08XYZ1234", "B07V4ZK5YB", "B07Q8J9W7M", "s?k=creality"):
        check_(f"{fake!r} never reaches him", fake not in cleaned, cleaned[:120])
    check_("all four were caught", len(removed) == 4, removed)

    # --- the one real link is untouched --------------------------------------
    check_("a link a tool DID return survives",
           "https://ultimaker.com/software/ultimaker-cura" in cleaned, cleaned)

    # --- and he keeps the answer, not just the silence ------------------------
    for kept in ("Creality Ender 3 V2", "$200", "Ultimaker 2+", "PLA filament",
                 "adhesive sheet"):
        check_(f"he still gets {kept!r}", kept in cleaned, cleaned[:160])

    # --- and is told why, rather than left to ask twice ----------------------
    note = explain(removed)
    check_("it says links were left out", "left out" in note, note)
    check_("...and says why, honestly", "dead one" in note, note)
    check_("no note when nothing was invented", explain([]) == "")

    # --- the shapes a model uses to point at a real result --------------------
    led2 = LinkLedger()
    led2.note({"items": [{"url": "https://www.wcvb.com/article/brockton-shooting/12345"}]})
    for variant, ok in (
            ("https://www.wcvb.com/article/brockton-shooting/12345", True),
            ("http://wcvb.com/article/brockton-shooting/12345", True),   # scheme/www
            ("https://www.wcvb.com/article/brockton-shooting/12345/", True),  # slash
            ("https://www.wcvb.com", True),                              # bare host shown
            ("https://www.wcvb.com/article/something-else/999", False),   # different claim
            ("https://cnn.com/article/brockton-shooting/12345", False)):  # different host
        got = led2.allows(variant)
        check_(f"{'allows' if ok else 'blocks'} {variant[:52]!r}", got == ok, got)

    # --- an empty ledger blocks everything, which is the safe direction ------
    empty = LinkLedger()
    out, gone = check("See https://www.amazon.com/dp/B0FAKE123 for details.", empty)
    check_("with no tool results, no link goes out", "amazon.com" not in out, out)
    check_("...and it is reported", len(gone) == 1, gone)

    # --- text without links is left completely alone -------------------------
    plain = "Nvidia is down 4.6% today, sir."
    out2, gone2 = check(plain, empty)
    check_("a reply with no links is untouched", out2 == plain and not gone2, out2)

    # --- blocking is only half: he asked for links and must GET some ---------
    check_("'find me some links on Amazon' is a request for links",
           wanted_links("Can you find me some links on Amazon of what I'll need"))
    check_("'there's no links I can click on' is too",
           wanted_links("Thank you Jarvis but there's no links I can click on"))
    check_("an ordinary question is not",
           not wanted_links("how possible is that for me?"))

    stripped, _ = check(INVENTED, led)
    supplied = supply(stripped, led)
    check_("the real search results are handed over instead",
           "amazon.com/dp/B08663TXWS" in supplied, supplied[-240:])
    check_("...named, so he knows what he is clicking",
           "Creality Ender 3 V2 3D Printer" in supplied, supplied[-240:])
    check_("the link that survived is not offered twice",
           supplied.count("ultimaker-cura") == 1, supplied[-240:])
    check_("...and the offered link keeps its exact case",
           "B08663TXWS" in supplied, supplied[-240:])
    # One surviving link is not "he got what he asked for": he asked about five
    # things. Bailing on the first survivor handed him one link out of five.
    check_("a partial answer is still topped up",
           supply("See https://ultimaker.com/software/ultimaker-cura", led)
           != "See https://ultimaker.com/software/ultimaker-cura")
    check_("...but nothing is added when there is nothing left to add",
           supply("See https://ultimaker.com/software/ultimaker-cura "
                  "and https://www.amazon.com/dp/B08663TXWS "
                  "and https://www.creality.com/products/ender-3-v2", led).count("http") == 3)

    bare = LinkLedger()
    check_("with nothing to offer, it says so rather than inventing",
           "stand behind" in supply("Here are some options.", bare))

    # --- a price nobody looked up is not a fact about today -------------------
    quoted = "Ultimaker 2+ - $1,500, professional-grade."
    check_("an unlooked-up price is qualified",
           "from memory" in price_caveat(quoted, bare), price_caveat(quoted, bare))

    priced = LinkLedger()
    priced.note({"stocks": [{"symbol": "NVDA", "price": 178.2,
                             "url": "https://finnhub.io/x"}]})
    check_("a price a tool really returned is NOT qualified",
           "from memory" not in price_caveat("Nvidia is at $178.20.", priced),
           price_caveat("Nvidia is at $178.20.", priced))
    check_("a reply with no prices is untouched",
           price_caveat("Nvidia is down today.", bare) == "Nvidia is down today.")

    # --- punctuation is not left in ruins ------------------------------------
    out3, _ = check("The printer: https://www.amazon.com/dp/BFAKE .", empty)
    check_("no stranded colon where a link was", ": ." not in out3 and "  " not in out3,
           repr(out3))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
