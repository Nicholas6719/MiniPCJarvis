"""JARVIS over Telegram — the same one JARVIS, different mouth.

Long-polls the Bot API (outbound only: no ports, no exposure) and runs incoming
messages through the EXACT turn pipeline voice uses — brain reflexes, fact
store, live web, LLM, persona. Replies with the spoken text; images, screenshots
and files ride along as real Telegram media. Risk-gated tools pause on inline
DO IT / NO buttons. Reminders and proactive alerts are pushed to the phone.

Security posture:
- The bot token is DPAPI-encrypted at rest (%APPDATA%/JARVIS/telegram.token.bin,
  decryptable only by this Windows user) — never in config.json, never in git.
- Exactly ONE chat may command JARVIS: pairing binds the first chat that sends
  /pair <code> (code printed to the log and /remote/telegram/status). Everyone
  else gets silence — not even an error, nothing to probe.
- Remote turns run with TTS suppressed (no speaking to an empty room) and the
  confirmation window widened to 120 s (phones are slower than voices).
"""
from __future__ import annotations

import asyncio
import logging
import secrets as pysecrets
import time
from pathlib import Path

import httpx

from config import APP_DIR, config
from events import bus

log = logging.getLogger("jarvis.telegram")

TOKEN_PATH = APP_DIR / "telegram.token.bin"
API = "https://api.telegram.org"


# ---------------------------------------------------------------- token at rest
def save_token(token: str) -> None:
    import win32crypt
    blob = win32crypt.CryptProtectData(token.encode(), "jarvis-telegram", None, None, None, 0)
    TOKEN_PATH.write_bytes(blob)


def load_token() -> str | None:
    try:
        if not TOKEN_PATH.exists():
            return None
        import win32crypt
        _, data = win32crypt.CryptUnprotectData(TOKEN_PATH.read_bytes(), None, None, None, 0)
        return data.decode()
    except Exception:
        log.exception("telegram token unreadable")
        return None


class TelegramBridge:
    def __init__(self) -> None:
        self.token: str | None = None
        self.bot_username = ""
        self.pairing_code: str | None = None
        self._task: asyncio.Task | None = None
        self._orch = None
        self._offset = 0
        self._turn_lock = asyncio.Lock()
        # collected during a remote turn by the bus listener
        self._collect: dict | None = None
        self._turn_done: asyncio.Event | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------- lifecycle
    def start(self, orchestrator) -> None:
        self._orch = orchestrator
        bus.add_listener(self._on_event)
        self.token = load_token()
        if not self.token:
            log.info("telegram bridge dormant: no token stored")
            return
        if not config.get("remote", "telegram_chat_id", default=None):
            self.pairing_code = f"{pysecrets.randbelow(1000000):06d}"
            log.info("telegram pairing code: %s", self.pairing_code)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def set_token(self, token: str) -> dict:
        """Store (encrypted), verify with getMe, (re)start polling."""
        save_token(token.strip())
        self.token = token.strip()
        me = await self._api("getMe")
        self.bot_username = (me or {}).get("username", "")
        if not me:
            return {"ok": False, "error": "Telegram rejected the token"}
        if not config.get("remote", "telegram_chat_id", default=None):
            self.pairing_code = f"{pysecrets.randbelow(1000000):06d}"
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())
        return {"ok": True, "bot": self.bot_username, "pairing_code": self.pairing_code}

    def status(self) -> dict:
        return {"configured": bool(self.token or TOKEN_PATH.exists()),
                "bot": self.bot_username,
                "paired": bool(config.get("remote", "telegram_chat_id", default=None)),
                "pairing_code": self.pairing_code,
                "polling": bool(self._task and not self._task.done())}

    # ------------------------------------------------------------- transport
    async def _api(self, method: str, http_timeout: float = 30, **params):
        """`params` go to Telegram as the JSON body; http_timeout is ours alone.
        (They used to be the same knob, which quietly disabled long polling.)"""
        if not self.token:
            return None
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
        try:
            r = await self._client.post(f"{API}/bot{self.token}/{method}",
                                        json=params, timeout=http_timeout)
            data = r.json()
            if not data.get("ok"):
                log.warning("telegram %s: %s", method, data.get("description"))
                return None
            return data.get("result")
        except Exception:
            log.warning("telegram %s failed", method, exc_info=True)
            return None

    async def _send(self, text: str) -> None:
        chat = config.get("remote", "telegram_chat_id", default=None)
        if chat and text:
            # Telegram caps messages at 4096 chars
            for i in range(0, len(text), 4000):
                await self._api("sendMessage", chat_id=chat, text=text[i:i + 4000])

    async def _send_photo_url(self, url: str, caption: str = "") -> bool:
        chat = config.get("remote", "telegram_chat_id", default=None)
        if not chat:
            return False
        return await self._api("sendPhoto", chat_id=chat, photo=url,
                               caption=caption[:1000]) is not None

    async def _upload(self, path: str, kind: str = "photo", caption: str = "") -> bool:
        """sendPhoto/sendDocument from a local file (multipart)."""
        chat = config.get("remote", "telegram_chat_id", default=None)
        p = Path(path)
        if not chat or not p.exists() or p.stat().st_size > 49_000_000:
            return False
        method, field = ("sendPhoto", "photo") if kind == "photo" else ("sendDocument", "document")
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=10))
            r = await self._client.post(
                f"{API}/bot{self.token}/{method}",
                data={"chat_id": str(chat), "caption": caption[:1000]},
                files={field: (p.name, p.read_bytes())}, timeout=120)
            return bool(r.json().get("ok"))
        except Exception:
            log.warning("telegram upload failed", exc_info=True)
            return False

    # ------------------------------------------------------------- the loop
    async def _poll_loop(self) -> None:
        # httpx logs the full request URL at INFO — and the bot token lives IN that
        # URL, so every poll was writing the token into sidecar.log in plaintext.
        logging.getLogger("httpx").setLevel(logging.WARNING)
        log.info("telegram bridge polling as @%s", self.bot_username or "?")
        me = await self._api("getMe")
        if me:
            self.bot_username = me.get("username", "")
        while True:
            try:
                # `timeout` must be a BODY parameter for Telegram to hold the
                # connection open (long polling). As an httpx kwarg only, every
                # call returned instantly and we hammered the API every ~3 s.
                updates = await self._api("getUpdates", http_timeout=70, timeout=50,
                                          offset=self._offset,
                                          allowed_updates=["message", "callback_query"])
                if updates is None:
                    await asyncio.sleep(5)
                    continue
                for u in updates:
                    self._offset = max(self._offset, u["update_id"] + 1)
                    try:
                        await self._handle_update(u)
                    except Exception:
                        log.exception("telegram update failed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram poll crashed — retrying")
                await asyncio.sleep(10)

    async def _handle_update(self, u: dict) -> None:
        allowed = config.get("remote", "telegram_chat_id", default=None)
        # -- inline confirmation buttons ------------------------------------
        if "callback_query" in u:
            cq = u["callback_query"]
            if allowed and cq.get("from", {}).get("id") == allowed:
                data = cq.get("data", "")
                if data.startswith("confirm:"):
                    _, cid, ans = data.split(":", 2)
                    from tools.registry import registry
                    ok = registry.resolve_confirmation(cid, ans == "yes")
                    await self._api("answerCallbackQuery", callback_query_id=cq["id"],
                                    text="Done." if ok else "That question expired.")
            return
        msg = u.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        sender = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        # -- pairing: the ONLY thing an unpaired bridge responds to ----------
        if not allowed:
            if text.startswith("/pair") and self.pairing_code and \
                    text.split(maxsplit=1)[-1].strip() == self.pairing_code and sender == chat_id:
                config.set("remote", "telegram_chat_id", value=chat_id)
                self.pairing_code = None
                await self._api("sendMessage", chat_id=chat_id,
                                text="Paired. At your service, sir.")
                log.info("telegram paired to chat %s", chat_id)
            return
        if chat_id != allowed or sender != allowed:
            return   # silence for strangers — nothing to probe
        if not text:
            if msg.get("voice"):
                await self._send("Voice notes are coming in the next update — text for now, sir.")
            return
        if text == "/start":
            await self._send("At your service. Ask me anything you would at the PC.")
            return
        await self._remote_turn(text)

    # ------------------------------------------------------------- the turn
    def _on_event(self, evt: dict) -> None:
        """Bus listener (sync): collect what a remote turn produces; forward
        reminders and proactive alerts even when no turn is running."""
        kind = evt.get("kind")
        if kind == "task_due":
            asyncio.ensure_future(self._send(f"Reminder, sir: {evt.get('text', '')}"))
            return
        if kind == "proactive":
            asyncio.ensure_future(self._send(str(evt.get("text") or evt.get("alert") or "")))
            return
        c = self._collect
        if c is None:
            return
        if kind == "turn_done":
            c["text"] = evt.get("text") or c.get("text", "")
            if self._turn_done:
                self._turn_done.set()
        elif kind == "images":
            c["images"] = [im.get("src") for im in (evt.get("images") or [])][:4]
            c["images_query"] = evt.get("query", "")
        elif kind == "tool_call" and evt.get("status") == "success":
            res = evt.get("result")
            if evt.get("tool") in ("take_screenshot", "screenshot_grid") \
                    and isinstance(res, dict) and res.get("path"):
                c["screenshot"] = res["path"]
                c["grid"] = res.get("grid")
        elif kind == "file_preview":
            c["file"] = evt.get("path")
        elif kind == "confirmation_required":
            asyncio.ensure_future(self._send_gate(evt))

    async def _send_gate(self, evt: dict) -> None:
        chat = config.get("remote", "telegram_chat_id", default=None)
        if not chat:
            return
        cid = evt.get("confirm_id", "")
        await self._api("sendMessage", chat_id=chat,
                        text=f"Before I do this — {evt.get('tool', '?').replace('_', ' ')} "
                             f"({evt.get('risk', '?')} risk). Proceed?",
                        reply_markup={"inline_keyboard": [[
                            {"text": "DO IT", "callback_data": f"confirm:{cid}:yes"},
                            {"text": "NO", "callback_data": f"confirm:{cid}:no"}]]})

    @staticmethod
    async def _recycle(path: str) -> None:
        """Send a delivered screenshot to the Recycle Bin — recoverable, not gone."""
        try:
            from tools.file_tools import FOF_ALLOWUNDO, FO_DELETE, _QUIET, _shell_op
            await asyncio.to_thread(_shell_op, FO_DELETE, str(path), None,
                                    _QUIET | FOF_ALLOWUNDO)
        except Exception:
            log.warning("could not recycle %s", path, exc_info=True)

    async def _remote_turn(self, text: str) -> None:
        from tools.registry import registry
        async with self._turn_lock:
            # wait for quiet — a turn sent mid-speech would be dropped
            for _ in range(30):
                if self._orch.sm.state.value in ("idle", "sleeping"):
                    break
                await asyncio.sleep(2)
            await self._orch.wake_if_sleeping()
            self._collect = {"text": ""}
            self._turn_done = asyncio.Event()
            self._orch.remote_turn = True
            registry.confirm_timeout = 120
            await self._api("sendChatAction", chat_id=config.get("remote", "telegram_chat_id"),
                            action="typing")
            try:
                await self._orch.run_text_turn(text)
                try:
                    await asyncio.wait_for(self._turn_done.wait(), timeout=240)
                except asyncio.TimeoutError:
                    pass
                c = self._collect
                await self._send(c.get("text") or "Done, sir.")
                for url in c.get("images", []):
                    if url and not await self._send_photo_url(url):
                        break   # DDG thumbs occasionally refuse Telegram's fetch
                if c.get("screenshot"):
                    await self._upload(c["screenshot"], "photo",
                                       caption=(f'Say "click C4" — grid {c["grid"]}'
                                                if c.get("grid") else ""))
                    # remote screenshots are a MESSAGE, not a file the user wanted:
                    # once it's on the phone it goes to the bin, so the desktop and
                    # the screenshots folder don't fill up with them.
                    if config.get("remote", "recycle_screenshots", default=True):
                        await self._recycle(c["screenshot"])
                if c.get("file"):
                    await self._upload(c["file"], "document")
            finally:
                self._orch.remote_turn = False
                registry.confirm_timeout = 30
                self._collect = None
                self._turn_done = None


telegram = TelegramBridge()
