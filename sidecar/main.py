"""JARVIS sidecar entrypoint — FastAPI + WebSocket server on loopback.

Launched and supervised by the Tauri (Rust) core. Never exposed off-machine.
Usage: python main.py --port 8790 --token <session-token>
"""
from __future__ import annotations

import argparse
import os
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
    from tools import file_tools, weather
    file_tools.register_all()
    weather.register_all()
    if os.environ.get("JARVIS_DEBUG") == "1":
        from tools.registry import Risk, Tool, registry as _reg

        async def _debug_confirm() -> dict:
            return {"ok": True, "did": "nothing (test)"}
        _reg.register(Tool(name="_debug_confirm",
                           description="test-only confirmation-gated no-op",
                           parameters={"type": "object", "properties": {}, "required": []},
                           risk=Risk.MEDIUM, handler=_debug_confirm))
    scheduler.announce = orchestrator.announce
    scheduler.start()
    from mcp_client import mcp_manager
    asyncio.create_task(mcp_manager.start())
    from proactive import proactive
    proactive.announce = orchestrator.announce
    proactive.start()
    from brain.router import brain

    async def _load_brain():
        try:
            await brain.load()
        except Exception:
            logging.getLogger("jarvis").exception("brain failed to load (LLM-only mode)")
    asyncio.create_task(_load_brain())
    asyncio.create_task(orchestrator.start())
    yield
    proactive.stop()
    scheduler.stop()
    from mcp_client import mcp_manager
    await mcp_manager.stop()
    from browser.session import browser
    await browser.close()
    from search_brave_web import brave_web
    await brave_web.close()
    from llm.vision_server import vision
    await vision.stop()
    await orchestrator.shutdown()


app = FastAPI(lifespan=lifespan)

from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    # WebView2 (Tauri) and dev origins only; API is loopback + token-authed.
    allow_origins=["http://tauri.localhost", "https://tauri.localhost",
                   "http://localhost:1420", "http://127.0.0.1:1420",
                   # vite dev server (1420 is silently firewalled on this machine)
                   "http://localhost:5173", "http://127.0.0.1:5173"],
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
    # the hotkey and the tray both land here: reaching for him ends sleep too
    await orchestrator.wake_if_sleeping()
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
    apis = sd.query_hostapis()
    mme = next((i for i, h in enumerate(apis) if "MME" in h["name"].upper()), 0)
    # Only shared-mode (MME) entries: the WDM-KS/DirectSound/WASAPI duplicates
    # Windows exposes would let a user accidentally pick an exclusive-mode path.
    def _list(kind):
        seen, out = set(), []
        for i, d in enumerate(devs):
            if d[kind] <= 0 or d["hostapi"] != mme:
                continue
            nm = d["name"]
            if nm in seen or nm.startswith("Microsoft Sound Mapper") or nm.startswith("Primary Sound"):
                continue
            seen.add(nm)
            out.append({"id": i, "name": nm})
        return out
    return {
        "input": _list("max_input_channels"),
        "output": _list("max_output_channels"),
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


@app.post("/voices/preview")
async def voice_preview(body: dict, x_jarvis_token: str | None = Header(None)):
    """Speak a short sample in a given voice without changing settings."""
    _auth(x_jarvis_token)
    from audio.io import speaker
    from audio.tts import tts
    voice = str(body.get("voice") or config.get("tts", "voice"))
    text = str(body.get("text") or
               "Good evening. I'm JARVIS — this is how I sound.")
    if orchestrator.sm.state not in (State.IDLE, State.SLEEPING):
        return {"ok": False, "error": "busy"}
    await orchestrator.sm.to(State.SPEAKING, force=True)
    # temporarily swap the voice for this one synthesis
    prev = config.get("tts", "voice")
    config.data["tts"]["voice"] = voice
    cancel = asyncio.Event()
    orchestrator._speak_cancel = cancel
    try:
        async for chunk in tts.synthesize_stream(text, cancel):
            if cancel.is_set():
                break
            await speaker.play_chunk(chunk, tts.sample_rate)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        config.data["tts"]["voice"] = prev
        if orchestrator.sm.state == State.SPEAKING:
            await orchestrator.sm.to(State.IDLE, force=True)
    return {"ok": True, "voice": voice}


@app.get("/models")
async def models(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from llm.llama_server import llama
    return {
        "models": list(config.get("llm", "models", default={}).keys()),
        "active": llama.model_name or config.get("llm", "active_model"),
        "external": llama.external,
    }


@app.get("/brain")
async def brain_status(x_jarvis_token: str | None = Header(None)):
    """JARVIS's own brain: examples, skills, reflex/LLM split, recent learning."""
    _auth(x_jarvis_token)
    from brain.router import brain
    return brain.status()


@app.post("/brain/teach")
async def brain_teach(body: dict, x_jarvis_token: str | None = Header(None)):
    """Teach a phrasing -> skill explicitly (from the UI)."""
    _auth(x_jarvis_token)
    from brain.router import brain
    text, skill = str(body.get("text", "")), str(body.get("skill", ""))
    ok = await brain.learn(text, skill, source="user")
    return {"ok": ok, "examples": brain.example_count}


@app.post("/brain/forget_command")
async def brain_forget_command(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from brain.router import brain
    return {"ok": await brain.forget_command(str(body.get("phrase", "")))}


@app.post("/brain/classify")
async def brain_classify(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dry-run: what would the brain do with this text? (no side effects)"""
    _auth(x_jarvis_token)
    from brain.router import brain
    d = await brain.decide(str(body.get("text", "")))
    name, conf = await brain.classify(str(body.get("text", "")))
    return {"skill": d[0].name if d else None, "args": d[1] if d else None,
            "confidence": d[2] if d else conf, "nearest": name}


@app.post("/browser/open")
async def browser_open_ep(body: dict, x_jarvis_token: str | None = Header(None)):
    """Open a page in JARVIS's in-app browser (HUD url bar / result click)."""
    _auth(x_jarvis_token)
    from browser.session import browser
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url required")
    return await browser.goto(url)


@app.post("/browser/click")
async def browser_click_ep(body: dict, x_jarvis_token: str | None = Header(None)):
    """Click-through: fractional x/y on the HUD screenshot -> real click in the hidden browser."""
    _auth(x_jarvis_token)
    from browser.session import browser
    return await browser.click_at(float(body.get("x", 0)), float(body.get("y", 0)))


@app.post("/browser/scroll")
async def browser_scroll_ep(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from browser.session import browser
    return await browser.scroll_by(int(body.get("dy", 400)))


@app.post("/browser/type")
async def browser_type_ep(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from browser.session import browser
    return await browser.type_keys(str(body.get("text", "")), bool(body.get("enter", False)))


@app.post("/browser/back")
async def browser_back_ep(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from browser.session import browser
    return await browser.back()


@app.get("/files")
async def files_list(path: str = "downloads", x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools import file_tools
    return await file_tools.list_folder(path)


@app.get("/files/search")
async def files_search(q: str, folder: str | None = None, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools import file_tools
    return await file_tools.find_files(q, folder)


@app.get("/files/preview")
async def files_preview(path: str, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools import file_tools
    return await file_tools.preview_file(path)


@app.post("/files/op")
async def files_op(body: dict, x_jarvis_token: str | None = Header(None)):
    """HUD-initiated file operations (the user clicked, so no voice confirmation)."""
    _auth(x_jarvis_token)
    from tools import file_tools
    op = body.get("op")
    if op == "rename":
        return await file_tools.rename_file(body["path"], body["new_name"])
    if op == "move":
        return await file_tools.move_file(body["path"], body["destination"])
    if op == "delete":
        return await file_tools.delete_file(body["path"])
    if op == "open":
        return await file_tools.open_with_windows(body["path"])
    raise HTTPException(400, "unknown op")


@app.get("/windows")
async def windows_list(thumbs: int = 1, x_jarvis_token: str | None = Header(None)):
    """Open windows with live thumbnails for the APPS view."""
    _auth(x_jarvis_token)
    from tools.window_thumbs import windows_with_thumbs
    return {"windows": await asyncio.to_thread(windows_with_thumbs, False, bool(thumbs))}


@app.post("/windows/act")
async def windows_act(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools.window_thumbs import act
    return await asyncio.to_thread(act, int(body.get("hwnd", 0)), str(body.get("action", "focus")))


@app.get("/system")
async def system_snapshot(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools.system_panel import snapshot
    return await asyncio.to_thread(snapshot)


@app.post("/system/volume")
async def system_volume(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools.windows_tools import set_volume
    return await asyncio.to_thread(set_volume, int(body.get("percent", 50)))


@app.post("/system/mute")
async def system_mute(body: dict, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from tools.windows_tools import set_mute
    return await asyncio.to_thread(set_mute, bool(body.get("muted", True)))


@app.post("/system/power")
async def system_power(body: dict, x_jarvis_token: str | None = Header(None)):
    """Power actions from the HUD (the user clicked and confirmed in the UI)."""
    _auth(x_jarvis_token)
    from tools.windows_tools import lock_computer, power_action
    action = str(body.get("action", ""))
    if action == "lock":
        return await asyncio.to_thread(lock_computer)
    if action in ("sleep", "restart", "shutdown"):
        return await asyncio.to_thread(power_action, action)
    raise HTTPException(400, "unknown action")


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


@app.post("/debug/silence")
async def debug_silence(body: dict, x_jarvis_token: str | None = Header(None)):
    """Self-test: keep the speaker quiet for N seconds (turns still run end to end)."""
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from audio.io import speaker
    import time as _t
    speaker.silent_until = _t.time() + float(body.get("seconds", 600))
    return {"ok": True, "until": speaker.silent_until}


@app.get("/brain/export")
async def brain_export(x_jarvis_token: str | None = Header(None)):
    """Training dataset: every user turn with the assistant reply and the tools that ran
    between them (from the audit log), as JSONL in %APPDATA%/JARVIS/dataset.jsonl.
    This is what a future LoRA fine-tune on a rented GPU would consume."""
    _auth(x_jarvis_token)
    import json as _json, sqlite3 as _sq
    from config import APP_DIR, DB_PATH
    db = _sq.connect(DB_PATH)
    turns = db.execute("SELECT ts, role, content FROM transcript ORDER BY id").fetchall()
    audit = db.execute("SELECT ts, tool, args, status FROM audit_log WHERE status='success' ORDER BY id").fetchall()
    out = APP_DIR / "dataset.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for i, (ts, role, content) in enumerate(turns):
            if role != "user":
                continue
            reply = next(((t2, c2) for t2, r2, c2 in turns[i + 1:i + 3] if r2 == "assistant"), None)
            end = reply[0] if reply else ts + 120
            tools = [{"tool": t, "args": _json.loads(a) if a else {}} for (ta, t, a, st) in audit if ts <= ta <= end]
            f.write(_json.dumps({"ts": ts, "user": content, "tools": tools,
                                 "assistant": reply[1] if reply else None}, ensure_ascii=False) + chr(10))
            n += 1
    return {"ok": True, "path": str(out), "examples": n}


@app.post("/debug/view")
async def debug_view(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test: switch the HUD to a view (conversation|files|apps|system|browser|...)."""
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    await bus.emit("set_view", view=str(body.get("view", "conversation")))
    return {"ok": True}


@app.get("/debug/hud.png")
async def debug_hud_png(x_jarvis_token: str | None = Header(None)):
    """Dev/test: full-resolution capture of the JARVIS window itself (PrintWindow), so
    the agent can look at the HUD without the user's screen."""
    _auth(x_jarvis_token)
    # A screen-capture endpoint has no business existing in a normal launch, token or not.
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from fastapi.responses import Response
    from tools.window_thumbs import capture_window_png
    png = await asyncio.to_thread(capture_window_png, "JARVIS")
    if not png:
        raise HTTPException(404, "JARVIS window not found")
    return Response(content=png, media_type="image/png")


@app.post("/debug/confirm_test")
async def debug_confirm_test(x_jarvis_token: str | None = Header(None)):
    """Dev/test only: fire a MEDIUM-risk no-op so the voice yes/no confirmation flow can be
    exercised without touching power state. Returns immediately; watch the event stream."""
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from tools.registry import registry

    async def _run():
        await orchestrator.sm.to(State.EXECUTING, force=True)
        try:
            await registry.execute("_debug_confirm", {})
        finally:
            if orchestrator.sm.state != State.ERROR:
                await orchestrator.sm.to(State.IDLE, force=True)
    asyncio.create_task(_run())
    return {"ok": True}


@app.post("/debug/llm_probe")
async def debug_llm_probe(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): ask the model one question with no tools and
    arbitrary sampling, so accuracy can be swept without a rebuild per setting."""
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from llm.prompts import system_prompt, turn_context
    from llm.provider import local_llm
    msgs = [{"role": "system", "content": system_prompt()},
            {"role": "user", "content": turn_context("") + chr(10) + str(body.get("text", ""))}]
    out = ""
    async for chunk in local_llm.stream(msgs, max_tokens=int(body.get("max_tokens", 256)),
                                        sampling=body.get("sampling") or None):
        out += chunk.text or ""
    return {"reply": out.strip()}


@app.get("/selftest")
async def selftest_report(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from config import APP_DIR
    p = APP_DIR / "selftest.json"
    if not p.exists():
        return {"ok": None, "results": []}
    import json as _json
    return _json.loads(p.read_text("utf-8"))


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
    from tools.registry import registry as _registry
    if _registry.has_pending:
        if await orchestrator.try_voice_confirmation(text):
            return {"ok": True, "answered_confirmation": True}
        _registry.resolve_latest(False)          # a different request = implicit no
        for _ in range(50):
            if orchestrator.sm.state in (State.IDLE, State.INTERRUPTED):
                break
            await asyncio.sleep(0.1)
    # typing to him is as deliberate as saying his name: it ends sleep rather than
    # bouncing off it. Without this, sleep mode also silently disabled the text box.
    await orchestrator.wake_if_sleeping()
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
    parser.add_argument("--token-stdin", action="store_true",
                        help="read the session token from stdin (keeps it out of argv)")
    args = parser.parse_args()
    if args.token_stdin and not args.token:
        try:
            args.token = (sys.stdin.readline() or "").strip()
        except Exception:
            args.token = ""
    # never run with auth disabled: an empty token would make every endpoint (file ops,
    # power, browser) open on loopback. If none was supplied, mint one — a manual caller
    # then has to read it from this process's command line, which is the intended bar.
    import secrets as _secrets
    SESSION_TOKEN = args.token or _secrets.token_hex(16)
    # Debug builds only: publish the token so the test harnesses can authenticate
    # (production keeps it in memory - it is never on the command line or on disk).
    if os.environ.get("JARVIS_DEBUG") == "1":
        try:
            from config import APP_DIR
            (APP_DIR / "session.token").write_text(SESSION_TOKEN, "utf-8")
        except Exception:
            pass

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
