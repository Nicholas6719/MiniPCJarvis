"""He should not sit through a silence between every sentence.

`_speaker_worker` used to be strictly serial: synthesize a sentence, play it,
and only then start synthesizing the next one. So every boundary in a
multi-sentence reply was dead air — roughly 0.6-0.9 s of Kokoro working while he
listened to nothing. A three-sentence answer paid it twice, and it is the single
largest avoidable delay in a reply.

Synthesis now runs ahead of playback into a bounded queue. This test asserts the
property rather than the shape of the code: with synthesis and playback both
faked at known durations, a pipelined worker must finish a three-sentence reply
close to `synth + 3*play`, not `3*(synth + play)` — and sentence two must START
synthesizing before sentence one has finished playing.

Offline: no model, no audio device. Run: python tests/test_speech_pipeline.py
"""
import asyncio
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "pipe.db"))

fails = []
SYNTH_S = 0.30      # what Kokoro costs per sentence
PLAY_S = 0.40       # how long that sentence takes to speak


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def run(sentences):
    """Drive the real _speaker_worker with fake synthesis and fake speakers."""
    import numpy as np

    import orchestrator as omod
    from orchestrator import orchestrator as orc

    events = []          # (what, sentence, t)
    t0 = time.time()

    class FakeTTS:
        sample_rate = 24000
        idle = asyncio.Event()

        async def synthesize_stream(self, sentence, cancel):
            events.append(("synth_start", sentence, time.time() - t0))
            await asyncio.sleep(SYNTH_S)
            events.append(("synth_done", sentence, time.time() - t0))
            yield np.zeros(8, dtype=np.float32)

    class FakeSpeaker:
        async def play_chunk(self, chunk, rate):
            await asyncio.sleep(PLAY_S)
            events.append(("played", None, time.time() - t0))

    class FakeBus:
        async def emit(self, *a, **k):
            if a and a[0] == "speaking":
                events.append(("speaking", k.get("text"), time.time() - t0))

    class FakeWake:
        def reset(self):
            pass

    real = (omod.tts, omod.speaker, omod.bus, omod.wake, orc.sm.to,
            orc._barge_in_watch)
    omod.tts, omod.speaker, omod.bus, omod.wake = (
        FakeTTS(), FakeSpeaker(), FakeBus(), FakeWake())

    async def no_transition(*a, **k):
        return None

    async def idle_forever():
        await asyncio.sleep(3600)

    orc.sm.to = no_transition
    orc._barge_in_watch = idle_forever
    orc.remote_turn = False
    orc._speak_cancel.clear()

    q: asyncio.Queue = asyncio.Queue()
    for s in sentences:
        q.put_nowait(s)
    q.put_nowait(None)

    try:
        started = time.time()
        await asyncio.wait_for(orc._speaker_worker(q), timeout=30)
        elapsed = time.time() - started
    finally:
        (omod.tts, omod.speaker, omod.bus, omod.wake, orc.sm.to,
         orc._barge_in_watch) = real
    return elapsed, events


def main() -> int:
    sentences = ["One.", "Two.", "Three."]
    elapsed, events = asyncio.run(run(sentences))

    serial = len(sentences) * (SYNTH_S + PLAY_S)          # the old behaviour
    pipelined = SYNTH_S + len(sentences) * PLAY_S          # the best possible
    print(f"  (serial would be {serial:.2f}s, pipelined ideal {pipelined:.2f}s, "
          f"measured {elapsed:.2f}s)")

    check("every sentence was spoken",
          len([e for e in events if e[0] == "played"]) == len(sentences),
          [e for e in events if e[0] == "played"])
    check("...in order",
          [e[1] for e in events if e[0] == "speaking"] == sentences,
          [e[1] for e in events if e[0] == "speaking"])

    # The property that matters: it must be much closer to pipelined than serial.
    midpoint = (serial + pipelined) / 2
    check("the reply is pipelined, not serial", elapsed < midpoint,
          f"{elapsed:.2f}s vs serial {serial:.2f}s / ideal {pipelined:.2f}s")

    # And the direct evidence: sentence two starts synthesizing before sentence
    # one has finished playing.
    synth2 = next(e[2] for e in events if e[0] == "synth_start" and e[1] == "Two.")
    play1 = next(e[2] for e in events if e[0] == "played")
    check("sentence two is synthesized while sentence one is still playing",
          synth2 < play1, f"synth2 at {synth2:.2f}s, first play ended {play1:.2f}s")

    # A one-sentence reply must not be made slower by any of this.
    elapsed1, _ = asyncio.run(run(["Only one."]))
    check("a single sentence is not delayed by the lookahead",
          elapsed1 < SYNTH_S + PLAY_S + 0.25, f"{elapsed1:.2f}s")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
