# PyInstaller spec: bundles the sidecar as a self-contained onedir app.
# Build:  .venv\Scripts\pyinstaller jarvis-sidecar.spec --noconfirm
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas, binaries, hiddenimports = [], [], []

for pkg in ["faster_whisper", "piper", "fastembed", "onnxruntime", "tokenizers",
            "openwakeword", "trafilatura", "pycaw", "comtypes",
            "kokoro_onnx", "playwright", "mcp",
            "espeakng_loader", "phonemizer", "num2words", "cssselect", "lxml",
            "onnx_asr", "winocr"]:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# winrt is a namespace package split across many wheels: collect each one we use
for pkg in ["winrt.runtime", "winrt.windows.foundation", "winrt.windows.foundation.collections",
            "winrt.windows.globalization", "winrt.windows.graphics.imaging", "winrt.windows.media.ocr",
            "winrt.windows.storage.streams", "winrt._winrt_windows_media_ocr"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as e:
        print("collect skipped", pkg, e)

# The face model for presence detection. 227 KB, and vision_presence looks for
# it under sys._MEIPASS/models at runtime — without this line the frozen build
# silently loses face detection while every offline test still passes, because
# the tests stub the detector.
datas += [("models/face_detection_yunet_2023mar.onnx", "models")]

hiddenimports += [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "sounddevice", "_sounddevice_data",
    "win32gui", "win32con", "win32clipboard", "win32api", "win32ui", "win32process",
    "mss",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "pyinstaller"],  # PIL required by fastembed
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="jarvis-sidecar",
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="jarvis-sidecar",
)
