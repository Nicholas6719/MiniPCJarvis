"""How long does cutting the background out actually take, and on what?

It is 20 of the 36 seconds of a warm tier-3 run — more than the model that does
the actual reconstruction — so it is the thing worth measuring properly.
"""
import sys
import time

import rembg
from PIL import Image

IMG = Image.open(sys.argv[1] if len(sys.argv) > 1 else "probe.png").convert("RGB")
DML = ["DmlExecutionProvider", "CPUExecutionProvider"]
CPU = ["CPUExecutionProvider"]


def bench(model, providers, n=2):
    try:
        t0 = time.time()
        s = rembg.new_session(model, providers=providers)
        load = time.time() - t0
        rembg.remove(IMG, session=s)             # warm
        t0 = time.time()
        for _ in range(n):
            rembg.remove(IMG, session=s)
        return load, (time.time() - t0) / n
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"[:110]


for model in ("bria-rmbg", "u2netp", "u2net", "silueta"):
    for label, prov in (("cpu", CPU), ("780M", DML)):
        load, per = bench(model, prov)
        if load is None:
            print(f"{model:12s} {label:5s}  unavailable: {per}")
        else:
            print(f"{model:12s} {label:5s}  load {load:5.1f}s   per image {per:6.2f}s")
    sys.stdout.flush()
