"""Recognising HIM is a claim about a person; it has to be earned and honest.

Phase 3 of the camera work. *"It needs to recognize me as me and people who it
doesn't recognize as persons... it needs to know who I am, so we can teach it
that."* He teaches it with "learn my face"; SFace embeddings — never images —
go to face_profile.json, and cosine >= 0.363 (OpenCV's own documented 99.8%
decision point, not a number I invented) says whether a face is his.

What is gated here:
  * nobody enrolled -> every face is "unknown", never "him";
  * his own embedding matches, an orthogonal one does not;
  * enrollment refuses to store a profile from too few clear samples;
  * "forget my face" really deletes it;
  * the routing guard: "remember my face" must NOT become a stored fact;
  * and what he is TOLD, name and all — the sentence is the feature.

Offline apart from loading the local SFace model file. No camera.
Run: python tests/test_identity.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "ident.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    import numpy as np

    import vision_identity as vi

    # keep the profile in a scratch directory, never his real one
    tmp = tempfile.mkdtemp()
    vi._profile_path = lambda: os.path.join(tmp, "face_profile.json")

    ident = vi.Identity()
    check("nothing is enrolled to begin with", ident.enrolled is False)
    check("...and nobody has been seen", ident.who() is None)

    # a face appears with no profile stored: it is a person, never HIM
    ident._embed = lambda small, row: np.ones((1, 128), dtype=np.float32)
    ident._checked_at = 0.0
    ident.consider(object(), [[0] * 15])
    check("with no profile, a face is 'unknown', never 'him'",
          ident.who() == "unknown", ident.who())

    # --- enrollment ----------------------------------------------------------
    check("too few clear samples are refused",
          vi.Identity().enroll_from([None, None, np.ones((1, 128), np.float32)])
          .get("error") is not None)

    his = np.random.RandomState(7).rand(1, 128).astype(np.float32)
    res = ident.enroll_from([his] * 5)
    check("a real enrollment stores", res.get("ok") is True, res)
    check("...and he is immediately himself", ident.who() == "him")
    check("...and the profile survives a fresh instance",
          vi.Identity().enrolled is True)

    # --- recognition against the REAL SFace matcher --------------------------
    fresh = vi.Identity()
    if fresh._recognizer() is None:
        check("SFace model available for the match tests", False,
              "model missing — the frozen build would lose recognition")
    else:
        fresh._embed = lambda small, row: his          # the same face again
        fresh._checked_at = 0.0
        fresh.consider(object(), [[0] * 15])
        check("his own embedding is recognised as him", fresh.who() == "him",
              fresh.who())

        stranger = vi.Identity()
        # orthogonal to his vector: cosine ~0, far below 0.363
        other = np.random.RandomState(99).rand(1, 128).astype(np.float32)
        other -= ((other @ his.T).item() / (his @ his.T).item()) * his
        stranger._embed = lambda small, row: other.astype(np.float32)
        stranger._checked_at = 0.0
        stranger.consider(object(), [[0] * 15])
        check("a different face is 'unknown', not waved through",
              stranger.who() == "unknown", stranger.who())

    # empty frame clears the answer
    ident._checked_at = 0.0
    ident.consider(object(), None)
    check("nobody in frame means who() is None again", ident.who() is None)

    # --- forgetting ----------------------------------------------------------
    check("forget deletes the profile", ident.forget() is True)
    check("...and it is really gone", vi.Identity().enrolled is False)
    check("forgetting twice is harmless", ident.forget() is True)

    # --- the identity check may never raise into the camera ------------------
    broken = vi.Identity()
    broken._embed = lambda small, row: (_ for _ in ()).throw(RuntimeError("boom"))
    broken._checked_at = 0.0
    try:
        broken.consider(object(), [[0] * 15])
        ok = True
    except Exception:
        ok = False
    check("a broken embedder never raises into the capture thread", ok)

    # --- routing: "remember my face" is enrollment, not a fact ---------------
    from brain.skills import slots_remember
    check("'remember my face' is refused by the memory skill",
          slots_remember("remember my face") is None)
    check("'remember what i look like' too",
          slots_remember("remember what i look like") is None)
    check("...while a real fact is still remembered",
          slots_remember("remember that i drink my coffee black")
          == {"content": "i drink my coffee black"})

    # --- what he hears -------------------------------------------------------
    from brain.skills import say_learn_face
    # NOT word for word. "I'll know you from now on", said to a face it already
    # knew, is indistinguishable from having done nothing — which is exactly
    # what he asked about. What matters is that he is told it worked and given
    # a number that could only come from actually looking.
    first = say_learn_face({}, {"ok": True, "samples": 10, "replaced": False})
    check("success is confirmed in his voice",
          first.startswith("Done, sir") and "10" in first, first)
    again = say_learn_face({}, {"ok": True, "samples": 10, "replaced": True})
    check("...and re-learning says it REPLACED what it had",
          "replaced" in again.lower(), again)
    said = say_learn_face({}, {"error": "I couldn't get a clear enough view of your face"})
    check("failure says what to do about it", "try again" in said, said)

    from vision_objects import describe
    check("describe names him when identity says so",
          describe({"objects": [{"label": "person", "count": 1, "confidence": .9},
                                {"label": "bottle", "count": 1, "confidence": .6}]},
                   who="him") == "you and a bottle",
          describe({"objects": [{"label": "person", "count": 1, "confidence": .9},
                                {"label": "bottle", "count": 1, "confidence": .6}]},
                   who="him"))
    check("...himself first even when something else scored higher",
          describe({"objects": [{"label": "bottle", "count": 1, "confidence": .9},
                                {"label": "person", "count": 1, "confidence": .6}]},
                   who="him").startswith("you"))
    check("...two people with him in front",
          describe({"objects": [{"label": "person", "count": 2, "confidence": .9}]},
                   who="him") == "you and one other person")
    check("...and a stranger stays 'a person'",
          describe({"objects": [{"label": "person", "count": 1, "confidence": .9}]},
                   who="unknown") == "a person")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
