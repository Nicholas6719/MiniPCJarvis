# model3d — tiers 3 and 4, outside the app

The hologram's two heavy tiers live here rather than in the sidecar:

| tier | what it does | measured |
|---|---|---|
| 3 | a photograph → a 3D mesh (TripoSR) | **33 s** end to end through the sidecar |
| 4 | a description → a reference picture → tier 3 | **~55 s** |

They are **not bundled and must not be.** They need PyTorch and about 1.7 GB of
weights; the sidecar is already 980 MB and ships through PyInstaller. So they get
their own environment under `C:\AI\model3d`, invoked as a subprocess — exactly
the shape `llm.server_binary` uses to point at `C:\AI\llama.cpp`. If the install
is missing, those tiers say so in a sentence and specifically do **not** fall
back to another technique.

Install with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_model3d.ps1
```

## What was measured, and why it is built this way

**The Radeon 780M is worth about 4x on real convolutional work.** An earlier
`torch-directml` matmul benchmark said 1.3x and that was wrong — a matmul is
memory-bound and flatters eight Zen 4 cores. Through ONNX Runtime's DirectML
provider, on the models this project already ships:

```
yolox  1x3x640x640   CPU 65.3 ms   780M 17.3 ms   3.76x
sface  1x3x112x112   CPU  7.9 ms   780M  4.7 ms   1.67x
```

**Background removal was 20 of the first 36 seconds, and almost all of it was a
bad default.** rembg 2.x defaults to `bria-rmbg`, a much heavier transformer:

```
bria-rmbg   CPU 11.16 s/image   780M 5.51 s     (the default)
u2net       CPU  0.28 s/image   780M 0.06 s     (what TripoSR was built around)
u2netp      CPU  0.13 s/image   780M 0.04 s
```

`photo_to_mesh.py` therefore asks for `u2net` explicitly and passes
`DmlExecutionProvider` explicitly — rembg's own provider selection checks CUDA,
ROCm and OpenVINO and then falls through to CPU, so it never uses the iGPU on
this machine. That took a warm run from 36.6 s to 18.8 s.

**Marching cubes without a compiler.** TripoSR imports `torchmcubes`, a CUDA/C++
extension. `_mcubes_shim.py` registers a scikit-image implementation under that
name before `tsr` is imported, so no toolchain is needed.

**Tier 4 is tier 3 with a reference picture in front of it**, deliberately.
Direct text-to-3D (Shap-E and its kin) is another 1.3 GB, minutes of CPU here,
and produces blobs. Finding a picture and reconstructing that reuses the image
search JARVIS already has and the model already installed. It is only honest
because it says so: what comes back is a mesh of a picture of a duck.

## Files

- `photo_to_mesh.py` — the worker. Takes an image and an output STL, prints JSON
  on its last stdout line. Scales the result into millimetres and checks the
  winding, because an inside-out mesh is watertight, consistent, negative-volume
  and prints as the complement of what was asked for.
- `_mcubes_shim.py` — the compiler-free marching cubes.
- `bench_ep.py` — CPU vs 780M on the project's own ONNX models.
- `bench_rembg.py` — background-removal models and providers.
