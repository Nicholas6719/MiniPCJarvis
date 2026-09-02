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
from events import bus, spawn

log = logging.getLogger("jarvis.telegram")

MAX_VOICE_BYTES = 20_000_000        # Telegram's own download ceiling anyway

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



def _health_payload(text: str) -> bool:
    """Is this telemetry rather than something he said? Never raises: a bad
    detector here must not be able to swallow a real message or crash the
    poller, so any failure answers 'no' and the text is treated as speech."""
    try:
        from tools.health import looks_like_payload
        return looks_like_payload(text)
    except Exception:
        log.debug("health payload sniff failed", exc_info=True)
        return False

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
        # urgent messages awaiting acknowledgement, token -> {text, at, sent}
        self._urgent: dict[str, dict] = {}

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
                # "edited_message" is REQUIRED for live location. Telegram does
                # not merely ignore an update kind that is missing from this
                # list — it does not send it at all — and a live location share
                # is delivered by EDITING the original message each time he
                # moves. Without this the feature would have looked broken with
                # nothing in any log to explain why.
                updates = await self._api("getUpdates", http_timeout=70, timeout=50,
                                          offset=self._offset,
                                          allowed_updates=["message", "edited_message",
                                                           "callback_query"])
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
            # A button press has to be recognised as HIS, and the old test asked
            # whether the USER id equalled the CHAT id. Those are the same number
            # in a private chat and different everywhere else, and the value is
            # stored as whatever Telegram first sent - so a type or shape mismatch
            # silently dropped every tap. On 2026-08-31 he tapped "Got it" three
            # times and was chased anyway. Accept either identifier, compared as
            # text so 12345 and "12345" cannot disagree.
            who = {str(cq.get("from", {}).get("id")),
                   str((cq.get("message") or {}).get("chat", {}).get("id"))}
            if allowed and str(allowed) in who:
                data = cq.get("data", "")
                if data.startswith("confirm:"):
                    _, cid, ans = data.split(":", 2)
                    from tools.registry import registry
                    ok = registry.resolve_confirmation(cid, ans == "yes")
                    await self._api("answerCallbackQuery", callback_query_id=cq["id"],
                                    text="Done." if ok else "That question expired.")
                elif data.startswith("ack:"):
                    tok = data.split(":", 1)[1]
                    had = self._urgent.pop(tok, None)
                    log.info("telegram ack %s - %s", tok,
                             "chase stopped" if had else "nothing was pending")
                    await self._api("answerCallbackQuery", callback_query_id=cq["id"],
                                    text="Noted, sir.")
                elif data.startswith("clarify:"):
                    # tapping "The stock" is exactly saying it — it goes back in
                    # as the next thing he said, and the answer for it is already
                    # fetched and waiting
                    choice = data.split(":", 1)[1]
                    await self._api("answerCallbackQuery", callback_query_id=cq["id"])
                    spawn(self._remote_turn(choice), name="tg-clarify")
            elif allowed:
                log.warning("telegram: ignoring a button press from %s (paired to %s)",
                            who, allowed)
            return
        # An EDITED message is how a live location moves: Telegram edits the
        # original rather than sending a new one. Everything below applies to it
        # unchanged, including the allowed-chat check.
        edited = "edited_message" in u
        msg = u.get("message") or u.get("edited_message") or {}
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

        # -- phone-derived data, from the paired chat only --------------------
        # Deliberately AFTER the allowed-chat check and before anything is
        # treated as speech. These are readings, not requests: they are stored
        # and acknowledged quietly, never run as a turn.
        loc = msg.get("location")
        if loc:
            from tools import location as _loc
            ok = _loc.ingest(loc, label=(msg.get("venue") or {}).get("title", ""))
            # A live share edits the same message every few seconds. Answering
            # each one would be a message storm of exactly the kind that already
            # cost him a night's sleep — acknowledge the first, then stay quiet.
            if ok and not edited:
                await self._send("Got your location, sir.")
            return
        if text and _health_payload(text):
            from tools import health as _health
            res = _health.ingest_payload(text)
            if res.get("error"):
                await self._send(f"I couldn't read that health data, sir — {res['error']}.")
            elif res.get("stored"):
                await self._send(f"Logged {res['stored']} reading"
                                 f"{'s' if res['stored'] != 1 else ''}, sir.")
            else:
                await self._send("Nothing in that payload was a metric I track, sir.")
            return

        if not text:
            voice = msg.get("voice") or msg.get("audio") or msg.get("video_note")
            if voice:
                spoken = await self._hear(voice)
                if spoken:
                    await self._remote_turn(spoken)
                return
            photo = msg.get("photo")
            if photo:
                # Telegram sends the same photo at several sizes, smallest first;
                # the last is the largest and the only one worth looking at.
                await self._look_at_photo(photo[-1], (msg.get("caption") or "").strip())
                return
            if msg.get("document"):
                await self._send("I can't read documents yet, sir — send it as a photo "
                                 "if you want me to look at it.")
            return
        if text == "/start":
            await self._send("At your service. Ask me anything you would at the PC.")
            return
        self.acknowledge_all()        # he is looking at his phone; stop chasing
        await self._remote_turn(text)

    # ------------------------------------------------------------- the turn
    def _on_event(self, evt: dict) -> None:
        """Bus listener (sync): collect what a remote turn produces; forward
        reminders and proactive alerts even when no turn is running."""
        kind = evt.get("kind")
        # Reminders and proactive alerts are NOT forwarded from here any more.
        # delivery.py decides where they go — speak them if he is at the machine,
        # send them if he is not — and it calls send_proactive() itself. Echoing
        # them here as well sent everything twice, once to the room and once to
        # the phone, whether or not he was in the room.
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
            # WHAT THIS TURN ACTUALLY DID to the machine, so the reply can show
            # him rather than assert. See CHANGED_THE_DESKTOP below.
            c.setdefault("did", []).append(evt.get("tool"))
            if evt.get("tool") in ("take_screenshot", "screenshot_grid") \
                    and isinstance(res, dict) and res.get("path"):
                c["screenshot"] = res["path"]
                c["grid"] = res.get("grid")
        elif kind == "file_preview":
            c["file"] = evt.get("path")
        elif kind == "confirmation_required":
            spawn(self._send_gate(evt), name="tg-gate")
        elif kind == "clarify":
            # the question carries its own options, so the plain reply text would
            # only repeat it
            c["asked"] = True
            spawn(self._send_choice(evt), name="tg-clarify-ask")

    # TOOLS THAT CHANGE WHAT HIS SCREEN LOOKS LIKE.
    #
    # His instruction, verbatim: "if I ever ask Jarvis to do anything on my
    # computer — minimizing something, opening something, deleting something,
    # whatever I ask him to do — he should always send me a screenshot so I can
    # see that he had done it and then I can instruct him further afterwards."
    #
    # That is a better protocol than words, because from the phone he cannot
    # check. "File removed, sir." is a claim; a picture of the desktop is
    # evidence, and it is also the context for his next instruction — in the
    # exchange that prompted this he had to type "show me" after every single
    # action.
    #
    # An explicit set rather than a risk tier: LOW covers plenty of tools that
    # change nothing visible (remembering a fact, setting a watch), and a
    # screenshot after those is noise. ADD TO THIS LIST when a new tool moves
    # something on screen.
    CHANGED_THE_DESKTOP = {
        "minimize_window", "maximize_window", "close_window", "focus_window",
        "show_desktop", "restore_windows",
        "open_application", "close_application", "open_url", "open_with_windows",
        "search_in_browser",
        "delete_file", "move_file", "rename_file", "empty_recycle_bin",
        "restore_from_recycle_bin",
        "click_screen", "type_text", "press_keys", "scroll_screen",
    }

    MAX_PHOTO_BYTES = 15_000_000

    async def _look_at_photo(self, photo: dict, caption: str = "") -> None:
        """A photo he sent, described back to him. Never raises into the poller.

        Downloaded to a temp file and DELETED afterwards. A picture he sent from
        his phone is not something this program should quietly accumulate on
        disk — the vision model needs a path, and that is the only reason a copy
        exists at all.
        """
        import os
        import tempfile
        size = int(photo.get("file_size") or 0)
        if size > self.MAX_PHOTO_BYTES:
            await self._send(f"That photo is {size // 1_000_000} MB, sir — a little "
                             "large for me to look at.")
            return
        meta = await self._api("getFile", file_id=photo.get("file_id"))
        path = (meta or {}).get("file_path")
        if not path:
            await self._send("I couldn't fetch that photo, sir.")
            return
        tmp = None
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=10))
            r = await self._client.get(f"{API}/file/bot{self.token}/{path}", timeout=90)
            r.raise_for_status()
            suffix = os.path.splitext(path)[1] or ".jpg"
            fd, tmp = tempfile.mkstemp(prefix="jarvis-tg-", suffix=suffix)
            with os.fdopen(fd, "wb") as fh:
                fh.write(r.content)
            from tools.vision_analyze import analyze_object
            res = await analyze_object(tmp, caption)
            await self._send(res.get("analysis") or
                             f"I couldn't make that out, sir — {res.get('error', 'no idea why')}.")
        except Exception:
            log.exception("telegram photo analysis failed")
            await self._send("I couldn't look at that photo, sir.")
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    log.debug("could not remove %s", tmp, exc_info=True)

    async def _hear(self, voice: dict) -> str:
        """A voice note, in his own words. Downloads it, decodes it and runs the
        same recogniser the microphone uses, so talking to him from the other
        side of the country is the same as talking to him from the chair."""
        from audio.decode import Undecodable, to_pcm16k
        from audio.stt import stt

        size = int(voice.get("file_size") or 0)
        if size > MAX_VOICE_BYTES:
            await self._send(f"That note is {size // 1_000_000} MB, sir — a little long "
                             "for me. Under a couple of minutes, if you would.")
            return ""
        meta = await self._api("getFile", file_id=voice.get("file_id"))
        path = (meta or {}).get("file_path")
        if not path:
            await self._send("I couldn't fetch that voice note, sir.")
            return ""
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10))
            r = await self._client.get(f"{API}/file/bot{self.token}/{path}", timeout=60)
            r.raise_for_status()
            audio = await asyncio.to_thread(to_pcm16k, r.content)
        except Undecodable as e:
            log.warning("voice note undecodable: %s", e)
            await self._send("I couldn't make out that recording, sir.")
            return ""
        except Exception:
            log.exception("voice note download failed")
            await self._send("I couldn't fetch that voice note, sir.")
            return ""
        heard = (await stt.transcribe(audio) or "").strip()
        if not heard:
            await self._send("I couldn't make out that recording, sir.")
            return ""
        log.info("voice note: %.1fs -> %r", len(audio) / 16000, heard[:60])
        # Show what was understood before acting on it: a misheard word is
        # otherwise invisible, and he is not in the room to notice.
        await self._send(f"“{heard}”")
        return heard

    async def send_proactive(self, text: str, tier: str = "notable",
                             subject: str = "") -> None:
        """Something he did not ask for, reaching him where he is.

        Urgent carries an acknowledge button and keeps asking until he taps it:
        the whole point of the tier is that it must not be missed. Everything
        else is said once.
        """
        chat = config.get("remote", "telegram_chat_id", default=None)
        if not chat or not text:
            return
        if tier != "urgent":
            await self._send(text)
            return
        token = pysecrets.token_hex(4)
        self._urgent[token] = {"text": text, "at": time.time(), "sent": 1}
        await self._api("sendMessage", chat_id=chat, text=text,
                        reply_markup={"inline_keyboard": [[
                            {"text": "Got it", "callback_data": f"ack:{token}"}]]})
        log.info("telegram urgent %s sent, chasing until acknowledged: %r",
                 token, text[:70])
        spawn(self._chase(token), name="tg-urgent-chase")

    async def _chase(self, token: str) -> None:
        """Keep asking until he acknowledges.

        Telegram bots cannot place calls — there is no such method in the Bot
        API — so this is the honest substitute Nicholas chose: repeat, on a
        widening interval, and stop the moment he answers. His phone is never
        silenced, which is the one weakness of doing it this way.
        """
        from delivery import URGENT, _in_quiet_hours, delivery
        chat = config.get("remote", "telegram_chat_id", default=None)
        # ONE follow-up at night, three by day. The chase is deliberate and he
        # asked for it, but it reaches a phone beside a sleeping man: three
        # repeats per alert, times however many alerts fire, is how a safety
        # feature turns into the thing he switches off.
        waits = (600,) if _in_quiet_hours() else (300, 300, 600)
        for wait in waits:
            await asyncio.sleep(wait)
            item = self._urgent.get(token)
            if not item:
                log.info("telegram chase %s stopping - he acknowledged it", token)
                return                              # acknowledged, or replied to
            # A chase is a message on his phone like any other, so it answers to
            # the same hourly ceiling. Without this the cap counted only the
            # FIRST send of each alert and quietly permitted four times as many.
            if not delivery.has_budget(URGENT):
                log.warning("telegram chase %s stopping - the hourly message "
                            "budget is spent", token)
                break
            item["sent"] += 1
            await self._api(
                "sendMessage", chat_id=chat,
                text=f"Still unanswered, sir — {item['text']}",
                reply_markup={"inline_keyboard": [[
                    {"text": "Got it", "callback_data": f"ack:{token}"}]]})
            delivery.note_sent()
        if self._urgent.pop(token, None):
            log.info("telegram chase %s giving up - never acknowledged", token)

    def acknowledge_all(self) -> int:
        """Any message from him counts as having seen it."""
        n = len(self._urgent)
        if n:
            log.info("telegram: he replied, so %d chase(s) stop", n)
        self._urgent.clear()
        return n

    async def _send_choice(self, evt: dict) -> None:
        """Ask which reading he meant — one tap, no typing. Both answers are
        already being fetched while this sits on his phone."""
        chat = config.get("remote", "telegram_chat_id", default=None)
        if not chat:
            return
        opts = [str(o) for o in (evt.get("options") or [])][:3]
        await self._api("sendMessage", chat_id=chat,
                        text=str(evt.get("question") or "Which did you mean, sir?"),
                        reply_markup={"inline_keyboard": [[
                            {"text": o.title(), "callback_data": f"clarify:{o}"}
                            for o in opts]]})

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
            # NOT surfacing. A message from his phone must not wake the PC's
            # monitor or pull the window to the front — he found this himself,
            # messaging at night with the screen off and having the screen come
            # on. The state machine still has to leave SLEEPING, because the turn
            # path only runs from IDLE.
            await self._orch.wake_if_sleeping(surface=False)
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
                # SHOW HIM, DON'T TELL HIM. If the turn moved something on his
                # desktop and did not already take a picture, take one now — he
                # should never have to type "show me" after asking for something
                # to be minimised or deleted, and the picture is the context for
                # whatever he says next.
                if (not c.get("screenshot")
                        and set(c.get("did") or []) & self.CHANGED_THE_DESKTOP
                        and config.get("remote", "screenshot_after_actions",
                                       default=True)):
                    try:
                        shot = await registry.execute("take_screenshot", {})
                        got = (shot or {}).get("result") or {}
                        if isinstance(got, dict) and got.get("path"):
                            c["screenshot"] = got["path"]
                    except Exception:
                        log.warning("could not photograph the result", exc_info=True)

                if not c.get("asked"):        # the question went with its buttons
                    line = c.get("text") or ""
                    # NO "Done, sir." IN FRONT OF A PICTURE. His words: "he
                    # doesn't need to say screenshot saved every time — I'll know
                    # he took a screenshot by him actually showing me." A caption
                    # that says nothing the image does not is one more thing to
                    # read on a phone.
                    if line.strip() or not c.get("screenshot"):
                        await self._send(line or "Done, sir.")
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
