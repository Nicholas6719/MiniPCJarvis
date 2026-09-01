"""What JARVIS says he sees must be what the model actually returned.

Phase 2b. YOLOX, on demand: "what do you see?" -> one frame -> eighty COCO
classes -> a sentence.

Measured on his machine: 71 ms a look, and on a test image it returned
person 0.91 and sports ball 0.94 — correct. Latency was never the constraint,
because a look happens once per question rather than thirty times a second;
accuracy was, which is why YOLOX-S (35.9 MB) beat NanoDet (3.8 MB) here.

What this file protects is the honesty of the answer. A vision feature that
embellishes is worse than none: he will find the edge of it within a day and
then not believe any of it.

Offline: the model is never loaded. Run: python tests/test_objects.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "obj.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import vision_objects as vo
    from brain.skills import say_look

    # --- the class list is the thing that silently renames everything --------
    check("COCO has exactly 80 classes", len(vo.CLASSES) == 80, len(vo.CLASSES))
    check("...starting at person", vo.CLASSES[0] == "person", vo.CLASSES[0])
    check("...ending at toothbrush", vo.CLASSES[-1] == "toothbrush", vo.CLASSES[-1])
    check("...with no duplicates", len(set(vo.CLASSES)) == 80)
    # spot-check indices that would go unnoticed if the list slipped by one
    for idx, want in ((0, "person"), (39, "bottle"), (63, "laptop"),
                      (67, "cell phone"), (56, "chair")):
        check(f"class {idx} is {want!r}", vo.CLASSES[idx] == want, vo.CLASSES[idx])

    # --- the sentence says what was found, and nothing more ------------------
    one = {"objects": [{"label": "person", "count": 1, "confidence": 0.9}]}
    check("one thing", vo.describe(one) == "a person", vo.describe(one))

    vowel = {"objects": [{"label": "apple", "count": 1, "confidence": 0.9}]}
    check("...with the right article", vo.describe(vowel) == "an apple",
          vo.describe(vowel))

    # SUPERSEDED: this used to expect "2 cups". Counts are now reported only for
    # classes where they are reliable (see COUNTED below) — the detector splits
    # objects across boxes often enough that a number is a confident-sounding
    # guess for everything except people.
    many = {"objects": [{"label": "person", "count": 1, "confidence": 0.9},
                        {"label": "laptop", "count": 1, "confidence": 0.8},
                        {"label": "cup", "count": 2, "confidence": 0.7}]}
    check("several, joined into a sentence",
          vo.describe(many) == "a person, a laptop and a cup", vo.describe(many))
    people = {"objects": [{"label": "person", "count": 3, "confidence": 0.9}]}
    check("...and people ARE counted", vo.describe(people) == "3 people",
          vo.describe(people))

    check("an empty room says so, rather than guessing",
          vo.describe({"objects": []}) == "nothing I recognise")
    check("an error produces no claim at all",
          vo.describe({"error": "the look failed"}) == "")

    # --- and the spoken line never invents --------------------------------
    check("he is told plainly when it saw nothing",
          say_look({}, {"said": "nothing I recognise"}) == "Nothing I recognise, sir.",
          say_look({}, {"said": "nothing I recognise"}))
    check("...and told the reason when it could not look",
          "couldn't look" in say_look({}, {"error": "the camera would not open"}))
    said = say_look({}, {"said": "a person and a laptop"})
    check("...and the objects when it did", said == "I can see a person and a laptop, sir.",
          said)

    # --- a missing model is survivable, not a crash -------------------------
    o = vo.Objects()
    o._unavailable = True
    res = o.detect(object())
    check("a missing model reports rather than raises",
          res.get("error") and "objects" not in res, res)

    # --- the floors that actually decide what he hears -----------------------
    # CONF is deliberately LOW now: a single frame is a coin flip, so it only
    # gathers candidates. Persistence below decides what survives.
    check("the per-frame floor only gathers candidates", 0.05 <= vo.CONF <= 0.25,
          vo.CONF)
    check("a kept object must reach a real confidence once",
          0.25 <= vo.KEEP_MIN_CONF <= 0.45, vo.KEEP_MIN_CONF)
    check("...or be convincing on its own", vo.KEEP_ALONE_CONF >= 0.55,
          vo.KEEP_ALONE_CONF)
    check("...and be in a decent share of the frames",
          0.3 <= vo.KEEP_SEEN_FRACTION <= 0.75, vo.KEEP_SEEN_FRACTION)
    check("a look uses several frames, not one", vo.LOOK_FRAMES >= 4, vo.LOOK_FRAMES)
    check("...and a cap on how much it will list", 3 <= vo.MAX_REPORTED <= 12,
          vo.MAX_REPORTED)

    # --- persistence, replayed against his ACTUAL room -----------------------
    # He held up a water bottle and was told nothing. Measured on his camera:
    # frame 0 has a mean brightness of 8 (the shutter is still opening), and the
    # same unmoving person scores 0.31, 0.68, 0.40, 0.52 on consecutive frames —
    # so ONE frame at a 0.50 floor is a coin flip. The room also produced cat
    # 0.20, dog 0.14, teddy bear 0.18, toilet 0.06, none of which exist, so
    # simply lowering the floor would have invented him a pet.
    #
    # These are the real per-frame numbers from 2026-09-01 13:35.
    real = [
        {"person": 0.22, "bed": 0.35, "couch": 0.07},
        {"person": 0.35, "cat": 0.12, "bed": 0.11, "couch": 0.10, "cup": 0.08},
        {"person": 0.50, "bed": 0.28, "potted plant": 0.10, "couch": 0.09},
        {"person": 0.50, "bed": 0.30, "couch": 0.18, "cat": 0.16, "dog": 0.10},
        {"person": 0.50, "bed": 0.20, "cat": 0.08, "teddy bear": 0.08},
        {"person": 0.31, "bed": 0.22, "cat": 0.20, "dog": 0.14, "couch": 0.13},
        {"person": 0.47, "bed": 0.16, "couch": 0.14, "cat": 0.12, "cup": 0.06},
        {"person": 0.60, "bed": 0.41, "potted plant": 0.11, "cat": 0.08},
    ]

    class Replay(vo.Objects):
        def __init__(self, seq):
            self.seq, self.i = list(seq), 0

        def detect(self, frame):
            d = self.seq[self.i]
            self.i += 1
            return {"objects": [{"label": k, "count": 1, "confidence": v}
                                for k, v in d.items()], "detect_ms": 70}

    res = Replay(real).detect_many([None] * len(real))
    kept = {i["label"] for i in res["objects"]}
    check("what is really there survives", kept == {"person", "bed"}, sorted(kept))
    check("...and the phantom pets do not",
          not ({"cat", "dog", "teddy bear"} & kept), sorted(kept))
    check("...nor the rest of the flicker",
          not ({"cup", "couch", "potted plant"} & kept), sorted(kept))

    # The whole point: 'bed' peaked at 0.41 and the old single-frame floor of
    # 0.50 would have thrown it away.
    bed = next(i for i in res["objects"] if i["label"] == "bed")
    check("a real object below the OLD 0.50 floor is now kept",
          bed["confidence"] < 0.50, bed)
    check("...because it was in every frame", bed["seen_in"] == "8/8", bed)
    check("the sentence matches", vo.describe(res) == "a person and a bed",
          vo.describe(res))

    # one glimpse is not a sighting
    flicker = [{"person": 0.9}] * 7 + [{"person": 0.9, "toaster": 0.45}]
    r2 = Replay(flicker).detect_many([None] * 8)
    check("one glimpse of a toaster is not a toaster",
          "toaster" not in {i["label"] for i in r2["objects"]},
          [i["label"] for i in r2["objects"]])

    # ...but something genuinely convincing once still counts
    once = [{"person": 0.9}] * 7 + [{"person": 0.9, "bottle": 0.72}]
    r3 = Replay(once).detect_many([None] * 8)
    check("a single CONVINCING sighting is kept",
          "bottle" in {i["label"] for i in r3["objects"]},
          [i["label"] for i in r3["objects"]])

    check("no frames is an error, not an empty room",
          vo.Objects().detect_many([]).get("error") is not None)

    # --- English, and not over-claiming --------------------------------------
    # Live on 2026-09-01 it said "I can see 2 beds and a person" (one bed, split
    # across two boxes in a few frames) and "2 persons and a bed". Both are the
    # same class of error: sounding confident about something wrong.
    check("people, not persons", vo.plural("person", 2) == "people")
    for word, want in (("mouse", "mice"), ("knife", "knives"), ("sheep", "sheep"),
                       ("couch", "couches"), ("bed", "beds"), ("cup", "cups")):
        check(f"...{word} -> {want}", vo.plural(word, 2) == want, vo.plural(word, 2))
    check("...and one of a thing keeps its own name", vo.plural("person", 1) == "person")

    two_people = {"objects": [{"label": "person", "count": 2, "confidence": 0.9},
                              {"label": "bed", "count": 1, "confidence": 0.5}]}
    check("the sentence reads like English",
          vo.describe(two_people) == "2 people and a bed", vo.describe(two_people))

    # one bed seen as two in a couple of frames must be reported as ONE
    split = [{"bed": (0.5, 1)}] * 6 + [{"bed": (0.5, 2)}] * 2

    class CountReplay(vo.Objects):
        def __init__(self, seq):
            self.seq, self.i = list(seq), 0

        def detect(self, frame):
            d = self.seq[self.i]
            self.i += 1
            return {"objects": [{"label": k, "count": c, "confidence": v}
                                for k, (v, c) in d.items()], "detect_ms": 70}

    r4 = CountReplay(split).detect_many([None] * 8)
    check("a bed split across two boxes is still one bed",
          r4["objects"][0]["count"] == 1, r4["objects"])

    # ...but two genuinely present are still two
    both = [{"person": (0.8, 2)}] * 7 + [{"person": (0.8, 1)}]
    r5 = CountReplay(both).detect_many([None] * 8)
    check("...while two people really there are counted as two",
          r5["objects"][0]["count"] == 2, r5["objects"])

    # --- counts only where they are reliable AND useful ----------------------
    # Live it still said "2 beds and a person" for a room with ONE bed: YOLOX
    # genuinely splits the bed across two boxes in most frames, so averaging
    # across frames cannot fix it. Furniture loses its count — "a bed" is the
    # whole message anyway — and people keep theirs, because how many people are
    # in the room is worth knowing and people separate cleanly.
    both_counted = {"objects": [{"label": "bed", "count": 2, "confidence": 0.5},
                                {"label": "person", "count": 2, "confidence": 0.9}]}
    check("furniture is not counted at him",
          vo.describe(both_counted) == "a bed and 2 people",
          vo.describe(both_counted))
    check("person is on the counted list", "person" in vo.COUNTED)
    check("...and bed is not", "bed" not in vo.COUNTED)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
