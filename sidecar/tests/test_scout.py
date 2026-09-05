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
        args = seen["a baseball"]["_ask"]["args"]
        check("what he was shown travels into the confirmed call",
              args.get("reference") == "p.jpg",
              "looking again could find something else, and then what he "
              "agreed to is not what gets made")
        check("...as a reference, not as a photo he supplied",
              not args.get("image_path"),
              "image_path routes to tier 3 / hands tier 2 a photograph to "
              "trace — the emblem regression")
        check("...and the confirmation does not ask again",
              args.get("confirmed") is True)
        margs = seen["a nintendo 2ds xl"]["_ask"]["args"]
        check("a found model is fetched, not generated",
              margs.get("tier") == 5, margs)
        check("...and it is THAT model, not a fresh search's",
              (margs.get("scouted_model") or {}).get("repo") == "x/ps5", margs)
        check("a picture route carries no model",
              not args.get("scouted_model"), args)

        print("\n-- and the tiers use what they were handed --")
        import create3d
        real_ref, real_photo, real_show = (create3d.reference_image,
                                           create3d.from_photo,
                                           create3d._show_reference)
        real_avail = create3d.available
        searched = []
        built = []

        async def no_search(desc, flat=False, skip=0):
            searched.append(desc)
            return "other.jpg"

        async def fake_photo(path, name, progressive=False, **kw):
            built.append(path)
            return {"stl": "x.stl", "name": name}

        async def no_show(*a, **k):
            return None

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            ref = f.name
        create3d.reference_image = no_search
        create3d.from_photo = fake_photo
        create3d._show_reference = no_show
        create3d.available = lambda: {**real_avail(), 4: True}
        try:
            r = await create3d.from_text("a baseball", "b", reference=ref)
            check("tier 4 builds from the scouted picture",
                  built == [ref] and not r.get("error"), (built, r))
            check("...without searching for another",
                  not searched, searched)
            check("...and tidies it away afterwards", not os.path.exists(ref))
            built.clear()
            await create3d.from_text("a baseball", "b", reference=ref, skip=1)
            check("'find another design' does look again",
                  searched == ["a baseball"] and built == ["other.jpg"],
                  (searched, built))
        finally:
            create3d.reference_image = real_ref
            create3d.from_photo = real_photo
            create3d._show_reference = real_show
            create3d.available = real_avail
            try:
                os.remove(ref)
            except OSError:
                pass

        import model_find as MF
        real_find, real_meshes, real_fetch = MF.find, MF.github_meshes, MF.fetch
        real_scale = create3d._apply_unit_scale
        asked = []

        async def fake_find(desc, limit=6):
            return {"candidates": [{"url": "https://github.com/other/ps5",
                                    "host": "github.com", "title": "other"}]}

        async def fake_meshes(url, want=""):
            asked.append(url)
            repo = url.split("github.com/")[1]
            return [{"repo": repo, "path": "ps5.stl", "bytes": 204800,
                     "url": f"{url}/raw/ps5.stl"}]

        async def fake_fetch(url, name=""):
            return {"stl": "ps5.stl", "triangles": 50000,
                    "size_mm": [390, 104, 260], "name": name}

        async def no_scale(got):
            return {}

        MF.find, MF.github_meshes, MF.fetch = fake_find, fake_meshes, fake_fetch
        create3d._apply_unit_scale = no_scale
        try:
            r = await create3d.from_the_web(
                "a ps5", "p", scouted_model={"repo": "x/ps5", "file": "ps5.stl",
                                             "page": "https://github.com/x/ps5"})
            check("tier 5 fetches the model he was shown first",
                  asked[:1] == ["https://github.com/x/ps5"]
                  and r.get("credit") == "x/ps5", (asked, r.get("credit")))
            asked.clear()
            r = await create3d.from_the_web("a ps5", "p")
            check("...and searches as before when nothing was scouted",
                  r.get("credit") == "other/ps5", r.get("credit"))
        finally:
            MF.find, MF.github_meshes, MF.fetch = real_find, real_meshes, real_fetch
            create3d._apply_unit_scale = real_scale

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
