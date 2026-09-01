"""Knowing that the face in front of the camera is HIS.

*"It needs to recognize me as me and people who it doesn't recognize as
persons, so it should say 'I see you, sir'... it needs to know who I am, so we
can teach it that."*

He teaches it by saying "remember my face": ten samples are taken over a couple
of seconds, each turned into a 128-number SFace embedding, and the embeddings —
NOT the pictures — are stored in %APPDATA%/JARVIS/face_profile.json. No image
of him is ever written to disk; a stored embedding cannot be turned back into
his face. "Forget my face" deletes the file.

Recognition: YuNet finds the face (the same detector presence already runs),
SFace aligns and embeds it, and cosine similarity against his stored samples
decides. The threshold is OpenCV's own documented one — 0.363, at which two
faces are the same identity with 99.8% accuracy — not a number I invented.

STILL NOT AUTHENTICATION. A photograph of him would pass this. It decides what
JARVIS calls the person it sees, and nothing more dangerous than that.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time

log = logging.getLogger("jarvis.identity")

MODEL = "face_recognition_sface_2021dec.onnx"
# cv2's documented decision point for SFace cosine similarity: >= this is the
# same person at 99.80% accuracy.
COSINE_SAME = 0.363
ENROLL_SAMPLES = 10
# He is recognised if his best stored sample agrees. Re-checked at most this
# often from the presence loop; the answer is cached between checks.
RECHECK_S = 2.0


def _profile_path() -> str:
    from config import APP_DIR
    return str(APP_DIR / "face_profile.json")


def _model_path() -> str | None:
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "models", MODEL),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", MODEL)):
        if os.path.exists(cand):
            return cand
    return None


class Identity:
    def __init__(self) -> None:
        self._rec = None
        self._unavailable = False
        self._lock = threading.Lock()
        self._profile: list | None = None      # list of embeddings, or None = not loaded
        self._who: str | None = None           # "him" | "unknown" | None (nobody/unchecked)
        self._checked_at = 0.0

    # ---------- the stored profile ----------

    def _load_profile(self) -> list:
        if self._profile is not None:
            return self._profile
        try:
            with open(_profile_path(), encoding="utf-8") as fh:
                data = json.load(fh)
            self._profile = [s for s in data.get("samples", []) if isinstance(s, list)]
            log.info("identity: profile loaded (%d samples)", len(self._profile))
        except FileNotFoundError:
            self._profile = []
        except Exception:
            log.exception("identity: profile unreadable; treating as absent")
            self._profile = []
        return self._profile

    @property
    def enrolled(self) -> bool:
        return bool(self._load_profile())

    def forget(self) -> bool:
        """Delete everything it knows about his face."""
        self._profile = []
        self._who = None
        try:
            os.remove(_profile_path())
            log.info("identity: profile deleted")
            return True
        except FileNotFoundError:
            return True
        except Exception:
            log.exception("identity: could not delete the profile")
            return False

    def _save_profile(self, samples: list) -> bool:
        try:
            with open(_profile_path(), "w", encoding="utf-8") as fh:
                json.dump({"name": "Nicholas", "saved_at": time.time(),
                           "samples": samples}, fh)
            self._profile = samples
            return True
        except Exception:
            log.exception("identity: could not save the profile")
            return False

    # ---------- the model ----------

    def _recognizer(self):
        if self._rec is not None or self._unavailable:
            return self._rec
        path = _model_path()
        if not path:
            self._unavailable = True
            log.error("identity: %s is missing", MODEL)
            return None
        try:
            import cv2
            self._rec = cv2.FaceRecognizerSF.create(path, "")
            log.info("identity: SFace ready")
        except Exception:
            self._unavailable = True
            log.exception("identity: SFace would not load")
        return self._rec

    def _embed(self, small_frame, face_row):
        """One aligned 128-d embedding from a YuNet face row. None on failure."""
        rec = self._recognizer()
        if rec is None:
            return None
        try:
            aligned = rec.alignCrop(small_frame, face_row)
            return rec.feature(aligned)
        except Exception:
            log.debug("identity: embed failed", exc_info=True)
            return None

    # ---------- recognition ----------

    def consider(self, small_frame, faces) -> None:
        """Called from the presence pass with YuNet's output. Never raises.

        Cheap by construction: it runs at most every RECHECK_S, only when a
        face is there, and SFace costs ~5 ms — so the camera loop never feels it.
        """
        try:
            now = time.time()
            if faces is None or len(faces) == 0:
                self._who = None
                return
            if now - self._checked_at < RECHECK_S and self._who is not None:
                return
            self._checked_at = now
            profile = self._load_profile()
            if not profile:
                self._who = "unknown"           # someone is there; nobody is enrolled
                return
            emb = self._embed(small_frame, faces[0])
            if emb is None:
                return                          # keep the previous answer
            import cv2
            import numpy as np
            rec = self._recognizer()
            best = max(rec.match(emb, np.array(s, dtype=np.float32),
                                 cv2.FaceRecognizerSF_FR_COSINE)
                       for s in profile)
            self._who = "him" if best >= COSINE_SAME else "unknown"
        except Exception:
            log.debug("identity check failed", exc_info=True)

    def check_once(self, small_frame, faces) -> tuple[str | None, float]:
        """One deliberate look, unthrottled. Returns (verdict, best_score).

        `consider()` is the wrong call for a confirmation: it answers at most
        every RECHECK_S and will happily hand back a cached verdict from two
        seconds ago, which is precisely the staleness that made "can you see me"
        claim to see a man who had left. A confirmation asks about NOW, so this
        shares the matching but keeps none of the caching, and touches none of
        the presence state.

        Verdicts: "him", "unknown", "no_face", or None when it could not tell
        (no model, no profile, embedding failed). None is not a match and must
        never be treated as one.
        """
        try:
            if faces is None or len(faces) == 0:
                return "no_face", 0.0
            profile = self._load_profile()
            if not profile:
                return None, 0.0                # nobody enrolled: cannot tell
            emb = self._embed(small_frame, faces[0])
            if emb is None:
                return None, 0.0
            import cv2
            import numpy as np
            rec = self._recognizer()
            if rec is None:
                return None, 0.0
            best = max(rec.match(emb, np.array(s, dtype=np.float32),
                                 cv2.FaceRecognizerSF_FR_COSINE)
                       for s in profile)
            return ("him" if best >= COSINE_SAME else "unknown"), float(best)
        except Exception:
            log.debug("identity one-shot check failed", exc_info=True)
            return None, 0.0

    def who(self) -> str | None:
        """"him", "unknown", or None when nobody is in frame / never checked."""
        return self._who

    def reset(self) -> None:
        self._who = None

    # ---------- enrollment ----------

    def enroll_from(self, samples: list) -> dict:
        """Store embeddings gathered by the enrollment tool. Replaces the profile."""
        good = [s for s in samples if s is not None]
        if len(good) < 3:
            return {"error": "I couldn't get a clear enough view of your face"}
        stored = [s.flatten().tolist() for s in good]
        if not self._save_profile(stored):
            return {"error": "the profile could not be saved"}
        self._who = "him"                       # he is the one in front of it
        return {"ok": True, "samples": len(stored)}


identity = Identity()
