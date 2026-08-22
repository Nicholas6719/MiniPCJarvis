"""System diagnostics: real health checks per subsystem + repair actions."""
from __future__ import annotations

import logging
from pathlib import Path

import psutil

from config import APP_DIR, config, secrets

log = logging.getLogger("jarvis.diagnostics")


async def run_diagnostics() -> list[dict]:
    from audio.io import mic, speaker
    from audio.stt import stt
    from audio.tts import tts, VOICES_DIR
    from llm.llama_server import llama
    from llm.vision_server import vision
    from memory.store import memory
    from tasks.scheduler import scheduler

    checks: list[dict] = []

    def add(name: str, status: str, detail: str, repairable: bool = False):
        checks.append({"name": name, "status": status, "detail": detail,
                       "repairable": repairable})

    # LLM
    if await llama.healthy():
        src = "shared server" if llama.external else "managed"
        add("AI Engine", "ok", f"{llama.model_name} ({src})")
    else:
        add("AI Engine", "error", "llama-server not responding", repairable=True)

    # STT
    add("Speech Recognition", "ok" if stt._model is not None else "warn",
        f"faster-whisper {config.get('stt', 'model')}"
        if stt._model is not None else "not loaded yet (loads on first use)",
        repairable=True)

    # TTS
    voice = str(config.get("tts", "voice"))
    if voice.startswith("en_"):
        loaded = getattr(tts.piper, "_voice", None) is not None
        available = (VOICES_DIR / f"{voice}.onnx").exists()
        engine = "Piper"
    else:
        from audio.tts import KOKORO_DIR
        loaded = getattr(tts.kokoro, "_k", None) is not None
        available = (KOKORO_DIR / "kokoro-v1.0.onnx").exists()
        engine = "Kokoro"
    if loaded:
        add("Voice Synthesis", "ok", f"{voice} ({engine})")
    elif available:
        add("Voice Synthesis", "warn", f"{voice} ({engine}, loads on first use)", repairable=True)
    else:
        add("Voice Synthesis", "error", f"{engine} voice files missing — will fall back", repairable=False)

    # Microphone
    if mic._stream is None:
        add("Microphone", "error", "not capturing", repairable=True)
    elif mic.using_preferred:
        add("Microphone", "ok", f"{mic.device_name} (webcam mic)", repairable=True)
    else:
        add("Microphone", "warn", f"{mic.device_name} — webcam mic not detected, using fallback",
            repairable=True)

    # Wake word
    from audio.wake import wake
    mode = config.get("wake", "mode", default="push_to_talk")
    if mode in ("wake_word", "both"):
        add("Wake Word", "ok" if wake._model is not None else "warn",
            f"'hey jarvis' active (mode: {mode})" if wake._model is not None
            else "model not loaded", repairable=False)
    else:
        add("Wake Word", "ok", "disabled (push-to-talk mode)")

    # Vision
    vm = Path(config.get("vision", "model",
                         default=r"C:\AI\models\gemma-3-4b-it-q4_0.gguf"))
    if vision.running:
        add("Vision", "ok", "model loaded (auto-stops when idle)")
    elif vm.exists():
        add("Vision", "ok", "available (loads on demand)")
    else:
        add("Vision", "error", "vision model files missing")

    # Web search
    from search_brave_web import brave_web
    if secrets.get("brave_api_key"):
        add("Web Search", "ok", "Brave Search API")
    elif brave_web.available:
        add("Web Search", "ok", "via your Brave browser (no API key needed)")
    else:
        add("Web Search", "warn", "install Brave browser or add a Brave API key in Settings")

    # Memory
    try:
        n = memory.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        add("Memory", "ok", f"{n} memories stored")
    except Exception as e:
        add("Memory", "error", str(e))

    # Scheduler
    add("Tasks", "ok" if scheduler._task and not scheduler._task.done() else "error",
        f"{len(scheduler.list_pending())} pending" if scheduler._task else "not running",
        repairable=True)

    # Resources
    vmem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(APP_DIR.anchor))
    add("System RAM", "ok" if vmem.percent < 92 else "warn",
        f"{vmem.percent:.0f}% used ({round(vmem.used/1e9, 1)} / {round(vmem.total/1e9, 1)} GB)")
    add("Disk", "ok" if disk.percent < 90 else "warn",
        f"{disk.percent:.0f}% used, {round(disk.free/1e9)} GB free")

    return checks


async def repair(subsystem: str) -> dict:
    """Best-effort repair actions per subsystem."""
    from audio.io import mic, speaker
    from audio.stt import stt
    from audio.tts import tts
    from llm.llama_server import llama
    from tasks.scheduler import scheduler

    name = subsystem.lower()
    try:
        if name in ("ai engine", "llm"):
            await llama.stop()
            ok = await llama.ensure()
            return {"ok": ok, "action": "restarted llama-server"}
        if name in ("speech recognition", "stt"):
            stt.reload()
            await stt.warmup()
            return {"ok": True, "action": "reloaded whisper"}
        if name in ("voice synthesis", "tts"):
            tts.reload()
            await tts.warmup()
            return {"ok": True, "action": "reloaded voice"}
        if name == "microphone":
            mic.restart()
            return {"ok": True, "action": "restarted microphone"}
        if name == "tasks":
            scheduler.start()
            return {"ok": True, "action": "restarted scheduler"}
    except Exception as e:
        log.exception("repair %s failed", subsystem)
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"no repair action for '{subsystem}'"}
