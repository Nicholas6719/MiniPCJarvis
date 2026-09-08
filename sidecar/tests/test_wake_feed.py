"""The wake-word model is fed by one thread at a time, and a feeder that
finds it busy drops its block rather than queueing behind it.

2026-09-07, release 56: five worker threads were inside the detector's
predict() at once (the wake loop, and barge-in watchers that outlived their
replies), each call slower than the last as the model's streaming buffer
grew under them, and the sidecar burned 2.3 cores while "sleeping".

Run: python tests/test_wake_feed.py
"""
import os
import sys
import tempfile
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "wake.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeModel:
    """Counts how many predict() calls overlap; the real one corrupts."""
    def __init__(self):
        self.inside = 0
        self.worst = 0
        self.calls = 0
        self._lock = threading.Lock()

    def predict(self, frame):
        with self._lock:
            self.inside += 1
            self.worst = max(self.worst, self.inside)
            self.calls += 1
        time.sleep(0.01)
        with self._lock:
            self.inside -= 1
        return {"hey_jarvis": 0.1}

    def reset(self):
        pass


def main() -> int:
    from audio import wake as W
    ww = W.WakeWord()
    fake = FakeModel()
    ww._model = fake
    ww._ensure = lambda: fake          # never load openwakeword here

    block = np.zeros(W.CHUNK, dtype=np.float32)
    # eight threads hammering it, the way leaked watchers did
    results = []

    def feeder():
        for _ in range(20):
            results.append(ww.feed(block))
    ts = [threading.Thread(target=feeder) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    check("predict() never overlaps", fake.worst == 1, fake.worst)
    check("a busy detector drops the block instead of queueing", ww.dropped > 0, ww.dropped)
    check("...and says so with a score of nothing", all(r in (0.0, 0.1) for r in results))
    check("some blocks were still scored", fake.calls > 0, fake.calls)

    # a feeder that fell behind does not replay seconds of stale audio
    ww2 = W.WakeWord()
    fake2 = FakeModel()
    ww2._model = fake2
    ww2._ensure = lambda: fake2
    ww2.feed(np.zeros(W.CHUNK * 40, dtype=np.float32))        # 3.2 s in one go
    check("a backlog is trimmed to the newest quarter second", fake2.calls <= 4, fake2.calls)

    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "orchestrator.py"), encoding="utf-8").read()
    check("only one barge-in watcher at a time", "_barge_task" in src and "prev.cancel()" in src)
    check("only one wake loop at a time", "for old in (self._loop_task, self._wake_task)" in src)

    # ...AND TWO LISTENERS NEVER SHARE ONE. The lock above drops a block when
    # the model is busy, which is right for a duplicate feeder and fatal for a
    # different one: the wake loop won every block while JARVIS spoke and the
    # barge-in never fired (bargein_e2e, 2026-09-08).
    check("the barge-in has its own detector", W.barge is not W.wake)
    check("...with its own buffer and its own lock",
          W.barge._lock is not W.wake._lock and W.barge._buf is not W.wake._buf)
    check("...and the watcher uses it", "barge.feed" in src and "barge.threshold" in src)
    check("...and resets it when it fires", "barge.reset()" in src)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
