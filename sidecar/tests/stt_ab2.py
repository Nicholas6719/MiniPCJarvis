"""STT bake-off round 2: faster-whisper base.en vs Parakeet TDT 0.6B v3 (onnx-asr, fp32 +
int8) vs Moonshine (onnx). Same 45 synthesized command clips + noise as stt_ab.py.
Run: python tests/stt_ab2.py"""
import os, sys, time, difflib, re
import numpy as np
from kokoro_onnx import Kokoro

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
NUMS = {"forty": "40", "twenty": "20", "fifteen hundred": "1500", "percent": "%", "dollars": "$"}


def norm(t):
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)            # glued words: "OpenYoutube" -> "Open Youtube"
    t = re.sub(r"(?<=[a-z])(?=[$\d])", " ", t)            # "under$1,500" -> "under $1,500"
    t = t.lower().replace("-", " ").replace("’", "'")
    t = re.sub(r"\$\s*1,?500", "1500", t); t = re.sub(r"\$1,500", "1500", t)
    for w, n in NUMS.items():
        t = t.replace(w, n)
    t = t.replace("%", " %").replace("$", " $")
    t = re.sub(r"youtube\s*\.?\s*com", "youtube dot com", t)
    t = re.sub(r"[^a-z0-9 %$']", " ", t)
    return t.split()


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
        a = s[idx].astype(np.float32) + rng.normal(0, 0.004, len(idx)).astype(np.float32)
        clips.append((p, a))
print(f"{len(clips)} clips")


def run(name, fn):
    fn(clips[0][1])  # warm
    lat, w, bad = [], [], []
    for p, a in clips:
        t0 = time.time(); hyp = fn(a) or ""; lat.append(time.time() - t0)
        e = wer(p, hyp); w.append(e)
        if e > 0:
            bad.append((p, hyp.strip()))
    print(f"{name:26} median {np.median(lat)*1000:5.0f} ms  p90 {np.percentile(lat, 90)*1000:5.0f} ms  "
          f"WER {np.mean(w)*100:4.1f}%  exact {sum(1 for x in w if x == 0)}/{len(w)}")
    for p, h in bad[:5]:
        print(f"      {p!r} => {h!r}")


which = sys.argv[1:] or ["whisper", "parakeet", "parakeet-int8", "moonshine"]
if "whisper" in which:
    from faster_whisper import WhisperModel
    m = WhisperModel("base.en", device="cpu", compute_type="int8", cpu_threads=4)
    run("faster-whisper base.en", lambda a: " ".join(s.text for s in m.transcribe(a, language="en", beam_size=1)[0]))
if "parakeet" in which:
    import onnx_asr
    pk = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3")
    run("parakeet-tdt-0.6b-v3 fp32", lambda a: pk.recognize(a, sample_rate=16000))
if "parakeet-int8" in which:
    import onnx_asr
    pk8 = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
    run("parakeet-tdt-0.6b-v3 int8", lambda a: pk8.recognize(a, sample_rate=16000))
if "moonshine" in which:
    import moonshine_onnx as mo
    tok = mo.load_tokenizer()
    for mn in ("moonshine/base", "moonshine/tiny"):
        try:
            mdl = mo.MoonshineOnnxModel(model_name=mn)
            run(f"moonshine {mn.split('/')[1]}", lambda a, mdl=mdl: " ".join(tok.decode_batch(mdl.generate(a[None, :].astype(np.float32)))))
        except Exception as e:
            print(f"moonshine {mn}: {type(e).__name__}: {str(e)[:100]}")
