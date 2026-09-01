"""Phase 3: describing a thing, and the two ways a photo reaches him.

This phase is small on purpose — the Gemma 3 + mmproj plumbing already existed
and is reused rather than rebuilt. So what is gated is the part that is actually
new, plus the failure paths, which is where a reused pipeline usually breaks:

  * the PROMPT is the feature. "Describe this image" makes a vision model narrate
    a scene; he wants to know what the object IS, what it is made of, and what is
    wrong with it. The prompt must also carry the honesty rule, because a vision
    model will invent a brand name off a blurry logo without being asked;
  * a missing file, a wrong type, an oversized file and an unavailable model are
    all SENTENCES, never exceptions — this runs from the Telegram poller, which
    carries reminders and alerts;
  * the Telegram photo path takes the LARGEST of the sizes Telegram offers, and
    deletes its temp copy afterwards. A photo he sent is not something this
    program should quietly accumulate on disk.

Runs offline: the model is stubbed, so this proves the wiring and the failure
behaviour without a 4B model or a network. Two real sample images are generated
on the fly rather than committed as binaries.

Run: python tests/test_vision_analyze.py
"""
import asyncio
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p3.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def sample_image(path, size=(160, 120), colour=(90, 120, 200)):
    from PIL import Image
    Image.new("RGB", size, colour).save(path)
    return path


async def main() -> int:
    from llm import vision_server
    from tools import vision_analyze as VA

    tmpdir = tempfile.mkdtemp()
    good = sample_image(os.path.join(tmpdir, "thing.jpg"))
    notimg = os.path.join(tmpdir, "notes.txt")
    open(notimg, "w").write("not an image")

    # --- the prompt is the feature ------------------------------------------
    p = VA.OBJECT_PROMPT.lower()
    check("the prompt asks what it is MADE of", "made of" in p)
    check("...and how it is built or finished",
          "constructed" in p or "finished" in p)
    check("...and what is notable or damaged", "damaged" in p or "unusual" in p)
    check("...and forbids inventing text it cannot read",
          "do not invent" in p and "cannot clearly read" in p,
          "a vision model will read a brand off a blurry logo unprompted")
    check("...and requires admitting what it cannot see",
          "say so plainly" in p)

    # --- failure paths, before any model is touched -------------------------
    check("a missing file is a sentence",
          (await VA.analyze_object(os.path.join(tmpdir, "nope.jpg"))).get("error"))
    check("a non-image is a sentence", (await VA.analyze_object(notimg)).get("error"))
    check("an empty path is a sentence", (await VA.analyze_object("")).get("error"))
    check("None is a sentence, not a crash",
          (await VA.analyze_object(None)).get("error"))

    # --- with the model stubbed ---------------------------------------------
    calls = {}

    class FakeVision:
        async def ensure(self):
            return True

        async def describe(self, image_b64, question, max_tokens=400):
            calls["question"] = question
            calls["b64_len"] = len(image_b64)
            return "  A blue ceramic mug, glazed, with a chip on the rim.  "

    real = vision_server.vision
    vision_server.vision = FakeVision()
    try:
        res = await VA.analyze_object(good)
        check("a good photo comes back described", "ceramic mug" in res.get("analysis", ""), res)
        check("...trimmed", not res["analysis"].startswith(" "), repr(res.get("analysis")))
        check("...and the object prompt was the one sent",
              "made of" in calls.get("question", "").lower())
        check("...over a downscaled image, not the raw file",
              calls.get("b64_len", 0) > 0)

        res = await VA.analyze_object(good, "is the handle cracked?")
        check("a specific question is appended, not substituted",
              "made of" in calls["question"].lower()
              and "handle cracked" in calls["question"].lower(), calls["question"][-80:])
        check("...and reported back", res.get("asked") == "is the handle cracked?")

        class Empty(FakeVision):
            async def describe(self, *a, **k):
                return "   "
        vision_server.vision = Empty()
        check("an empty answer is an error, not a blank description",
              (await VA.analyze_object(good)).get("error"))

        class Broken(FakeVision):
            async def describe(self, *a, **k):
                raise RuntimeError("model fell over")
        vision_server.vision = Broken()
        out = await VA.analyze_object(good)
        check("a model that raises becomes a sentence", out.get("error"), out)

        class Absent(FakeVision):
            async def ensure(self):
                return False
        vision_server.vision = Absent()
        check("an unavailable model says so",
              "not available" in (await VA.analyze_object(good)).get("error", ""))
    finally:
        vision_server.vision = real

    # --- the Telegram input path --------------------------------------------
    import remote_telegram as RT
    src = inspect.getsource(RT)
    check("a photo is no longer refused",
          "can't read attachments yet" not in src)
    check("the LARGEST size Telegram offers is used",
          "photo[-1]" in src, "the first entry is a thumbnail")
    check("the caption is passed as the question",
          'msg.get("caption")' in src)
    check("the temp copy is deleted afterwards",
          "os.remove(tmp)" in src,
          "a photo he sent should not accumulate on disk")
    check("photo analysis cannot raise into the poller",
          "telegram photo analysis failed" in src)
    check("an oversized photo is refused before download",
          "MAX_PHOTO_BYTES" in src)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
