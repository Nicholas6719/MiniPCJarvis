"""The endpoint hears the pause as well as reading the words (2026-09-18).

Smart Turn v3 (pipecat-ai) says, from the waveform, whether a pause is the
end of a turn. Its verdict only MOVES the silence budget the capture loop
already enforces: a finished command that also sounds finished is cut at
0.20 s instead of 0.40; an unclear transcript that sounds finished at 0.40
instead of 0.90; the trailing-off rules keep the first say. Without the model
file everything is exactly as it was. Run: python tests/test_turn_model.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "turn.db"))
ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from audio import endpoint as E
    from audio import turn_model as T

    print("\n-- whisper features, without transformers --")
    f = T.features(np.zeros(16000, dtype=np.float32))
    check("shape is (1, 80, 800) for eight seconds at hop 160", f.shape == (1, 80, 800), f.shape)
    check("silence normalises to the floor, not NaN", np.isfinite(f).all() and f.min() >= -2.0 - 1e-3 and f.max() <= 2.0 + 1e-3, (f.min(), f.max()))
    t = np.linspace(0, 1, 16000, dtype=np.float32)
    tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    g = T.features(tone)
    check("one second of audio lands in the LAST 100 frames (left padding)",
          g[0, :, -50:].max() > g[0, :, :100].max() + 0.5, (g[0, :, -50:].max(), g[0, :, :100].max()))
    check("a 440 Hz tone lights a low mel bin, not a high one", g[0, :20, -50:].mean() > g[0, 60:, -50:].mean())
    long = T.features(np.random.default_rng(0).standard_normal(16000 * 12).astype(np.float32))
    check("more than eight seconds keeps the last eight", long.shape == (1, 80, 800))

    print("\n-- absent model = absent signal --")
    from config import config
    config.data.setdefault("wake", {})["turn_model_path"] = str(ROOT / "tests" / "no-such-model.onnx")
    check("predict returns None without the file", T.predict(np.zeros(16000, dtype=np.float32)) is None)
    check("available() is False without the file", not T.available())

    print("\n-- the budget, with and without the sound --")
    check("no probability: exactly as before (brain hit -> FAST)", E.budget_for("what time is it", True) == (E.FAST, "a command the brain recognises"))
    s, why = E.budget_for("what time is it", True, 0.95)
    check("finished by words AND sound -> SNAP 0.20", s == E.SNAP and "sounded finished" in why, (s, why))
    s, why = E.budget_for("the thing with the", False, 0.95)
    check("trailing off keeps the first say, whatever it sounded like", s == E.PATIENT, (s, why))
    s, why = E.budget_for("remind me to", True, 0.99)
    check("'remind me to' stays PATIENT even when the sound says done", s == E.PATIENT, (s, why))
    s, why = E.budget_for("blue seven pencils", False, 0.90)
    check("unclear by words, finished by sound -> FAST instead of NORMAL", s == E.FAST and "sounded finished" in why, (s, why))
    s, why = E.budget_for("blue seven pencils", False, 0.05)
    check("unclear by words, unfinished by sound -> PATIENT", s == E.PATIENT and "sounded unfinished" in why, (s, why))
    s, why = E.budget_for("blue seven pencils", False, 0.5)
    check("a middling probability changes nothing", s == E.NORMAL, (s, why))
    s, why = E.budget_for("who directed jaws?", False, 0.05)
    check("finished by words, unfinished by sound: the words win (FAST stays)", s == E.FAST, (s, why))

    print("\n-- decide() runs the model beside the transcription, off the loop --")
    src = (ROOT / "audio" / "endpoint.py").read_text(encoding="utf-8")
    check("in parallel with Parakeet", "asyncio.to_thread(turn_model.predict, audio)" in src and "await stt.transcribe(audio)" in src)
    check("...bounded, so a slow model never adds wait", "wait_for(turn_task, timeout=0.25)" in src)
    check("...only when the model is installed", "if turn_model.available() else None" in src)
    check("warmed at audio boot", '("turn model", _turn_warm)' in (ROOT / "orchestrator.py").read_text(encoding="utf-8"))
    check("switchable in config", config.get("wake", "turn_model", default=None) is True)

    class _Stt:
        async def transcribe(self, audio):
            return "what time is it"

    class _Brain:
        async def decide(self, text):
            return ("time", {}, 1.0)

    secs, why, text = asyncio.run(E.decide(np.zeros(16000, dtype=np.float32), _Stt(), _Brain()))
    check("without the file, decide() is the old decision", secs == E.FAST and text == "what time is it" and "[turn" not in why, (secs, why))

    print()
    if fails:
        print(f"FAILED: {len(fails)}: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
