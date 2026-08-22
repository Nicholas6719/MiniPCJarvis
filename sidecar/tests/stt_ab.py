"""A/B faster-whisper models on synthesized voice commands (Kokoro, several voices,
with light noise) for speed and word accuracy. Run: python tests/stt_ab.py"""
import os, sys, time, difflib, re
import numpy as np
from kokoro_onnx import Kokoro
from faster_whisper import WhisperModel

d = os.path.expandvars(r"%APPDATA%\JARVIS\voices\kokoro")
k = Kokoro(os.path.join(d, "kokoro-v1.0.onnx"), os.path.join(d, "voices-v1.0.bin"))

PHRASES = [
    "what time is it", "set the volume to forty percent", "open spotify for me",
    "remind me in twenty minutes to call dad", "what's the weather in boston right now",
    "show me a picture of saturn", "take a screenshot and save it to my desktop",
    "search the web for the best gaming laptop under fifteen hundred dollars",
    "remember that i like my coffee black", "how many legs does a spider have",
    "close notepad please", "what windows do i have open", "mute the speakers",
    "tell me a fun fact about octopuses", "open youtube dot com",
]
VOICES = ["am_michael", "af_sarah", "bm_george"]


def norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower().replace("-", " ")).split()


def wer(ref, hyp):
    r, h = norm(ref), norm(hyp)
    sm = difflib.SequenceMatcher(a=r, b=h)
    errs = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")
    return errs / max(1, len(r))


clips = []
rng = np.random.default_rng(0)
for v in VOICES:
    for p in PHRASES:
        s, sr = k.create(p, voice=v, speed=1.05)
        idx = np.linspace(0, len(s) - 1, int(len(s) * 16000 / sr)).astype(int)
        a = s[idx].astype(np.float32)
        a = a + rng.normal(0, 0.004, len(a)).astype(np.float32)   # faint room noise
        clips.append((p, a))
print(f"{len(clips)} clips")

for name in sys.argv[1:] or ["base.en", "small.en"]:
    m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=4)
    m.transcribe(clips[0][1], language="en", beam_size=1)   # warm
    lat, w = [], []
    for p, a in clips:
        t0 = time.time()
        segs, _ = m.transcribe(a, language="en", beam_size=1, vad_filter=False)
        hyp = " ".join(s.text for s in segs)
        lat.append(time.time() - t0)
        w.append(wer(p, hyp))
    print(f"{name:9} median {np.median(lat)*1000:.0f} ms  p90 {np.percentile(lat, 90)*1000:.0f} ms  "
          f"WER {np.mean(w)*100:.1f}%  exact {sum(1 for x in w if x == 0)}/{len(w)}")
