"""What is actually in front of the camera. YOLOX, on demand only.

Phase 2b. "What do you see?" — one frame, eighty COCO classes, an answer.

ON DEMAND, NOT CONTINUOUS, and that is the whole design. Presence runs every
second because it costs 3 ms; this costs 71 ms a frame (measured here), which is
fine once when he asks and absurd thirty times a second forever. Nothing in this
file runs unless something asked it a question.

MODEL CHOICE, measured rather than assumed. YOLOX-S (35.9 MB) against NanoDet
(3.8 MB): YOLOX read the test image correctly at 71 ms — person 0.91, sports
ball 0.94 — and since a look happens once per question, latency was never the
constraint. Being wrong about what is in his room is the expensive failure, so
accuracy won and NanoDet was dropped.

The honest limits, which the spoken answer has to respect:
  * Eighty classes and no more. It knows "cup" and "laptop"; it does not know
    which cup, or whose laptop, and it will never know a thing that is not in
    the list.
  * A confidence is not a fact. Below the threshold it says nothing rather than
    guessing, because "I think I see a toaster" in a room with no toaster is
    worse than silence.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

log = logging.getLogger("jarvis.objects")

MODEL = "object_detection_yolox_2022nov.onnx"
INPUT = 640
CONF = 0.50          # below this it says nothing rather than guessing
NMS = 0.50
MAX_REPORTED = 8

# The 80 COCO classes, in the exact order the model emits them. Written out
# rather than assembled, because a single misplaced name here silently renames
# every object he is told about.
CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag",
    "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon",
    "bowl", "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch", "potted plant",
    "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]
assert len(CLASSES) == 80, f"COCO has 80 classes, this list has {len(CLASSES)}"


def _model_path() -> str | None:
    here = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for cand in (os.path.join(here, "models", MODEL),
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", MODEL)):
        if os.path.exists(cand):
            return cand
    return None


class Objects:
    def __init__(self) -> None:
        self._net = None
        self._grids = None
        self._strides = None
        self._lock = threading.Lock()
        self._unavailable = False

    # ---------- model ----------

    def _anchors(self, np):
        grids, expanded = [], []
        for stride in (8, 16, 32):
            h = w = INPUT // stride
            xv, yv = np.meshgrid(np.arange(h), np.arange(w))
            grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
            grids.append(grid)
            expanded.append(np.full((*grid.shape[:2], 1), stride))
        self._grids = np.concatenate(grids, 1)
        self._strides = np.concatenate(expanded, 1)

    def _load(self):
        if self._net is not None or self._unavailable:
            return self._net
        path = _model_path()
        if not path:
            self._unavailable = True
            log.error("objects: %s is missing", MODEL)
            return None
        try:
            import cv2
            import numpy as np
            self._net = cv2.dnn.readNet(path)
            self._anchors(np)
            log.info("objects: YOLOX ready (%s)", os.path.basename(path))
        except Exception:
            self._unavailable = True
            log.exception("objects: could not load the detector")
        return self._net

    # ---------- looking ----------

    def detect(self, frame) -> dict:
        """One look at one frame. Never raises."""
        net = self._load()
        if net is None:
            return {"error": "the object model is unavailable"}
        try:
            import cv2
            import numpy as np

            h, w = frame.shape[:2]
            # Letterbox: keep the aspect ratio and pad, rather than squashing the
            # room into a square — a stretched person stops looking like one.
            r = min(INPUT / h, INPUT / w)
            nh, nw = int(round(h * r)), int(round(w * r))
            padded = np.ones((INPUT, INPUT, 3), dtype=np.uint8) * 114
            padded[:nh, :nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)

            t0 = time.time()
            with self._lock:            # one inference at a time; this is not cheap
                # float32: the ONNX graph refuses CV_8U, and the reference
                # preprocess only transposes.
                blob = np.transpose(padded.astype(np.float32), (2, 0, 1))[np.newaxis]
                net.setInput(blob)
                outs = net.forward(net.getUnconnectedOutLayersNames())
            took = (time.time() - t0) * 1000.0

            dets = outs[0][0]
            dets[:, :2] = (dets[:, :2] + self._grids) * self._strides
            dets[:, 2:4] = np.exp(dets[:, 2:4]) * self._strides
            boxes = dets[:, :4]
            xywh = np.ones_like(boxes)
            xywh[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
            xywh[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
            xywh[:, 2:4] = boxes[:, 2:4]
            scores = dets[:, 4:5] * dets[:, 5:]
            best = np.amax(scores, axis=1)
            idx = np.argmax(scores, axis=1)
            keep = cv2.dnn.NMSBoxesBatched(xywh.tolist(), best.tolist(), idx.tolist(),
                                           CONF, NMS)
            if len(keep) == 0:
                return {"objects": [], "detect_ms": round(took, 1)}

            seen: dict[str, float] = {}
            counts: dict[str, int] = {}
            for i in keep:
                cls = int(idx[i])
                name = CLASSES[cls] if cls < len(CLASSES) else f"object {cls}"
                conf = float(best[i])
                seen[name] = max(seen.get(name, 0.0), conf)
                counts[name] = counts.get(name, 0) + 1
            items = [{"label": k, "count": counts[k],
                      "confidence": round(v, 2)}
                     for k, v in sorted(seen.items(), key=lambda kv: -kv[1])]
            return {"objects": items[:MAX_REPORTED], "detect_ms": round(took, 1)}
        except Exception:
            log.exception("object detection failed")
            return {"error": "the look failed"}


objects = Objects()


def describe(res: dict) -> str:
    """The detections, as a sentence he would want to hear.

    Deliberately plain. The model knows eighty nouns; dressing that up as
    understanding would be a lie, and he would find the edge of it in a minute.
    """
    if res.get("error"):
        return ""
    items = res.get("objects") or []
    if not items:
        return "nothing I recognise"
    parts = []
    for it in items[:5]:
        label, n = it["label"], it.get("count", 1)
        parts.append(f"{n} {label}s" if n > 1 else
                     ("an " if label[0] in "aeiou" else "a ") + label)
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]
