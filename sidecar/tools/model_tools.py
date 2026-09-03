"""Fetching a model somebody already made, and being honest that it is theirs.

His requirement was "render Iron Man Mark III... it needs to happen", and the
technique that makes it happen is not generation. Nothing here can sculpt
armour: single-image reconstruction gives a lump and OpenSCAD is a solid
modeller. What DOES work is the thing a person would do — find the model
somebody spent weeks on, and download it.

TWO THINGS THIS SURFACE IS CAREFUL ABOUT.

IT IS SOMEBODY ELSE'S WORK. Every result carries its title, its host and its
author where they can be read, and the spoken line says "found", never "made".
Showing a stranger's sculpture as though JARVIS produced it would be a lie he
might repeat to someone else.

HE IS ASKED BEFORE IT DOWNLOADS. It reports what it found and how big it is and
waits, through the same conversational confirmation the long renders use. A
download is an action on his disk.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.model")

# What was offered last, so "yes" knows what it is agreeing to.
_pending: dict = {}


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n // 1024} KB"


async def find_3d_model(description: str = "", confirmed: bool = False) -> dict:
    """Find a ready-made 3D model of something and, once he agrees, fetch it."""
    import model_find as MF

    desc = (description or "").strip()
    if confirmed and _pending.get("url"):
        got = await MF.fetch(_pending["url"], name=_pending.get("name", ""))
        if got.get("error"):
            return {**got, "spoken": f"{got['error'].rstrip('.')}, sir."}
        try:
            from tools.holo_tools import show_hologram
            await show_hologram(path=got["stl"])
        except Exception:
            log.debug("could not project the downloaded model", exc_info=True)
        credit = _pending.get("credit") or got.get("host", "")
        return {**got, "credit": credit, "downloaded": True,
                "spoken": f"Here it is, sir — {got['spoken_size']}. "
                          f"Made by {credit}; I only fetched it."}

    if not desc:
        return {"error": "a model of what, sir?"}

    found = await MF.find(desc)
    cands = found.get("candidates") or []
    if not cands:
        return {"error": f"I couldn't find a model of {desc}, sir",
                "spoken": f"I couldn't find a model of {desc}, sir."}

    # A GITHUB RESULT CAN BE FOLLOWED ALL THE WAY TO A FILE. Printables, Cults3D
    # and MyMiniFactory all have the model and all put it behind a JavaScript app
    # and a session — measured, not assumed — so those are offered as pages to
    # open rather than pretended to be downloads.
    import create3d
    for c in cands:
        if "github.com" not in c.get("host", ""):
            continue
        meshes = await MF.github_meshes(c["url"])
        # THE BIGGEST FILE IN THE REPO IS NOT THE ANSWER. It was, and it
        # returned a webcam calibration plate for "a d20" and a keyslot bracket
        # for "a Mandalorian helmet" — both from repos that genuinely matched
        # the subject. Same picker the tier uses, so the tool and the tier
        # cannot disagree about what the best file is.
        best, others = create3d._pick_mesh(meshes, desc)
        if not best:
            continue
        _pending.clear()
        _pending.update({"url": best["url"], "name": desc,
                         "credit": best["repo"], "bytes": best["bytes"]})
        parts = 1 + sum(1 for o in others if o.get("is_piece"))
        piece = (f" It's published in {parts} parts and this is one of them."
                 if best.get("is_piece") and parts > 1 else "")
        return {"_ask": {
            "subject": desc,
            "question": (f"I found {c['title'][:60]} — {best['path'].split('/')[-1]}, "
                         f"{_mb(best['bytes'])}, from {best['repo']}.{piece} "
                         f"Shall I get it?"),
            "tool": "find_3d_model",
            "args": {"description": desc, "confirmed": True},
        }}

    # Nothing directly fetchable. Say what was found and where, rather than
    # inventing something worse.
    top = cands[0]
    return {"candidates": cands[:4], "fetchable": False,
            "spoken": f"I found {len(cands)} of them, sir — the best looks like "
                      f"{top['title'][:70]} on {top['host']}. "
                      f"That site needs an account to download, so I can open it "
                      f"for you.",
            "instruction": "Offer to open the page. Do NOT claim to have the file."}


def register_all() -> None:
    registry.register(Tool(
        name="find_3d_model",
        description="Find a ready-made 3D model of something on the web and "
                    "download it. Use when he asks for a real object that is "
                    "sculpted rather than engineered — a character, a helmet, "
                    "armour, a prop, a figure — where generating one would give "
                    "a poor result. Reports whose work it is.",
        parameters={"type": "object", "properties": {
            "description": {"type": "string",
                            "description": "what to find, e.g. 'iron man mark 3 helmet'"},
            "confirmed": {"type": "boolean"}},
            "required": ["description"]},
        # LOW: it writes a file into the work folder from the internet. The file
        # is data — an STL is never executed — and it is size-bounded and parsed
        # before it is shown.
        risk=Risk.LOW, handler=find_3d_model, timeout=180))
