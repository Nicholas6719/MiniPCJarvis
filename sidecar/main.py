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
from events import bus, spawn
from memory.store import memory
from orchestrator import orchestrator
from state_machine import State
from tasks.scheduler import scheduler
from tools import (builtin, browser_tools, handoff, memory_tools, task_tools,
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
    handoff.register_all()
    from tools import camera_tools, file_tools, market_tools, news_tools, weather
    file_tools.register_all()
    weather.register_all()
    camera_tools.register_all()   # the webcam view; the device stays shut until asked
    market_tools.register_all()   # quotes/analysts: realm 2, never cached
    news_tools.register_all()     # keyless RSS
    if config.get("remote", "allow_input", default=True):
        from tools import input_tools, uia   # remote hands (R2), all risk-gated
        input_tools.register_all()
        uia.register_all()               # click by control name, not by pixel
    if os.environ.get("JARVIS_DEBUG") == "1":
        from tools.registry import Risk, Tool, registry as _reg

        async def _debug_confirm() -> dict:
            return {"ok": True, "did": "nothing (test)"}
        _reg.register(Tool(name="_debug_confirm",
                           description="test-only confirmation-gated no-op",
                           parameters={"type": "object", "properties": {}, "required": []},
                           risk=Risk.MEDIUM, handler=_debug_confirm))
    # Everything JARVIS says on his own initiative goes through delivery, which
    # decides where he is and therefore whether to speak it or send it.
    from delivery import ALERT, delivery
    delivery.orchestrator = orchestrator

    async def _announce_alert(text: str, *, key: str = "") -> None:
        await delivery.deliver(text, tier=ALERT, key=key)

    async def _announce_reminder(text: str, *, key: str = "") -> None:
        """A due reminder, in JARVIS's own words rather than the user's.

        He set "wear my retainers" and heard `A reminder: wear my retainers`
        back every night. Now it goes through the LLM and comes out as
        something he would actually say, differently most nights.

        `key` is the TASK, deliberately - not the sentence. The whole point of
        this feature is that the wording changes, and delivery's flood guard
        de-duplicates on the key. Keying on text would mean every fresh phrasing
        looked like a brand new message, which is exactly how a stuck reminder
        became ~2,600 overnight messages on 2026-08-31.
        """
        from reminder_voice import phrase
        await delivery.deliver(await phrase(text), tier=ALERT, key=key)

    scheduler.announce = _announce_reminder
    scheduler.start()
    from mcp_client import mcp_manager
    asyncio.create_task(mcp_manager.start())
    from proactive import proactive
    proactive.announce = _announce_alert

    # His shift: four briefs a day, and a quiet watch between them for the
    # things that will not wait. delivery decides where each one goes.
    from briefing import briefing
    briefing.start()
    proactive.start()
    from brain.router import brain

    async def _load_brain():
        try:
            await brain.load()
        except Exception:
            logging.getLogger("jarvis").exception("brain failed to load (LLM-only mode)")
    asyncio.create_task(_load_brain())
    asyncio.create_task(orchestrator.start())
    memory.prune()          # bounded transcript / audit log; knowledge is never pruned
    from tools.shortlist import shortlist
    asyncio.create_task(shortlist.build(registry))   # embed tool descriptions once
    from dictation import dictation as _dict
    _dict.orchestrator = orchestrator     # so it refuses to fight a real turn
    from brain.night_school import night_school
    night_school.start(orchestrator)   # audits + curiosity + distillation while he sleeps
    if config.get("remote", "telegram", default=True):
        from remote_telegram import telegram
        telegram.start(orchestrator)   # dormant until a token is stored
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


@app.get("/secrets")
async def which_secrets(x_jarvis_token: str | None = Header(None)):
    """WHICH secrets this session holds — names only, never values.

    The Rust core owns the credential store and pushes secrets in at startup.
    If that push is missed (it gives up after three restarts in ten minutes),
    the sidecar has no way to know it is missing one, and simply behaves as
    though the user never configured it. This lets the core reconcile.
    """
    _auth(x_jarvis_token)
    return {"present": sorted(k for k, v in secrets.items() if v)}


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


@app.get("/facts")
async def list_facts(x_jarvis_token: str | None = Header(None)):
    """The fact store with receipts — realm 1 of docs/BRAIN_ROADMAP.md."""
    _auth(x_jarvis_token)
    from brain.facts import facts
    return {"facts": facts.list_all(), "stats": facts.stats}


@app.delete("/facts/{fact_id}")
async def delete_fact(fact_id: int, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from brain.facts import facts
    facts.delete(fact_id)
    return {"ok": True}


@app.post("/remote/telegram/token")
async def set_telegram_token(body: dict, x_jarvis_token: str | None = Header(None)):
    """Store the bot token (DPAPI-encrypted) and start the bridge. The token never
    touches config.json or logs."""
    _auth(x_jarvis_token)
    from remote_telegram import telegram
    token = str(body.get("token", "")).strip()
    if not token or ":" not in token:
        raise HTTPException(400, "that does not look like a bot token")
    return await telegram.set_token(token)


@app.get("/remote/telegram/status")
async def telegram_status(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from remote_telegram import telegram
    return telegram.status()


@app.post("/remote/telegram/unpair")
async def telegram_unpair(x_jarvis_token: str | None = Header(None)):
    """Forget the paired chat; a new pairing code is issued on next start."""
    _auth(x_jarvis_token)
    from remote_telegram import telegram
    config.set("remote", "telegram_chat_id", value=None)
    import secrets as _s
    telegram.pairing_code = f"{_s.randbelow(1000000):06d}"
    return {"ok": True, "pairing_code": telegram.pairing_code}


@app.post("/dictation/start")
async def dictation_start(x_jarvis_token: str | None = Header(None)):
    """Hotkey pressed: start capturing for dictation (no turn, no reply)."""
    _auth(x_jarvis_token)
    from dictation import dictation
    return await dictation.start()


@app.post("/dictation/stop")
async def dictation_stop(x_jarvis_token: str | None = Header(None)):
    """Hotkey released: transcribe and paste into the focused app."""
    _auth(x_jarvis_token)
    from dictation import dictation
    return await dictation.stop()


@app.get("/turnstats")
async def turn_stats(days: int = 7, x_jarvis_token: str | None = Header(None)):
    """Which turns wake the LLM and what they cost (brain roadmap stage 1)."""
    _auth(x_jarvis_token)
    return memory.turn_stats_summary(max(1, min(90, days)))


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


@app.post("/debug/tool")
async def debug_tool(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): run one registered tool with exact
    arguments, through the real risk gate.

    Tests that go through the model test the model's phrasing as much as the
    tool; this runs the tool itself so an end-to-end test can prove that a
    click really clicks. Confirmation still fires — approve it over /confirm
    like any other client would.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    name = (body.get("tool") or "").strip()
    if not name:
        raise HTTPException(400, "tool required")
    from tools.registry import registry as _reg
    prev = orchestrator.sm.state
    try:
        return await _reg.execute(name, body.get("args") or {})
    finally:
        # Confirmation moves the state machine — WAITING while he asks, EXECUTING
        # once approved — and it is the TURN that hands it back afterwards. There
        # is no turn here, so put it back ourselves. Left parked in EXECUTING, the
        # next thing said waits 60 s on a turn that does not exist (this is what
        # made sleep_e2e fail only inside a full suite run).
        if prev in (State.IDLE, State.SLEEPING) and orchestrator.sm.state != prev:
            await orchestrator.sm.to(prev, force=True)


@app.post("/debug/telegram_send_voice")
async def debug_telegram_send_voice(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): put a voice clip INTO the chat and return
    its file_id.

    There is no other way to test the voice-note path honestly. The bot cannot
    receive a recording he did not make, but it can send one — and Telegram
    hands back a real file_id, which the bridge then downloads and decodes
    exactly as it would his own.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from pathlib import Path as _P

    import httpx as _httpx
    from remote_telegram import API, telegram
    chat = config.get("remote", "telegram_chat_id", default=None)
    if not chat or not telegram.token:
        raise HTTPException(400, "not paired to a chat")
    path = _P(str(body.get("path") or ""))
    if not path.is_file():
        raise HTTPException(400, f"no such file: {path}")
    async with _httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{API}/bot{telegram.token}/sendVoice",
                         data={"chat_id": str(chat), "caption": "(test clip)"},
                         files={"voice": (path.name, path.read_bytes(), "audio/ogg")})
    data = r.json()
    if not data.get("ok"):
        raise HTTPException(502, str(data.get("description"))[:200])
    return {"ok": True, "file_id": (data["result"].get("voice") or {}).get("file_id")}


@app.post("/debug/audio_playing")
async def debug_audio_playing(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): pretend another app is (or is not) making
    noise, so the wake-word guard can be tested without filling the room with it.

    The detector itself is tested against real sound by hand and in
    tests/test_output_watch.py; this is for the WIRING — that the microphone
    loop actually consults it.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from audio import output_watch
    import time as _t
    on = bool(body.get("on"))
    output_watch.reset()
    if on:
        # hold it "heard just now" and stop it looking at the real sessions
        output_watch._heard_at = _t.time() + 3600
        output_watch._last_at = _t.time() + 3600
    return {"ok": True, "pretending_audio_plays": on}


@app.post("/debug/forget_secret")
async def debug_forget_secret(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): drop a secret from THIS session's memory.

    Simulates what a restart storm does — the sidecar comes back with nothing and
    tells the user to add a key that is already in Credential Manager. The Rust
    supervisor should notice within its 20 s tick and push it back; this is the
    only way to prove that it does.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    name = str(body.get("name") or "")
    had = bool(secrets.pop(name, None))
    return {"ok": True, "forgot": name, "had_it": had}


@app.post("/debug/brief")
async def debug_brief(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): compose a brief, or run the watch, and by
    default DO NOT send it anywhere.

    Building this without a way to look at the output would have meant testing
    by sending real messages to his phone, over and over, all day.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from briefing import briefing
    if body.get("watch"):
        found = await briefing.scan()
        return {"would_send": [{"text": t, "tier": tier} for t, tier, _ in found],
                "held_for_next_brief": len(briefing._held)}
    # Both shapes, from ONE build. Returning only the spoken form made this
    # endpoint useless for the thing he actually complained about - the brief on
    # his phone - and a verification tool that cannot see the output it is meant
    # to verify is worse than none.
    sections = await briefing._sections()
    text = await briefing.compose_brief(sections)
    written = await briefing.compose_brief_written(sections)
    if body.get("send"):
        from delivery import BRIEF, delivery
        return {"brief": text, "written": written,
                "delivery": await delivery.deliver(text, tier=BRIEF, written=written)}
    return {"brief": text, "written": written, "sent": False}


@app.post("/debug/telegram")
async def debug_telegram(body: dict, x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): hand the bridge an update as though it had
    arrived from his phone, so the REMOTE path can be tested end to end.

    Only the inbound half is simulated — everything the bridge sends back is a
    real message to the real chat. There is no other way to test this: the bot
    cannot receive a message from him without him sending one.
    """
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from remote_telegram import telegram
    chat = config.get("remote", "telegram_chat_id", default=None)
    if not chat:
        raise HTTPException(400, "not paired to a chat")
    if body.get("callback"):
        update = {"callback_query": {"id": "debug", "from": {"id": chat},
                                     "data": str(body["callback"])}}
    elif body.get("voice_file_id"):
        update = {"message": {"chat": {"id": chat}, "from": {"id": chat},
                              "voice": {"file_id": str(body["voice_file_id"]),
                                        "file_size": int(body.get("file_size") or 0)}}}
    else:
        update = {"message": {"chat": {"id": chat}, "from": {"id": chat},
                              "text": str(body.get("text") or "")}}
    spawn(telegram._handle_update(update), name="debug-telegram")
    return {"ok": True, "sent_as": chat}


@app.post("/debug/night_school")
async def debug_night_school(x_jarvis_token: str | None = Header(None)):
    """Dev/test only (JARVIS_DEBUG=1): run one full night-school pass right now,
    ignoring the sleep/quiet-hours conditions. Blocks until done; returns the report."""
    _auth(x_jarvis_token)
    if os.environ.get("JARVIS_DEBUG") != "1":
        raise HTTPException(403, "debug endpoints disabled")
    from brain.night_school import night_school
    return await night_school.run(force=True)


@app.get("/night_school")
async def night_school_status(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from brain.night_school import night_school
    return {"last_run": night_school._last_run_ts(), "last_report": night_school.last_report}


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
async def transcript(limit: int = 30, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    # the History pane asks for 200; cap so a bad param can't drag the whole DB out
    return {"transcript": memory.recent_transcript(max(1, min(500, limit)))}


@app.get("/camera/status")
async def camera_status(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    from camera import camera
    return camera.status()


@app.post("/camera")
async def camera_set(body: dict, x_jarvis_token: str | None = Header(None)):
    """Turn the camera on, off, or the other way from however it is now."""
    _auth(x_jarvis_token)
    from camera import camera
    want = body.get("on")
    # Opening a USB device blocks for the best part of a second, so it goes to a
    # thread. The event loop is where he waits for answers.
    if want is None:
        return await asyncio.to_thread(camera.toggle)
    return await asyncio.to_thread(camera.start if want else camera.stop)


@app.get("/camera/stream")
async def camera_stream(token: str = "", x_jarvis_token: str | None = Header(None)):
    """The live view, as multipart JPEG.

    An <img> tag cannot send a header, so this one route also accepts the token
    as a query parameter — the same session token, on loopback only, and the
    header is still honoured when the caller can set one.
    """
    _auth(x_jarvis_token or token)
    import asyncio as _a

    from fastapi.responses import StreamingResponse

    from camera import camera

    async def frames():
        # The stream NEVER turns the camera on by itself. He said "toggle camera
        # view mode"; a page that opened the device merely by being rendered
        # would be a camera he did not ask for.
        #
        # It WAITS for each new frame rather than sleeping on its own clock.
        # Sleeping added up to another 1/15 s of lag on top of the capture path
        # and could re-send a frame the HUD already had — half of why his face
        # trailed him on screen. frame_after blocks in a thread so the event
        # loop, where he waits for answers, is never held.
        blank, seq = 0, -1
        while camera.is_on:
            data, seq = await _a.to_thread(camera.frame_after, seq, 1.0)
            if data is None:
                blank += 1
                if blank > 10:                       # ~10 s with nothing at all
                    break
                continue
            blank = 0
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                   b"Content-Length: " + str(len(data)).encode() + b"\r\n\r\n"
                   + data + b"\r\n")

    return StreamingResponse(
        frames(), media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"})


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
    # run_text_turn interrupts speech and waits out a working turn, so a typed
    # message is never silently dropped for being "busy"
    spawn(orchestrator.run_text_turn(text), name="text-turn")
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
    # timeout_keep_alive: a second request QUEUED on a connection whose first
    # request is still running was being killed by the 5-second default, which
    # is every concurrent pair of tool calls from one client - verified
    # 2026-08-31: two quotes on one connection, second one ReadError, while two
    # separate connections both succeeded. Tools legitimately take longer than
    # five seconds.
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning",
                timeout_keep_alive=120)


if __name__ == "__main__":
    main()
