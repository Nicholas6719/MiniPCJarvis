"""Building a thing out of its parts, when the thing itself cannot be built.

His requirement: *"Render me Iron Man's Mark 3 suit... I zoom in on the helmet
to see the helmet specs. I zoom in on the gauntlet to see the gauntlet specs."*

A whole suit cannot be reconstructed from one photograph — that is what produced
the lump — and OpenSCAD cannot sculpt armour. But a HELMET can be found or
reconstructed, and so can a gauntlet, and a chestplate. So the request is taken
apart instead of the mesh:

    "iron man mark 3 suit"
        -> ask what it is made of        helmet, chestplate, gauntlet, boot
        -> build each one on its own     tier 5 if somebody published it,
                                         tier 4 from its own reference if not
        -> lay them out and name them    one model, several named parts

That inverts the hard problem into an easy one. Segmenting a finished blob into
"helmet" and "gauntlet" is a research problem; asking what a suit is made of is
something the model and the web both already know. It improves the RESULT too:
"iron man mark 3 helmet" is a far better image search than "iron man mark 3
suit", and a helmet photographed alone reconstructs far better than a whole
figure at the same resolution.

PLACEMENT IS ARITHMETIC, NEVER THE MODEL'S. Measured in the literature and worth
taking seriously: an LLM emitting absolute coordinates places objects at or
BELOW random — Holodeck reports 0.364 against 0.369 for collision-free random
placement, while a two-line "put it against a wall" heuristic scores 0.645.
Every system that works asks the model for relationships and solves the
positions in code.

So the model is asked for NAMES ONLY. The parts are laid out along a row, in the
order they were listed, spaced by their own measured widths — a workbench, not a
guess at anatomy. Nobody has to trust it, it cannot be subtly wrong, and it is
the view he actually described: every piece visible, each one nameable.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger("jarvis.components")

# Few enough to be worth waiting for and to fit on a bench.
MAX_COMPONENTS = 8
MIN_COMPONENTS = 2

# The gap between parts on the bench, as a fraction of the widest one.
GAP = 0.15

# Things that plainly have named pieces. Not a whitelist for deciding what can
# be built — anything can go through here on request — but what makes JARVIS
# offer it without being asked.
MULTIPART = re.compile(
    r"\b(?:suit|armou?r|costume|outfit|helmet and|full body|"
    r"assembly|rig|kit|set of|whole)\b", re.I)


def worth_splitting(description: str) -> bool:
    """Would he expect this to arrive in pieces?"""
    return bool(MULTIPART.search(description or ""))


async def component_list(description: str) -> list[str]:
    """What the thing is made of, in the words a person would use.

    Names only — no sizes, no positions, no arrangement. Everything the model
    is not reliable about is computed instead, and asking for less is what makes
    the little it does return trustworthy.
    """
    from llm.provider import local_llm

    prompt = (
        "List the separate physical pieces that make up this object, as a "
        "person would name them. One per line, lower case, two or three words "
        "at most, no numbering, no description, no sizes.\n\n"
        "Only pieces that are genuinely separate objects you could hold in your "
        "hand one at a time. If it is a single solid object with no separate "
        "pieces, answer with the word NONE and nothing else.\n\n"
        f"Object: {description}")
    try:
        out = ""
        async for ch in local_llm.stream([{"role": "user", "content": prompt}],
                                         max_tokens=400,
                                         sampling={"temperature": 0.2, "top_p": 0.9}):
            out += ch.text
            if ch.done:
                break
    except Exception:
        log.warning("could not ask what %r is made of", description, exc_info=True)
        return []

    return parse_components(out)


def parse_components(text: str) -> list[str]:
    """The list, out of whatever the model actually said.

    Kept separate from the call so it can be tested against real replies. A
    component is a short noun phrase; everything the model wraps around the
    list is longer than that, and that is the whole filter.
    """
    names: list[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip().strip("-*\u2022\t ").strip()
        if not line or line.upper() == "NONE":
            continue
        line = re.sub(r"^\d+[.)]\s*", "", line)
        # A colon means a heading or a label, never a piece.
        if ":" in line:
            continue
        line = re.sub(r"[^a-zA-Z0-9 \-]", "", line).strip().lower()
        if not line or len(line) > 34 or len(line.split()) > 4:
            continue
        if line not in names:
            names.append(line)
    return names[:MAX_COMPONENTS]


async def build_each(description: str, names: list[str], base_name: str) -> dict:
    """Make every component on its own, and lay them out together.

    Each piece goes through the ordinary route for its own name, so a helmet
    somebody has published is downloaded and a gauntlet nobody has is
    reconstructed from a picture of a gauntlet. The pieces do not have to agree
    about how they were made.
    """
    import asyncio

    import create3d
    import meshio
    from tools.fabrication import safe_name, work_dir

    made: list[dict] = []
    failed: list[str] = []
    for n in names:
        want = f"{description} {n}".strip()
        try:
            tier = create3d.choose_tier(want, "")
            r = await create3d.build(tier, description=want,
                                     name=f"{base_name}-{safe_name(n)}")
        except Exception:
            log.warning("component %r would not build", n, exc_info=True)
            failed.append(n)
            continue
        if r.get("error") or not r.get("stl"):
            log.info("component %r: %s", n, r.get("error", "nothing came back"))
            failed.append(n)
            continue
        made.append({"name": safe_name(n).replace("-", "_"), "stl": r["stl"],
                     "said_as": n, "tier": r.get("tier")})

    if len(made) < MIN_COMPONENTS:
        return {"error": f"I could only make {len(made)} of those pieces, sir",
                "made": [m["said_as"] for m in made], "failed": failed}

    # ---------------------------------------------------------- the bench
    # Measured, in order, spaced by their own widths. No part is asked where it
    # goes and none of it can be subtly wrong.
    loaded = []
    for m in made:
        try:
            loaded.append((m, await asyncio.to_thread(meshio.load, m["stl"])))
        except Exception:
            log.warning("component %s would not read back", m["name"], exc_info=True)
            failed.append(m["said_as"])
    if len(loaded) < MIN_COMPONENTS:
        return {"error": "the pieces would not read back, sir", "failed": failed}

    widths = [float(t.reshape(-1, 3)[:, 0].max() - t.reshape(-1, 3)[:, 0].min())
              for _, t in loaded]
    gap = max(widths) * GAP
    d = work_dir()
    placed: list[dict] = []
    x = 0.0
    for (m, tris), w in zip(loaded, widths):
        flat = tris.reshape(-1, 3)
        # To the bench: sitting on z=0, centred across, starting at x.
        off = (x - float(flat[:, 0].min()),
               -float(flat[:, 1].min() + flat[:, 1].max()) / 2.0,
               -float(flat[:, 2].min()))
        out = d / f"{base_name}.{m['name']}.stl"
        await asyncio.to_thread(meshio.write_stl, meshio.translated(tris, off),
                                str(out))
        placed.append({"name": m["name"], "stl": str(out),
                       "said_as": m["said_as"], "tier": m.get("tier")})
        x += w + gap

    whole = d / f"{base_name}.stl"
    import numpy as np

    # ARGUMENTS ARE EVALUATED BEFORE THE CALL. Written as
    # `to_thread(write_stl, np.concatenate([load(p) for p in placed]), ...)`
    # every one of those loads ran on the event loop and only the write went to
    # the thread — six components at up to 400,000 triangles each. The whole
    # job goes across, not just the last step of it.
    def join_and_write() -> None:
        meshio.write_stl(
            np.concatenate([meshio.load(p["stl"]) for p in placed], axis=0),
            str(whole))

    await asyncio.to_thread(join_and_write)

    import assembly
    assembly.write_manifest(str(whole), placed)
    return {"stl": str(whole), "name": base_name, "parts": [p["name"] for p in placed],
            "part_count": len(placed), "built_in_parts": True,
            "components": [p["said_as"] for p in placed],
            "failed": failed}


async def from_components(description: str, name: str = "") -> dict:
    """Take the request apart, build each piece, and hand back one model."""
    from tools.fabrication import safe_name

    desc = (description or "").strip()
    if not desc:
        return {"error": "what should I make, sir?"}
    names = await component_list(desc)
    if len(names) < MIN_COMPONENTS:
        return {"error": "I couldn't work out what that's made of, sir",
                "no_components": True}
    log.info("%r is made of: %s", desc[:40], ", ".join(names))
    return await build_each(desc, names, safe_name(name or desc))
