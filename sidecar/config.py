"""JARVIS configuration. Lives in %LOCALAPPDATA%/JARVIS/config.json — never holds secrets."""
from __future__ import annotations

import json
import logging
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
# JARVIS_DB redirects the database, which the offline gates use so they never
# touch (or mutate) the user's real brain, memories and facts. brain.load()
# re-seeds an empty file from the SKILLS list, so a routing test on a fresh DB
# measures the code as written rather than whatever the live brain has learned.
DB_PATH = Path(os.environ.get("JARVIS_DB") or (APP_DIR / "jarvis.db"))

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
        # other local apps (Houston) may already serve the same model — reuse it
        "adopt_ports": [8080],
        "context": 16384,
        "active_model": "gpt-oss-20b",
        # Sampling was never sent, so llama-server's chat defaults applied (temp 0.8,
        # top_p 0.95) -- creative-writing sampling on an assistant whose job is mostly to
        # state facts. Measured over 20 verifiable questions x 4 runs, word-for-word
        # (tests/accuracy_bench.py): accuracy barely moves (99% -> 100%) but run-to-run
        # CONSISTENCY does, and that was the actual complaint -- the same question giving a
        # different answer each time:
        #     temp 0.8   accuracy  99%   consistency   5%
        #     temp 0.15  accuracy 100%   consistency  45%
        #     temp 0.0   accuracy 100%   consistency  85%
        # Greedy for facts; CREATIVE_INTENT in orchestrator raises it for anything that
        # should vary (jokes, poems, brainstorms). repeat_penalty is the loop guard.
        "sampling": {"temperature": 0.0, "repeat_penalty": 1.05},
        "models": {
            # Google's QAT q4_0 (near-bf16 quality). MoE 26B/3.8B-active: ~25 t/s on the
            # 780M, first token ~0.8 s warm, native tool calling, thinking off for voice.
            # Measured and rejected on this iGPU (2026-08-22): MTP (+5-8% only), in-model
            # vision (14.4 GB weights + image compute buffers overflow the 17.4 GB Vulkan
            # heap -> crashes), CPU experts (-27% speed). Vision runs on the CPU side
            # server instead (gpu_full below).
            "gemma-4-26b-a4b": {
                "path": r"C:\AI\models\gemma-4-26B-A4B-it-qat-q4_0.gguf",
                # q8 KV cache + 12K context: ~1.5 GB less RAM than 16K f16 (measured 2026-08-22)
                "args": ["-ngl", "999", "-t", "8", "-fa", "on", "--jinja", "--cache-reuse", "256",
                         "-ctk", "q8_0", "-ctv", "q8_0"],
                "context": 12288,
                "template_kwargs": {"enable_thinking": False},
                "reasoning_field": "reasoning_content",
                "gpu_full": True,   # fills the iGPU heap: the vision server must use the CPU
                "note": "Smarter and quicker for text; vision runs on the CPU and RAM peaks ~96% on a 32 GB PC.",
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
    # Parakeet TDT 0.6B v3 int8: 139 ms / 0.6% WER vs whisper base.en 450 ms / 5.1% (tests/stt_ab2.py)
    "stt": {"engine": "parakeet", "parakeet_quant": "int8", "model": "base.en", "compute_type": "int8", "device": "cpu"},
    "tts": {"engine": "kokoro", "voice": "bm_george", "rate": 1.0},
    "speech": {"fillers": True},
    # ~1 line in 3 carries the honorific, matching JARVIS's actual dialogue (see
    # brain/skills.py honorific()). Set honorific "" to switch it off entirely.
    "persona": {"honorific": "sir", "honorific_rate": 0.55},
    "confirm": {"by_voice": True},   # answer shutdown/restart confirmations by saying yes/no
    "ui": {"panel_hold_s": 5},      # seconds the stage holds after an answer (§6.3: 5 s + drain bar)
    # token lives DPAPI-encrypted on disk, never here
    "remote": {"telegram": True, "telegram_chat_id": None,
               "recycle_screenshots": True,   # a screenshot sent to the phone is a message, not a file
               "allow_input": True},          # remote typing/clicking (R2), always risk-gated
    # RSS is keyless and instant; Finnhub needs a key (Settings -> Tools)
    "news": {"enabled": True},
    # hold-to-dictate into any app (Ctrl+Shift+D): local Parakeet, no turn taken
    "dictation": {"enabled": True, "strip_fillers": True,
                  "spoken_punctuation": True, "restore_clipboard": True},
    "weather": {"home": "", "units": "fahrenheit"},   # home "" = locate by IP; set e.g. "Framingham, MA" to pin
    "brain": {"enabled": True, "threshold": 0.82, "general_hint_threshold": 0.7},
    "audio": {"input_device": None, "output_device": None,
              "sound_cues": True, "boot_sound": True,
              # always prefer the webcam mic when present; onboard mic is the fallback
              "preferred_input_names": ["C920", "Webcam", "Logitech"]},
    # threshold 0.60 (raised from 0.45 on 2026-08-27, user's call): "Hey Jarvis"
    # scores ~0.99, so the full phrase is unaffected; bare "Jarvis" scores 0.46-0.98
    # by delivery, so it now needs to be said clearly. What forced this: ambient room
    # audio woke him at 0.94 while he was alone, and the turn that followed hit a dead
    # speaker and froze him for 90 minutes. NOTE a 0.94 false positive would still pass
    # this bar — the threshold reduces the mid-range fires; the real protection is that
    # a false wake is now HARMLESS (empty/garbage transcripts are dropped, and the
    # stuck-state watchdog can't let a turn hang). Confusables score 0.00-0.04.
    # semantic_endpoint: end the turn when the SENTENCE is finished, not when
    # the room goes quiet (audio/endpoint.py). Falls back to the fixed window.
    "wake": {"mode": "both", "threshold": 0.60, "semantic_endpoint": True},
    "vision": {
        "model": r"C:\AI\models\gemma-3-4b-it-q4_0.gguf",
        "mmproj": r"C:\AI\models\gemma-3-4b-it-mmproj.gguf",
        "device": "auto",   # auto | cpu ; forced to cpu when the active LLM fills the iGPU
    },
    "conversation": {"window_s": 8},      # follow-up without wake word after a reply
    "interrupt": {"mode": "wake_word"},   # wake_word | any_speech
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
    CONFIG_VERSION = 5

    def _migrate(self) -> bool:
        v = int(self.data.get("config_version", 1) or 1)
        changed = False
        if v < 2:
            # STT: base.en is 3x faster than small.en with equal command accuracy (tests/stt_ab.py)
            if self.data.get("stt", {}).get("model") == "small.en":
                self.data["stt"]["model"] = "base.en"
                changed = True
        # (the old v<3 "add missing models" step is covered by the unconditional mirror below)
        if v < 5:
            self.data.setdefault("stt", {}).setdefault("engine", "parakeet")
            self.data["stt"].setdefault("parakeet_quant", "int8")
            changed = True
        if True:
            # built-in model entries are ours to tune: always mirror DEFAULTS (user-added untouched)
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


def open_db(path: str | Path | None = None, timeout: float = 15.0):
    """Open the JARVIS database the one correct way.

    Four subsystems hold their own connection to this single file (memory,
    brain examples, facts, the tool audit log) and several of them write from
    background tasks — the scheduler firing a reminder while a turn logs a row
    while night school stamps a fact. In SQLite's default rollback-journal mode
    a writer locks the WHOLE database, so those collide and raise
    "database is locked", which surfaces as a dead turn. WAL lets one writer and
    many readers proceed together, and busy_timeout makes the rare true conflict
    wait instead of raising.
    """
    import sqlite3
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False, timeout=timeout)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")   # WAL-safe, far fewer fsyncs
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    except Exception:                                # a read-only or odd FS: still usable
        logging.getLogger("jarvis.config").warning(
            "could not enable WAL on the database", exc_info=True)
    return conn
