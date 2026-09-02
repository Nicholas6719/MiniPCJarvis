"""TIER 3: a photograph to a 3D mesh, as a subprocess.

Invoked by the sidecar as

    python photo_to_mesh.py <image> <output.stl> [--resolution N] [--size-mm N]

and its LAST line of stdout is JSON. Everything before that is progress noise
from the model, which belongs in the log rather than in an answer.

WHY A SUBPROCESS AND NOT AN IMPORT. This needs PyTorch and about 1.7 GB of
weights. The sidecar is already 980 MB and bundles with PyInstaller; putting
torch inside it would roughly triple the app to serve one tier. So it lives here,
in its own environment, exactly the way `llm.server_binary` points at llama.cpp —
and if this directory is missing, that tier simply says so.

THREE THINGS THIS DOES THAT THE UPSTREAM SCRIPT DOES NOT.

  * MARCHING CUBES WITHOUT A COMPILER. Upstream imports `torchmcubes`, a CUDA/C++
    extension. `_mcubes_shim` registers a scikit-image implementation under that
    name before `tsr` is imported.
  * IT COMES OUT IN MILLIMETRES. TripoSR works in a normalised space, so an
    unscaled mesh measures about two millimetres across and every print check
    downstream correctly calls it a sliver. It is scaled to a real size here.
  * THE WINDING IS CHECKED. A mesh whose faces are inside out is watertight and
    consistent and has NEGATIVE volume, and a slicer will happily print the
    complement of what he asked for.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import _mcubes_shim  # noqa: E402

_mcubes_shim.install()
sys.path.insert(0, os.path.join(HERE, "TripoSR"))

import numpy as np  # noqa: E402
import torch  # noqa: E402
import trimesh  # noqa: E402
from PIL import Image  # noqa: E402

_MODEL = None


def log(*a):
    print(*a, file=sys.stdout, flush=True)


def load_model(chunk: int = 8192):
    global _MODEL
    if _MODEL is None:
        from tsr.system import TSR
        t0 = time.time()
        _MODEL = TSR.from_pretrained("stabilityai/TripoSR",
                                     config_name="config.yaml",
                                     weight_name="model.ckpt")
        _MODEL.renderer.set_chunk_size(chunk)
        _MODEL.to("cpu")
        log(f"model loaded in {time.time() - t0:.1f}s")
    return _MODEL


_BG_SESSION = None


def bg_session():
    """The background remover — the right model, on the right device.

    MEASURED, because this step was 20 of the 36 seconds of a warm run and it
    turned out to be almost entirely the wrong default:

        bria-rmbg   CPU 11.16 s/image   780M 5.51 s      (rembg 2.x default)
        u2net       CPU  0.28 s/image   780M 0.06 s
        u2netp      CPU  0.13 s/image   780M 0.04 s

    `bria-rmbg` is a far heavier transformer and forty times the cost for a job
    that is one silhouette. `u2net` is also the model TripoSR was built around,
    so this is the correct choice as well as the fast one.

    And DirectML explicitly: rembg's own provider selection checks for CUDA,
    ROCm and OpenVINO and then falls through to CPU — it never considers
    DirectML, so on this machine it would leave the iGPU idle.
    """
    global _BG_SESSION
    if _BG_SESSION is None:
        import rembg
        for providers in (["DmlExecutionProvider", "CPUExecutionProvider"],
                          ["CPUExecutionProvider"]):
            try:
                _BG_SESSION = rembg.new_session("u2net", providers=providers)
                log(f"background remover on {providers[0]}")
                break
            except Exception as e:
                log(f"{providers[0]} unavailable ({type(e).__name__}), falling back")
    return _BG_SESSION


def prepare(path: str, no_bg: bool = False):
    """Background out, object centred. TripoSR is trained on cut-out objects and
    gives markedly worse results on a photo with a room still in it."""
    from tsr.utils import remove_background, resize_foreground
    img = Image.open(path).convert("RGB")
    if no_bg:
        return img.resize((512, 512))
    session = bg_session()
    img = remove_background(img, session)
    img = resize_foreground(img, 0.85)
    # Composite onto grey: the model expects three channels, and a black or white
    # background bleeds into the silhouette at the edges.
    arr = np.array(img).astype(np.float32) / 255.0
    if arr.shape[-1] == 4:
        arr = arr[:, :, :3] * arr[:, :, 3:4] + 0.5 * (1 - arr[:, :, 3:4])
    return Image.fromarray((arr * 255.0).astype(np.uint8))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out")
    ap.add_argument("--resolution", type=int, default=192,
                    help="marching cubes grid; 256 is upstream's default and "
                         "roughly twice the work of 192 for a little more detail")
    ap.add_argument("--size-mm", type=float, default=60.0)
    ap.add_argument("--chunk", type=int, default=8192)
    ap.add_argument("--no-bg-removal", action="store_true")
    a = ap.parse_args()

    t0 = time.time()
    try:
        model = load_model(a.chunk)
        img = prepare(a.image, a.no_bg_removal)
        log(f"image ready at {time.time() - t0:.1f}s")

        with torch.no_grad():
            codes = model([img], device="cpu")
        log(f"scene code at {time.time() - t0:.1f}s")

        meshes = model.extract_mesh(codes, has_vertex_color=False,
                                    resolution=a.resolution)
        mesh = meshes[0]
        log(f"mesh at {time.time() - t0:.1f}s: {len(mesh.faces)} faces")

        m = trimesh.Trimesh(vertices=np.asarray(mesh.vertices),
                            faces=np.asarray(mesh.faces), process=True)
        m.remove_unreferenced_vertices()
        # INSIDE OUT is watertight, consistent, and negative-volume — and a
        # slicer will print the complement of what he asked for.
        if m.is_watertight and m.volume < 0:
            m.invert()
            log("normals were inside out; flipped")

        # Into millimetres. Unscaled this is about two units across and every
        # print check downstream correctly calls it a sliver.
        extent = float(np.max(m.extents)) or 1.0
        m.apply_scale(a.size_mm / extent)
        m.apply_translation(-m.bounds[0])          # sit it on the bed at the origin

        m.export(a.out)
        took = time.time() - t0
        out = {"ok": True, "stl": a.out, "seconds": round(took, 1),
               "triangles": int(len(m.faces)),
               "size_mm": [round(float(v), 2) for v in m.extents],
               "watertight": bool(m.is_watertight),
               "resolution": a.resolution}
    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stdout)
        out = {"error": f"{type(e).__name__}: {e}"[:300]}
    print(json.dumps(out), flush=True)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
