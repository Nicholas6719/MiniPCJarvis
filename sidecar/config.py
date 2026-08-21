"""JARVIS configuration. Lives in %LOCALAPPDATA%/JARVIS/config.json — never holds secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Roaming AppData — deliberately NOT %LOCALAPPDATA%\JARVIS, which is where the
# NSIS per-user installer places the application itself (uninstall must never
# be able to take user data with it).
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "JARVIS"
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
DB_PATH = APP_DIR / "jarvis.db"

DEFAULTS: dict[str, Any] = {
    "general": {"first_run_complete": False},
    "llm": {
        "server_binary": r"C:\AI\llama.cpp\llama-server.exe",
        "port": 8033,
        # other local apps (Houston) may already serve the same model — reuse it
        "adopt_ports": [8080],
        "context": 16384,
        "active_model": "gpt-oss-20b",
        "models": {
            "gpt-oss-20b": {
                "path": r"C:\AI\models\gpt-oss-20b-MXFP4.gguf",
                "args": ["-ngl", "999", "-t", "8", "-fa", "on", "--jinja"],
                "template_kwargs": {"reasoning_effort": "low"},
                "reasoning_field": "reasoning_content",
            },
            # CPU-only: its hybrid attention OOMs the 780M Vulkan heap (tested
            # b10488 + b10549, --cpu-moe, host-memory). ~9 t/s — not for voice;
            # reserved for future non-realtime/vision use.
            "qwen3.6-35b-a3b": {
                "path": r"C:\AI\models\Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",
                "args": ["--device", "none", "-ngl", "0", "-t", "8", "--jinja"],
                "template_kwargs": {"enable_thinking": False},
                "reasoning_field": "reasoning_content",
            },
        },
    },
    "stt": {"model": "small.en", "compute_type": "int8", "device": "cpu"},
    "tts": {"engine": "kokoro", "voice": "bm_george", "rate": 1.0},
    "audio": {"input_device": None, "output_device": None,
              "sound_cues": True, "boot_sound": True,
              # always prefer the webcam mic when present; onboard mic is the fallback
              "preferred_input_names": ["C920", "Webcam", "Logitech"]},
    # threshold 0.45: "Hey Jarvis" scores ~0.99, bare "Jarvis" 0.46-0.95 by voice,
    # confusables (service/nervous/harvest) 0.00-0.04. Raise if the TV wakes him,
    # lower if bare "Jarvis" gets missed.
    "wake": {"mode": "push_to_talk", "threshold": 0.45},
    "conversation": {"window_s": 8},      # follow-up without wake word after a reply
    "interrupt": {"mode": "wake_word"},   # wake_word | any_speech
    "memory": {"enabled": True},
    "search": {"provider": "brave"},
    # MCP plugin servers: {"name": {"command": "...", "args": [...], "risk": "medium"}}
    "mcp": {"servers": {}},
    "proactive": {
        "enabled": True,
        "quiet_start": "22:00",
        "quiet_end": "08:00",
        "max_per_hour": 2,
        "disk_free_gb_warn": 50,
        "ram_percent_warn": 94,
        "break_after_min": 180,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self) -> None:
        self.data: dict[str, Any] = DEFAULTS
        self.load()

    def load(self) -> None:
        if CONFIG_PATH.exists():
            try:
                self.data = _merge(DEFAULTS, json.loads(CONFIG_PATH.read_text("utf-8")))
            except Exception:
                self.data = dict(DEFAULTS)
        else:
            self.save()

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self.data, indent=2), "utf-8")

    def get(self, *keys: str, default: Any = None) -> Any:
        cur: Any = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur


config = Config()

# Secrets are held in memory only; the Rust core injects them from Windows
# Credential Manager after startup. Never written to disk here.
secrets: dict[str, str] = {}
