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

    many = {"objects": [{"label": "person", "count": 1, "confidence": 0.9},
                        {"label": "laptop", "count": 1, "confidence": 0.8},
                        {"label": "cup", "count": 2, "confidence": 0.7}]}
    check("several, counted and joined",
          vo.describe(many) == "a person, a laptop and 2 cups", vo.describe(many))

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

    # --- the confidence floor exists and is not zero -------------------------
    check("there is a confidence floor", 0.3 < vo.CONF <= 0.7, vo.CONF)
    check("...and a cap on how much it will list", 3 <= vo.MAX_REPORTED <= 12,
          vo.MAX_REPORTED)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
