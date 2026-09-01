"""A word that chooses the SKILL must survive canonicalisation. A word that fills
a SLOT may be erased.

That single line is the whole rule, and breaking it cost him four separate bugs
in one day — every one found by him using his own assistant, none by a test:

  * "open the camera" launched the Windows Camera app. _CANON rewrote it to
    "open APP", which is open_app's canonical form, so the camera never had a
    chance at it.
  * "remember my face" did NOTHING AT ALL. Rewritten to "remember that FACT", the
    memory skill's guard correctly refused it — but the words "my face" were
    already gone, so the fallthrough re-classified a sentence with no face in it
    and landed nowhere.
  * "find me a video of X" could only ever be a web search. It folded onto
    "search the web for THING", so the one thing JARVIS could do was search and
    recite a URL into the panel — instead of opening it in his browser.
  * "show me pictures of X in my browser" rendered into the HUD panel he had
    just asked to bypass, because "in my browser" was folded away by the image
    canon before anything could act on it.

seed_collisions.py cannot catch these. It compares SEEDS against SEEDS, and in
every case above the phrasing was not a seed — it could not be, because seeding
it would have produced the very collision the fix had to remove first. So this
gate does not look at seeds at all. It takes phrasings he would actually say,
built around the nouns that NAME PARTS OF HIM, and asserts the noun is still
there after the rewrite.

The distinction that makes it work: in "open spotify", `spotify` is an argument —
erasing it is correct and intended, because every app launch should embed as the
same shape. In "open the camera", `camera` is not an argument. It is the answer
to WHICH SKILL, and a rewrite that deletes it has destroyed the only evidence.

Offline, no model, no embeddings. Run: python tests/test_canon_erasure.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "canon.db"))

from brain.router import _norm  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


# Nouns that name a PART OF HIM rather than a thing to act on. Each maps to
# phrasings he would plausibly say. Add a noun here whenever a new subsystem
# gets a voice — that is the moment this class of bug is created.
SUBSYSTEMS: dict[str, list[str]] = {
    "camera": [
        "open the camera", "close the camera", "turn on the camera",
        "bring up the camera", "show me the camera", "put the camera away",
        "hide the camera", "pull up the camera",
    ],
    "webcam": ["open the webcam", "turn off the webcam", "show me the webcam"],
    "face": [
        "remember my face", "learn my face", "forget my face",
        "remember what my face looks like",
    ],
    "video": [
        "find me a video of a rocket launch", "show me a video of the aurora",
        "pull up a video about black holes", "get me a video of a train",
    ],
    "youtube": [
        "find me a youtube video of a rocket launch", "search youtube for jazz",
    ],
    "trailer": ["find the trailer for dune", "show me the trailer for tenet"],
    "gameplay": ["find me gameplay of elden ring", "show me gameplay of doom"],
    "browser": [
        "show me iron man in my browser", "look that up in my browser",
        "show me pictures of a nebula in my browser",
        "find the best mini pc in my browser",
    ],
    "brave": ["look up elden ring in brave", "show me images of mars in brave"],
    "screen": ["look at my screen", "what's on my screen", "describe my screen"],
    "music": ["put on some music", "play some music"],
    "volume": ["turn the volume down", "set the volume"],
}


def main() -> int:
    print("=== a word that selects the skill must survive the rewrite ===")
    for noun, phrasings in SUBSYSTEMS.items():
        for said in phrasings:
            canon = _norm(said)
            check(f"{noun!r} survives {said!r}", noun in canon,
                  f"-> {canon!r} (the noun that chose the skill is gone)")

    # --- the other half of the rule, and the anti-cheat -------------------
    # This gate must not be satisfiable by gutting _CANON. Slot values SHOULD
    # still be erased, and these prove the rewrite is still doing its job.
    print("\n=== ...and a word that fills a slot is still erased, as intended ===")
    for said, gone, canon_expected in [
        ("open spotify", "spotify", "open APP"),
        ("launch notepad", "notepad", "open APP"),
        ("close chrome", "chrome", "close APP"),
        ("look up the population of tokyo", "tokyo", "search the web for THING"),
        ("remember that i park in the north garage", "garage", "remember that FACT"),
    ]:
        canon = _norm(said)
        check(f"{said!r} still collapses to a shape", gone not in canon,
              f"-> {canon!r}; if this fails, _CANON has been weakened")
        check(f"  ...specifically {canon_expected!r}",
              canon_expected.lower() in canon.lower(), f"-> {canon!r}")

    # --- the four real bugs, named, so they can never come back silently ---
    print("\n=== the four he actually hit ===")
    for said, must_keep, story in [
        ("open the camera", "camera", "launched the Windows Camera app"),
        ("remember my face", "face", "did nothing at all"),
        ("find me a video of a rocket launch", "video",
         "could only ever be a web search"),
        ("show me pictures of a nebula in my browser", "browser",
         "rendered into the panel he asked to bypass"),
    ]:
        canon = _norm(said)
        check(f"{said!r} — {story}", must_keep in canon, f"-> {canon!r}")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
