"""A `torchmcubes` that needs no compiler.

TripoSR's isosurface helper imports `torchmcubes`, which is a CUDA/C++ extension
installed from GitHub — a compiler and a toolchain, on a machine that has neither
and does not need them. scikit-image's marching cubes is pure Python over numpy,
already installed, and produces the same thing: vertices in grid-index space and
a triangle list.

Registered in `sys.modules` BEFORE `tsr` is imported, so the import inside
`tsr/models/isosurface.py` finds this instead of looking for the extension.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import torch
from skimage import measure


def marching_cubes(volume, isolevel: float = 0.0):
    """(verts, faces) in grid-index space, exactly as torchmcubes returns them."""
    vol = volume.detach().cpu().numpy().astype(np.float32)
    # `allow_degenerate=False` keeps zero-area triangles out of the result. They
    # are legal in a mesh and poison every downstream check — printcheck counts
    # them as defects and a slicer can choke on them.
    verts, faces, _normals, _values = measure.marching_cubes(
        vol, level=float(isolevel), allow_degenerate=False)
    return (torch.from_numpy(np.ascontiguousarray(verts)).float(),
            torch.from_numpy(np.ascontiguousarray(faces)).long())


def install() -> None:
    mod = types.ModuleType("torchmcubes")
    mod.marching_cubes = marching_cubes
    sys.modules["torchmcubes"] = mod
