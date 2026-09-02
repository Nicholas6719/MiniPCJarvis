# Install tiers 3 and 4 — photo-to-mesh, and text by way of a reference picture.
#
# These live OUTSIDE the app on purpose: PyTorch plus about 1.7 GB of TripoSR
# weights against a sidecar that is already 980 MB and ships through PyInstaller.
# Same shape as llm.server_binary pointing at C:\AI\llama.cpp. Without this the
# two tiers simply say they are not installed; nothing else changes.
#
# Reproducible on purpose. The first version of this lived only in C:\AI\model3d
# and would have vanished with the machine — the same mistake as the camera
# stack's undeclared dependencies, which a fresh clone could not have rebuilt.
#
#   powershell -ExecutionPolicy Bypass -File scripts\install_model3d.ps1
$ErrorActionPreference = "Stop"

$Root   = Split-Path $PSScriptRoot
$Target = if ($env:JARVIS_MODEL3D) { $env:JARVIS_MODEL3D } else { "C:\AI\model3d" }
$Py312  = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
if (-not (Test-Path $Py312)) { $Py312 = "python" }

Write-Host "installing tiers 3 and 4 into $Target"
New-Item -ItemType Directory -Force $Target | Out-Null

# 1. its own environment
if (-not (Test-Path "$Target\.venv\Scripts\python.exe")) {
    & $Py312 -m venv "$Target\.venv"
}
$py = "$Target\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip

# 2. torch, CPU build. The CUDA wheels are ~2.5 GB and useless here: this is an
#    AMD Radeon 780M, so CUDA is not an option at all.
& $py -m pip install --quiet torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 3. TripoSR's dependencies, plus:
#      scikit-image  - marching cubes without a compiler (see _mcubes_shim.py)
#      onnxruntime-directml - so background removal runs on the 780M, which is
#                             worth ~4x on this kind of work
& $py -m pip install --quiet "transformers==4.35.0" "omegaconf==2.3.0" "einops==0.7.0" `
      "trimesh>=4.0" huggingface-hub pillow scikit-image rembg onnxruntime-directml

# 4. the model code (MIT). Not pip-installable; it is a repo with a tsr/ package.
if (-not (Test-Path "$Target\TripoSR\tsr")) {
    git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git "$Target\TripoSR"
}

# 5. our worker and its shim, from the repo — so this directory can always be
#    rebuilt from a clone rather than being a machine-specific artefact.
Copy-Item "$Root\model3d\photo_to_mesh.py" $Target -Force
Copy-Item "$Root\model3d\_mcubes_shim.py"  $Target -Force
Copy-Item "$Root\model3d\bench_ep.py"      $Target -Force
Copy-Item "$Root\model3d\bench_rembg.py"   $Target -Force

# 6. pull the weights now (~1.7 GB) rather than on his first request, which
#    would blow the estimate by a minute and teach him not to trust it.
Write-Host "fetching the TripoSR weights (about 1.7 GB, once)"
& $py -c "from huggingface_hub import hf_hub_download as d; d('stabilityai/TripoSR','model.ckpt'); d('stabilityai/TripoSR','config.yaml'); print('weights ready')"

Write-Host ""
Write-Host "done. JARVIS finds this through config fabrication.model3d_dir"
Write-Host "verify with:  $py $Target\photo_to_mesh.py <a picture> out.stl"
