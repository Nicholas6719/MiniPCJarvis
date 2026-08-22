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
    # the user's real folders (Desktop is OneDrive-redirected on this PC)
    "folders": {
        "desktop": r"C:\Users\nicho\OneDrive\Desktop",
        "documents": r"C:\Users\nicho\Documents",
        "downloads": r"C:\Users\nicho\Downloads",
        "pictures": r"C:\Users\nicho\Pictures",
    },
    "llm": {
        "server_binary": r"C:\AI\llama.cpp\llama-server.exe",
        "port": 8033,
        # other local apps (Houston) may already serve the same model — reuse it
        "adopt_ports": [8080],
        "context": 16384,
        "active_model": "gpt-oss-20b",
        "models": {
            # Google's QAT q4_0 (near-bf16 quality). MoE 26B/3.8B-active: ~25 t/s on the
            # 780M, first token ~0.8 s warm, native tool calling, thinking off for voice.
            # Measured and rejected on this iGPU (2026-08-22): MTP (+5-8% only), in-model
            # vision (14.4 GB weights + image compute buffers overflow the 17.4 GB Vulkan
            # heap -> crashes), CPU experts (-27% speed). Vision runs on the CPU side
            # server instead (gpu_full below).
            "gemma-4-26b-a4b": {
                "path": r"C:\AI\models\gemma-4-26B-A4B-it-qat-q4_0.gguf",
                "args": ["-ngl", "999", "-t", "8", "-fa", "on", "--jinja", "--cache-reuse", "256"],
                "template_kwargs": {"enable_thinking": False},
                "reasoning_field": "reasoning_content",
                "gpu_full": True,   # fills the iGPU heap: the vision server must use the CPU
            },
            "gpt-oss-20b": {
                "path": r"C:\AI\models\gpt-oss-20b-MXFP4.gguf",
                "args": ["-ngl", "999", "-t", "8", "-fa", "on", "--jinja", "--cache-reuse", "256"],
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
    "stt": {"model": "base.en", "compute_type": "int8", "device": "cpu"},  # base.en: 3x faster than small.en, same accuracy on commands (tests/stt_ab.py)
    "tts": {"engine": "kokoro", "voice": "bm_george", "rate": 1.0},
    "speech": {"fillers": True},   # say "Let me see." while the model is still thinking
    "brain": {"enabled": True, "threshold": 0.82, "general_hint_threshold": 0.7},
    "audio": {"input_device": None, "output_device": None,
              "sound_cues": True, "boot_sound": True,
              # always prefer the webcam mic when present; onboard mic is the fallback
              "preferred_input_names": ["C920", "Webcam", "Logitech"]},
    # threshold 0.45: "Hey Jarvis" scores ~0.99, bare "Jarvis" 0.46-0.95 by voice,
    # confusables (service/nervous/harvest) 0.00-0.04. Raise if the TV wakes him,
    # lower if bare "Jarvis" gets missed.
    "wake": {"mode": "both", "threshold": 0.45},
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
            if self._migrate():
                self.save()
        else:
            self.save()

    # Saved configs snapshot every default, so improved defaults never reach an
    # existing install on their own. Each migration runs once (tracked by version).
    CONFIG_VERSION = 4

    def _migrate(self) -> bool:
        v = int(self.data.get("config_version", 1) or 1)
        changed = False
        if v < 2:
            # STT: base.en is 3x faster than small.en with equal command accuracy (tests/stt_ab.py)
            if self.data.get("stt", {}).get("model") == "small.en":
                self.data["stt"]["model"] = "base.en"
                changed = True
        if v < 3:
            # new model entries become available without touching the active choice
            models = self.data.setdefault("llm", {}).setdefault("models", {})
            for name, entry in DEFAULTS["llm"]["models"].items():
                if name not in models:
                    models[name] = entry
                    changed = True
        if v < 4:
            # built-in model entries are ours to tune: refresh them (user-added ones untouched)
            models = self.data.setdefault("llm", {}).setdefault("models", {})
            for name, entry in DEFAULTS["llm"]["models"].items():
                if models.get(name) != entry:
                    models[name] = entry
                    changed = True
        if v != self.CONFIG_VERSION:
            self.data["config_version"] = self.CONFIG_VERSION
            changed = True
        return changed

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(self.data, indent=2), "utf-8")

    def set(self, *keys: str, value: Any) -> None:
        cur = self.data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value
        self.save()

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
