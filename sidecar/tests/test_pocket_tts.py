"""Pocket TTS worker protocol, gated without Pocket TTS.

The worker runs under the C:\\AI interpreter in production; here it runs under
the sidecar's own with `--fake` (a tone generator behind the same protocol),
so what is gated is the part that is ours: the port handshake, the framing,
streaming before the utterance is finished, cancel by hanging up, the tempo
post-process, and the sidecar-side provider reading it all back.

Run: python tests/test_pocket_tts.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "pocket.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import numpy as np
    from audio import tts as T
    from config import config

    worker = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "audio", "pocket_worker.py")
    # THE BUNDLE MUST CARRY THE FILE. The worker is run by another interpreter,
    # so it has to exist on disk in the install; the first release without this
    # line spawned a python that could not open it, on every sentence, and
    # JARVIS spoke with the fallback voice all evening.
    spec = open(os.path.join(os.path.dirname(worker), "..", "jarvis-sidecar.spec"),
                encoding="utf-8").read()
    check("the spec ships pocket_worker.py as a data file",
          '("audio/pocket_worker.py", "audio")' in spec)
    from audio.tts import PocketTTS
    check("the provider finds the worker beside itself", PocketTTS.worker_path() is not None)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, worker, "--fake", stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        line = (await asyncio.wait_for(proc.stdout.readline(), 30)).decode().strip()
        check("the worker announces its port", line.startswith("PORT "), line)
        port = int(line.split()[1])

        # The provider, pointed at the fake worker it did not start.
        p = T.PocketTTS()
        p._proc, p._port = proc, port
        config.data.setdefault("tts", {})
        config.data["tts"].update({"voice": "george", "tempo": 1.0, "polish": False})

        cancel = asyncio.Event()
        chunks = []
        t0 = time.time()
        first = None
        async for c in p.synthesize_stream("Good evening, sir. The suit is ready.", cancel):
            if first is None:
                first = time.time() - t0
            chunks.append(c)
        total = sum(len(c) for c in chunks) / p.sample_rate
        check("audio streams back as float32", chunks and chunks[0].dtype == np.float32)
        check("...in small chunks, not one lump", len(chunks) > 5, len(chunks))
        check("...the first well before the end", first is not None and first < 0.5, first)
        check("...and adds up to the utterance", 1.5 < total < 4.0, total)

        # Tempo: 1.10 is ten percent more audio.
        config.data["tts"]["tempo"] = 1.10
        slow = sum([len(c) async for c in p.synthesize_stream(
            "Good evening, sir. The suit is ready.", asyncio.Event())]) / p.sample_rate
        check("tempo above one is slower", abs(slow / total - 1.10) < 0.03, slow / total)
        config.data["tts"]["tempo"] = 1.0

        # Polish is stateful across chunks and must not blow up or clip.
        config.data["tts"]["polish"] = True
        pol = [c async for c in p.synthesize_stream("Good evening, sir.", asyncio.Event())]
        check("polish keeps the signal in range",
              pol and max(float(np.max(np.abs(c))) for c in pol) <= 1.0)
        config.data["tts"]["polish"] = False

        # Cancel: set the flag after the first chunk; the stream must stop
        # promptly and the worker must survive to serve the next request.
        cancel = asyncio.Event()
        got = 0
        async for c in p.synthesize_stream("A much longer sentence that would take a while "
                                           "to finish speaking in full, sir.", cancel):
            got += 1
            cancel.set()
        check("cancel stops the stream after the chunk in hand", got == 1, got)
        again = [c async for c in p.synthesize_stream("Yes?", asyncio.Event())]
        check("...and the worker is still serving", len(again) >= 1)

        # A different voice is a different tone (the fake keys pitch on the name).
        config.data["tts"]["voice"] = "paul"
        a = np.concatenate([c async for c in p.synthesize_stream("Yes?", asyncio.Event())])
        config.data["tts"]["voice"] = "george"
        b = np.concatenate([c async for c in p.synthesize_stream("Yes?", asyncio.Event())])
        check("the voice is passed through", not np.allclose(a[:2000], b[:2000]))

        # The router routes by voice name and falls back by installation.
        config.data["tts"].update({"engine": "pocket", "voice": "george",
                                   "pocket_python": r"C:\no\such\python.exe"})
        r = T.TTSRouter()
        check("pocket not installed -> kokoro, not a crash", r._active() is r.kokoro)
        config.data["tts"]["pocket_python"] = sys.executable
        check("pocket installed + pocket voice -> pocket", r._active() is r.pocket)
        config.data["tts"]["voice"] = "bm_daniel"
        check("a kokoro voice name still routes to kokoro", r._active() is r.kokoro)
        config.data["tts"]["voice"] = "en_GB-alan-medium"
        check("a piper voice name still routes to piper", r._active() is r.piper)
    finally:
        proc.kill()
        await proc.wait()

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
