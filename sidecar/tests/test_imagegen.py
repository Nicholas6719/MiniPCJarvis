"""Drawing a picture: what it refuses, what it costs, and where it lands.

stable-diffusion.cpp over Vulkan on the 780M, measured cold on 2026-09-08 at
19.5 s for 512x512 — over the cost threshold, so it asks first exactly as a
render does.

The real generation is behind JARVIS_IMAGEGEN_LIVE=1 and skipped LOUDLY
otherwise: it wants the GPU that llama-server is holding, and a gate that
takes it on every build would be competing with the assistant.

Run: python tests/test_imagegen.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "img.db"))

fails, skips = [], []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import imagegen
    from tools import image_tools as IT

    print("\n-- what a picture is OF --")
    # The article is kept on purpose: "a lighthouse" is a better prompt for
    # the model than "lighthouse", and it reads better in the file name too.
    for said, want in (
            ("draw me a picture of a lighthouse", "a lighthouse"),
            ("make me an image of a red sports car", "a red sports car"),
            ("generate a picture showing a robot", "a robot"),
            ("paint the sea at night", "sea at night"),
            ("a castle on a hill", "a castle on a hill")):
        got = IT._subject(said)
        check(f"{said!r} -> {want!r}", got == want, got)

    print("\n-- the file --")
    root = Path(tempfile.mkdtemp())
    (root / "Pictures").mkdir()
    from tools import file_tools
    file_tools.roots = lambda: {"pictures": root / "Pictures"}
    p1 = IT._where("a lighthouse")
    p1.write_bytes(b"x")
    p2 = IT._where("a lighthouse")
    check("it lands in Pictures", p1.parent == root / "Pictures", p1)
    check("...and never over one that is already there",
          p2.name == "a lighthouse (2).png", p2.name)
    check("a name with separators in it cannot escape",
          "/" not in IT._safe("../../evil") and "\\" not in IT._safe("..\\evil"),
          IT._safe("../../evil"))

    print("\n-- the size --")
    check("sizes are named, not guessed at", set(IT._SIZES) ==
          {"square", "portrait", "landscape", "wide", "tall"}, sorted(IT._SIZES))
    for name, (w, h) in IT._SIZES.items():
        check(f"{name} is a multiple of 64 and within reach",
              w % 64 == 0 and h % 64 == 0 and max(w, h) <= imagegen.MAX_SIDE, (w, h))

    print("\n-- it costs twenty seconds, so it asks --")
    import render_estimates as est
    check("a picture has its own clock", 9 in est.SEED and est.SEED[9] > 0, est.SEED.get(9))
    check("...and it is not a 3D model's", est.SEED[9] != est.SEED.get(3))
    check("twenty seconds is over the threshold he set",
          est.estimate(9) > est.ask_threshold(), (est.estimate(9), est.ask_threshold()))
    if imagegen.available():
        r = await IT.generate_image(description="a lighthouse")
        check("it asks before spending them", "_ask" in r, r)
        ask = r.get("_ask", {})
        check("...and the answer runs the same tool, confirmed",
              ask.get("tool") == "generate_image" and ask["args"].get("confirmed") is True, ask)
        check("...and the question ends in one", ask.get("question", "").endswith("?"), ask)
    else:
        skips.append("the cost question (nothing installed)")

    print("\n-- nothing to draw with is said plainly --")
    real_avail, real_missing = imagegen.available, imagegen.missing
    imagegen.available = lambda: False
    imagegen.missing = lambda: "the image generator isn't installed"
    try:
        r = await IT.generate_image(description="a lighthouse")
        check("it says so rather than failing deeper",
              r.get("unavailable") and "isn't installed" in r.get("error", ""), r)
        check("...and does not offer to search the web instead",
              "search the web" in r.get("instruction", ""), r.get("instruction"))
    finally:
        imagegen.available, imagegen.missing = real_avail, real_missing
    check("an empty description is refused",
          (await IT.generate_image(description="  ")).get("error"), "")

    print("\n-- the tool --")
    IT.register_all()
    from tools.registry import Risk, registry
    t = registry.get("generate_image")
    check("it is registered at LOW", t is not None and t.risk is Risk.LOW, t.risk if t else None)
    check("...and says how it differs from searching the web",
          "show_images" in (t.description or ""), t.description if t else "")

    print("\n-- the real thing --")
    if os.environ.get("JARVIS_IMAGEGEN_LIVE") != "1":
        skips.append("the real generation (set JARVIS_IMAGEGEN_LIVE=1 to run it)")
        print("  SKIPPED - it wants the GPU llama-server is holding.")
    elif not imagegen.available():
        skips.append(f"the real generation ({imagegen.missing()})")
        print(f"  SKIPPED - {imagegen.missing()}")
    else:
        out = Path(tempfile.mkdtemp()) / "probe.png"
        r = await imagegen.generate("a red apple on a wooden table", out,
                                    width=512, height=512, steps=2, seed=7)
        check("a picture comes out", not r.get("error") and out.exists(), r)
        check("...of a believable size", out.exists() and out.stat().st_size > 40_000,
              out.stat().st_size if out.exists() else 0)
        check("...and it says how long it took", (r.get("seconds") or 0) > 0, r)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}"
          f"{f' ({len(skips)} skipped)' if skips else ''}")
    for s in skips:
        print("  skipped:", s)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
