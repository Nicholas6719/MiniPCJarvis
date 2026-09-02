"""Is the Radeon 780M worth using for neural inference on this machine?

Measured on the ONNX models this project ALREADY ships and runs — a real
convnet with real weights — rather than on a matmul microbenchmark, which is
what the first attempt used and which flatters neither backend honestly.
"""
import sys
import time

import numpy as np
import onnxruntime as ort

MODELS = {
    "yolox (36 MB detector)":
        r"C:\Users\nicho\Documents\Coding_Projects\JARVIS\sidecar\models"
        r"\object_detection_yolox_2022nov.onnx",
    "sface (39 MB embedder)":
        r"C:\Users\nicho\Documents\Coding_Projects\JARVIS\sidecar\models"
        r"\face_recognition_sface_2021dec.onnx",
}


def bench(path, provider, n=12):
    so = ort.SessionOptions()
    so.log_severity_level = 3
    try:
        s = ort.InferenceSession(path, so, providers=[provider])
    except Exception as e:
        return None, str(e)[:80]
    inp = s.get_inputs()[0]
    shape = [d if isinstance(d, int) else 1 for d in inp.shape]
    x = np.random.rand(*shape).astype(np.float32)
    try:
        for _ in range(3):
            s.run(None, {inp.name: x})
        t0 = time.time()
        for _ in range(n):
            s.run(None, {inp.name: x})
        return (time.time() - t0) / n * 1000.0, shape
    except Exception as e:
        return None, str(e)[:80]


print("providers:", ort.get_available_providers())
for name, path in MODELS.items():
    cpu, shp = bench(path, "CPUExecutionProvider")
    dml, _ = bench(path, "DmlExecutionProvider")
    if cpu is None:
        print(f"{name}: cpu failed - {shp}")
        continue
    if dml is None:
        print(f"{name}: input {shp}  CPU {cpu:.1f} ms   DML failed - {_}")
        continue
    print(f"{name}: input {shp}")
    print(f"    CPU {cpu:8.1f} ms")
    print(f"    780M {dml:7.1f} ms    speedup {cpu / dml:5.2f}x")
sys.stdout.flush()
