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
from tools import builtin, memory_tools
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
    asyncio.create_task(orchestrator.start())
    yield
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


@app.get("/memory")
async def memory_list(x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"memories": memory.list_all()}


@app.delete("/memory/{memory_id}")
async def memory_forget(memory_id: int, x_jarvis_token: str | None = Header(None)):
    _auth(x_jarvis_token)
    return {"ok": memory.forget(memory_id)}


@app.post("/text")
async def text_input(body: dict, x_jarvis_token: str | None = Header(None)):
    """Typed input path (secondary to voice) — runs the same turn pipeline."""
    _auth(x_jarvis_token)
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text required")
    if orchestrator.sm.state not in (State.IDLE, State.INTERRUPTED):
        return {"ok": False, "error": "busy"}
    import numpy as np
    # Synthesize a fake 'utterance' by bypassing STT: run the turn directly.
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
