"""Look first, say what was found, then ask — for every render.

His example was a PlayStation: *"he should look, search the internet, research
his LLM at the same time, and say — all right, I found this image of a PS5, I
couldn't find any dimensions, but do you want me to use my best judgment?"* And
then his correction: *"the PS5 flow was an example. It should work like that for
all types of 3D rendering."*

So the question is never "that's about forty seconds, shall I?", which tells him
the cost and nothing about the thing. It is what was actually found — and when
nothing was found, that is still an offer, because "I found nothing" as a final
answer is the thing he told me to stop saying.

Offline: no searches are run. What is tested is the four shapes of the question,
where the looking is skipped, and that what he was shown is what gets used.

Run: python tests/test_scout.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "scout.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import scout
    from tools import render_tools as RT

    print("\n-- reading a size out of what a page says --")
    for text, want in (
            ("The PS5 measures 390 x 104 x 260 mm and weighs 4.5 kg",
             "390 x 104 x 260 mm"),
            ("A baseball is 73.5 mm in diameter", "73.5 mm"),
            ("Height: 15.4 inches", "15.4 inches"),
            ("the ring is 76 mm across", "76 mm"),
            ("it costs 499 dollars and weighs 4.5 kg", None),
            ("no numbers here at all", None)):
        a = scout._DIMS.search(text)
        b = scout._ONE_DIM.search(text)
        got = (a.group(0) if a else
               (next((g for g in b.groups() if g), None) if b else None))
        got = got.strip() if got else None
        check(f"{text[:38]!r}", got == want, f"got {got!r}")
    check("a price is not a dimension",
          not scout._DIMS.search("it costs 499 dollars"),
          "a bare number with a unit is as likely to be money or weight")

    print("\n-- the four shapes of the question --")
    shapes = {
        "model": {"model": {"repo": "x/ps5", "file": "ps5.stl", "bytes": 204800},
                  "dimensions": {}, "picture": {}},
        "dimensions": {"model": {}, "dimensions": {"said": "390 x 104 x 260 mm"},
                       "picture": {"path": "p.jpg"}},
        "picture": {"model": {}, "dimensions": {}, "picture": {"path": "p.jpg"}},
        "nothing": {"model": {}, "dimensions": {}, "picture": {}},
    }
    for want, found in shapes.items():
        q = scout.question("a playstation 5", found)
        check(f"{want}: recognised", q["found"] == want, q)
    check("a found model names the file and whose it is",
          "ps5.stl" in scout.question("a ps5", shapes["model"])["question"]
          and "x/ps5" in scout.question("a ps5", shapes["model"])["question"])
    check("found dimensions are read back",
          "390 x 104 x 260 mm" in
          scout.question("a ps5", shapes["dimensions"])["question"])
    check("a picture with no dimensions offers best judgment",
          "best judgment" in scout.question("a ps5", shapes["picture"])["question"],
          "this is the sentence he asked for word for word")
    nothing = scout.question("a widget", shapes["nothing"])["question"]
    check("...and finding nothing is still an offer",
          "Shall I" in nothing and "?" in nothing,
          "'I found nothing' as a final answer is what he told me to stop saying")

    print("\n-- it is asked before every render, not just for products --")
    real_look = scout.look

    async def looked(d):
        return shapes.get({"a playstation 5": "dimensions",
                           "a baseball": "picture",
                           "a nintendo 2ds xl": "model"}.get(d, "nothing"))

    scout.look = looked
    try:
        seen = {}
        for d in ("a playstation 5", "a baseball", "a nintendo 2ds xl",
                  "a widget nobody has"):
            r = await RT.make_hologram(description=d, name="t")
            seen[d] = r
        check("a product scouts", seen["a playstation 5"].get("found") == "dimensions")
        check("...and so does a plain object",
              seen["a baseball"].get("found") == "picture",
              "the PS5 flow was an example, not a special case")
        check("...and one somebody has published",
              seen["a nintendo 2ds xl"].get("found") == "model")
        check("...and one nothing is known about",
              seen["a widget nobody has"].get("found") == "nothing")
        check("the cost is mentioned after what was found, never instead of it",
              seen["a playstation 5"]["_ask"]["question"].index("dimensions")
              < seen["a playstation 5"]["_ask"]["question"].index("Shall I"))
        check("what he was shown travels into the confirmed call",
              seen["a baseball"]["_ask"]["args"].get("image_path") == "p.jpg",
              "looking again could find something else, and then what he "
              "agreed to is not what gets made")
        check("...and the confirmation does not ask again",
              seen["a baseball"]["_ask"]["args"].get("confirmed") is True)

        print("\n-- and skipped where there is nothing to look up --")
        dimensioned = await RT.make_hologram(
            description="a plate 40 by 30 by 6 millimetres", name="p")
        check("a request he dimensioned himself does not scout",
              "found" not in dimensioned,
              "searching the web for a plate he already specified is theatre")
        withpic = await RT.make_hologram(description="this logo",
                                         image_path=__file__, name="l")
        check("a picture he supplied does not scout", "found" not in withpic)
    finally:
        scout.look = real_look

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
