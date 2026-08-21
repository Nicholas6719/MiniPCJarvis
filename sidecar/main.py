"""JARVIS sidecar entrypoint — FastAPI + WebSocket server on loopback.

Launched and supervised by the Tauri (Rust) core. Never exposed off-machine.
Usage: python main.py --port 8790 --token <session-token>
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect

from config import LOG_DIR, config, secrets
from events import bus
from memory.store import memory
from orchestrator import orchestrator
from state_machine import State
from tasks.scheduler import scheduler
from tools import (builtin, browser_tools, memory_tools, task_tools,
                   vision_tools, web_tools, windows_tools)
from tools.registry import registry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "sidecar.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("jarvis.main")

SESSION_TOKEN = ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    builtin.register_all()
    memory_tools.register_all()
    windows_tools.register_all()
    web_tools.register_all()
    task_tools.register_all()
    vision_tools.register_all()
    browser_tools.register_all()
    scheduler.announce = orchestrator.announce
    scheduler.start()
    from mcp_client import mcp_manager
    asyncio.create_task(mcp_manager.start())
    from proactive import proactive
    proactive.announce = orchestrator.announce
    proactive.start()
    asyncio.create_task(orchestrator.start())
    yield
    proactive.stop()
    scheduler.stop()
    from mcp_client import mcp_manager
    await mcp_manager.stop()
    from browser.session import browser
    await browser.close()
    from llm.vision_server import vision
    await vision.stop()
    await orchestrator.shutdown()


app = FastAPI(lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    # WebView2 (Tauri) and dev origins only; API is loopback + token-authed.
    allow_origins=["http://tauri.localhost", "https://tauri.localhost",
                   "http://localhost:1420", "http://127.0.0.1:1420"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _auth(token: str | None) -> None:
    if SESSION_TOKEN and token != SESSION_TOKEN:
        raise HTTPException(401, "bad token")


@app.get("/health")
async def health():
    return {"ok": True, "state": orchestrator.sm.state.value}


@app.post("/secrets")
async def set_secret(body: dict, x_jarvis_token: str | None = Header(None)):
    """Rust core injects secrets from Windows Credential Manager. Memory-only."""
    _auth(x_jarvis_token)
    name, value = body.get("name"), body.get("value")
    if not name or value is None:
        raise HTTPException(400, "name and value required")
    secrets[name] = value
    return {"ok": True}


@app.post("/listen/toggle")
async def listen_toggle(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    await orchestrator.toggle_listen()
    return {"ok": True, "state": orchestrator.sm.state.value}


@app.post("/interrupt")
async def interrupt(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    await orchestrator.interrupt()
    return {"ok": True}


@app.post("/confirm")
async def confirm(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    ok = registry.resolve_confirmation(body.get("confirm_id", ""), bool(body.get("approved")))
    return {"ok": ok}


@app.get("/config")
async def get_config(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"config": config.data}


@app.patch("/config")
async def patch_config(body: dict, x_jarvis_token: str | None = Header(None)):
    """Merge partial settings and apply them live where possible."""
    _auth(x_jarvis_token)
    from config import _merge
    old = config.data
    config.data = _merge(config.data, body)
    config.save()

    applied = []
    if body.get("tts"):
        from audio.tts import tts
        tts.reload()
        applied.append("tts")
    if body.get("stt"):
        from audio.stt import stt
        stt.reload()
        applied.append("stt")
    if (body.get("audio") or {}).get("input_device") is not None:
        from audio.io import mic
        mic.restart()
        applied.append("microphone")
    if (body.get("audio") or {}).get("output_device") is not None:
        from audio.io import speaker
        speaker.close()
        applied.append("speaker")
    new_model = (body.get("llm") or {}).get("active_model")
    if new_model and new_model != (old.get("llm") or {}).get("active_model"):
        from llm.llama_server import llama
        ok = await llama.ensure(new_model)
        applied.append(f"llm:{new_model}:{'ok' if ok else 'FAILED'}")
    await bus.emit("config_changed", applied=applied)
    return {"ok": True, "applied": applied}


@app.get("/audio/devices")
async def audio_devices(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    import sounddevice as sd
    devs = sd.query_devices()
    return {
        "input": [{"id": i, "name": d["name"]}
                  for i, d in enumerate(devs) if d["max_input_channels"] > 0],
        "output": [{"id": i, "name": d["name"]}
                   for i, d in enumerate(devs) if d["max_output_channels"] > 0],
        "default_input": sd.default.device[0],
        "default_output": sd.default.device[1],
    }


@app.get("/voices")
async def voices(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from audio.tts import KOKORO_DIR, VOICES_DIR
    piper = sorted(p.stem for p in VOICES_DIR.glob("*.onnx"))
    kokoro = []
    if (KOKORO_DIR / "kokoro-v1.0.onnx").exists():
        # curated British + strongest general voices (full list is 50+)
        kokoro = ["bm_george", "bm_fable", "bm_daniel", "bm_lewis",
                  "bf_emma", "bf_isabella", "am_michael", "af_bella"]
    return {"voices": kokoro + piper, "active": config.get("tts", "voice"),
            "note": "bm_/bf_/am_/af_ voices use the Kokoro engine (higher quality); en_GB voices use Piper (lowest latency)"}


@app.get("/models")
async def models(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from llm.llama_server import llama
    return {
        "models": list(config.get("llm", "models", default={}).keys()),
        "active": llama.model_name or config.get("llm", "active_model"),
        "external": llama.external,
    }


@app.get("/diagnostics")
async def diagnostics(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from diagnostics import run_diagnostics
    return {"checks": await run_diagnostics()}


@app.post("/repair")
async def repair_subsystem(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from diagnostics import repair
    result = await repair(body.get("subsystem", ""))
    await bus.emit("repair", subsystem=body.get("subsystem"), **result)
    return result


@app.get("/stats")
async def stats(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    import psutil
    from llm.llama_server import llama
    vm = psutil.virtual_memory()
    return {
        "cpu": psutil.cpu_percent(interval=0.1),
        "ram_percent": vm.percent,
        "ram_used_gb": round(vm.used / 1e9, 1),
        "model": llama.model_name,
        "model_external": llama.external,
        "state": orchestrator.sm.state.value,
        "wake_mode": config.get("wake", "mode"),
    }


@app.get("/tasks")
async def tasks_list(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tasks.scheduler import scheduler
    return {"tasks": scheduler.list_pending()}


@app.delete("/tasks/{task_id}")
async def tasks_cancel(task_id: int, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tasks.scheduler import scheduler
    return {"ok": scheduler.cancel(task_id)}


@app.get("/memory")
async def memory_list(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"memories": memory.list_all()}


@app.delete("/memory/{memory_id}")
async def memory_forget(memory_id: int, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"ok": memory.forget(memory_id)}


@app.patch("/memory/{memory_id}")
async def memory_update(memory_id: int, body: dict,
                        x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    ok = True
    if "pinned" in body:
        ok = memory.set_pinned(memory_id, bool(body["pinned"])) and ok
    if body.get("content"):
        ok = await memory.update_content(memory_id, str(body["content"])) and ok
    return {"ok": ok}


@app.post("/debug/inject_audio")
async def debug_inject_audio(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): push float32 16 kHz audio into the mic
    broadcast as if the microphone heard it. Lets the wake/capture loop be
    tested end-to-end without hardware."""
    _auth(x_jarvis_token)
    import os, base64
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    import numpy as np
    from audio.io import mic
    audio = np.frombuffer(base64.b64decode(body["audio_b64"]), dtype=np.float32)
    # pause the hardware mic so injected audio isn't interleaved with room noise
    mic.stop()
    try:
        for i in range(0, len(audio), 1024):
            blk = audio[i:i + 1024]
            if len(blk) < 1024:
                blk = np.pad(blk, (0, 1024 - len(blk)))
            mic._put(blk.copy())
            await asyncio.sleep(1024 / 16000)  # real-time pacing
        # keep feeding silence while the turn captures end-of-speech
        for _ in range(int(16000 * 1.5 / 1024)):
            mic._put(np.zeros(1024, dtype=np.float32))
            await asyncio.sleep(1024 / 16000)
    finally:
        mic.start()
    return {"ok": True, "seconds": round(len(audio) / 16000, 2)}


@app.get("/transcript")
async def transcript(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"transcript": memory.recent_transcript(30)}


@app.get("/metrics")
async def metrics(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"summary": orchestrator.metrics.summary(),
            "recent": orchestrator.metrics.turns[-10:]}


@app.post("/text")
async def text_input(body: dict, x_jarvis_token: str | None = Header(None)):
    """Typed input path (secondary to voice) — runs the same turn pipeline."""
    _auth(x_jarvis_token)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    if orchestrator.sm.state not in (State.IDLE, State.INTERRUPTED):
        return {"ok": False, "error": "busy"}
    asyncio.create_task(orchestrator.run_text_turn(text))
    return {"ok": True}


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    token = websocket.query_params.get("token", "")
    if SESSION_TOKEN and token != SESSION_TOKEN:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    await bus.attach(websocket)
    await websocket.send_json({"kind": "state", "state": orchestrator.sm.state.value})
    try:
        while True:
            await websocket.receive_text()  # client pings; content unused
    except WebSocketDisconnect:
        pass
    finally:
        await bus.detach(websocket)


def main() -> None:
    global SESSION_TOKEN
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    SESSION_TOKEN = args.token

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
