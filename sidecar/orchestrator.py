"""The JARVIS turn engine: listen → transcribe → think (with tools) → speak.

Owns the voice loop task. Supports push-to-talk toggle, VAD end-of-speech,
barge-in interruption, and stop-words.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid

import numpy as np

from audio.io import mic, speaker, MIC_RATE
from audio.stt import stt
from audio.tts import tts
from audio.vad import StreamingVAD
from audio.wake import wake
from audio.sounds import PALETTE
from audio.speech_text import clean_for_speech
from brain.router import brain
import collections
import re as _re
from config import config
from events import bus
from llm.llama_server import llama
from llm.prompts import system_prompt, turn_context
from llm.provider import local_llm
from memory.store import memory
from state_machine import State, StateMachine
from tools.registry import registry

log = logging.getLogger("jarvis.orchestrator")

WAKE_PHRASE = _re.compile(r"^\s*(?:hey|hi|ok|okay|yo)?[,\s]*jarvis[,.!?\s]*", _re.I)
# explicit requests to go online: the model must not answer from memory
SEARCH_INTENT = re.compile(
    r"\b(search|look\s*up|google|research|find\s+(?:me\s+)?(?:online|on the web)|"
    r"what'?s the latest|latest|current|today'?s|right now|news|price of|weather)\b", re.I)
STOP_WORDS = re.compile(r"^\s*(stop|cancel|never\s*mind|nevermind|shut\s*up|quiet|that's\s+enough)\W*$", re.I)
SENTENCE_END = re.compile(r"([.!?…]+[\s\"')\]]*)")

MAX_UTTERANCE_S = 30
SILENCE_END_S = 0.9          # end of speech after this much silence
WAKE_GRACE_S = 3.5           # after a bare 'Jarvis', wait this long for the request to start
MIN_SPEECH_FRAMES = 3        # ~100ms of speech to count as real


class TurnMetrics:
    """Rolling per-turn latency breakdown (last 50 turns)."""

    def __init__(self) -> None:
        self.turns: list[dict] = []
        self.current: dict = {}

    def begin(self) -> None:
        self.current = {"t0": time.time()}

    def mark(self, key: str) -> None:
        if self.current and key not in self.current:
            self.current[key] = round((time.time() - self.current["t0"]) * 1000)

    def finish(self) -> dict:
        cur = self.current
        cur["total_ms"] = round((time.time() - cur.get("t0", time.time())) * 1000)
        cur.pop("t0", None)
        self.turns.append(cur)
        self.turns = self.turns[-50:]
        self.current = {}
        return cur

    def summary(self) -> dict:
        if not self.turns:
            return {}
        def med(key: str) -> int | None:
            vals = sorted(t[key] for t in self.turns if key in t)
            return vals[len(vals) // 2] if vals else None
        return {"turns": len(self.turns),
                "median_stt_ms": med("stt_ms"),
                "median_first_token_ms": med("first_token_ms"),
                "median_first_audio_ms": med("first_audio_ms"),
                "median_total_ms": med("total_ms")}


class Orchestrator:
    def __init__(self) -> None:
        self.sm = StateMachine()
        self.vad = StreamingVAD()
        self.metrics = TurnMetrics()
        self._history: list[dict] = []
        self._turn_task: asyncio.Task | None = None
        self._listen_flag = asyncio.Event()   # push-to-talk pressed / listening on
        self._speak_cancel = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self._preroll: np.ndarray | None = None     # audio from before the wake word fired
        self._armed_until: float = 0.0               # conversation window (no wake word needed)
        self._sounds = {k: f() for k, f in PALETTE.items()}  # built once, replayed
        self.sm.on_change(self._announce_state)

    # ---------- sound cues ----------

    async def play_sound(self, name: str) -> None:
        if not config.get("audio", "sound_cues", default=True):
            return
        snd = self._sounds.get(name)
        if snd is None:
            return
        try:
            from audio.sounds import RATE
            await speaker.play_chunk(snd, RATE)
        except Exception as e:
            log.debug("sound %s failed: %s", name, e)

    def _arm_conversation(self) -> None:
        """Open the follow-up window: speech alone opens a turn, no wake word."""
        mode = config.get("wake", "mode", default="push_to_talk")
        win = float(config.get("conversation", "window_s", default=8))
        if mode in ("wake_word", "both") and win > 0:
            self._armed_until = time.time() + win
            asyncio.create_task(bus.emit("conversation", armed=True,
                                         until=self._armed_until, window_s=win))

    @property
    def armed(self) -> bool:
        return time.time() < self._armed_until

    async def _announce_state(self, old: State, new: State) -> None:
        await bus.emit("state", state=new.value, prev=old.value)

    # ---------- lifecycle ----------

    async def start(self) -> None:
        await self.sm.to(State.STARTING)
        await bus.emit("boot", summary="initializing subsystems")
        ok = await llama.ensure()
        if not ok:
            await self.sm.to(State.ERROR, force=True)
            await bus.emit("boot_error", summary="language model failed to start — retrying")
            asyncio.create_task(self._llm_retry_loop())
            return
        # warmups are optional at boot — any failure degrades, never wedges
        for label, warm in (("speech recognition", stt.warmup),
                            ("voice synthesis", tts.warmup)):
            try:
                await warm()
            except Exception as e:
                log.error("%s warmup failed (continuing): %s", label, e)
                await bus.emit("boot", summary=f"{label} degraded: {e}")
        try:
            mic.start()
        except Exception as e:
            log.error("microphone unavailable: %s", e)
            await bus.emit("boot", summary="microphone unavailable")
        self._loop_task = asyncio.create_task(self._listen_loop())
        self._wake_task = asyncio.create_task(self._wake_loop())
        self._watchdog_task = asyncio.create_task(self._llm_watchdog())
        self._device_task = asyncio.create_task(self._device_watch())
        if config.get("audio", "boot_sound", default=True):
            asyncio.create_task(self.play_sound("boot"))
        await self.sm.to(State.IDLE)
        await bus.emit("boot", summary="ready")
        # pre-warm the hidden search browser so the first web search is instant
        from search_brave_web import brave_web
        if brave_web.available:
            asyncio.create_task(brave_web.warmup())

    async def _device_watch(self) -> None:
        """Hot-plug: always use the webcam mic when present, fall back to the
        onboard mic when it's gone. Checks Windows' endpoint list (independent
        of PortAudio's cached view) and re-inits audio only on a change."""
        from audio.io import refresh_devices, speaker as _spk
        patterns = [str(x).lower() for x in
                    config.get("audio", "preferred_input_names",
                               default=["C920", "Webcam", "Logitech"])]
        last_switch = 0.0
        while True:
            await asyncio.sleep(15)
            try:
                # self-heal: if the stream is open but no audio has arrived for
                # 6 s (e.g. an exclusive-mode app yanked the device), reopen it
                if (mic._stream is not None and mic.last_frame_at
                        and time.time() - mic.last_frame_at > 6
                        and self.sm.state in (State.IDLE, State.SLEEPING)):
                    log.warning("microphone went silent — reopening")
                    mic.stop()
                    refresh_devices()
                    mic.start()
                    await bus.emit("boot", summary=f"microphone recovered: {mic.device_name}")
                    last_switch = time.time()
                    continue
                if time.time() - last_switch < 300:
                    continue  # never thrash the device: one switch per 5 min max
                if config.get("audio", "input_device") is not None:
                    continue  # user pinned a device explicitly
                if self.sm.state not in (State.IDLE, State.SLEEPING):
                    continue  # never yank the mic mid-conversation
                from pycaw.pycaw import AudioUtilities
                present = False
                for dev in AudioUtilities.GetAllDevices():
                    try:
                        name = (dev.FriendlyName or "").lower()
                        if dev.state == 1 and any(p in name for p in patterns):  # 1 = active
                            present = True
                            break
                    except Exception:
                        continue
                if present != mic.using_preferred:
                    log.info("audio device change: webcam mic %s",
                             "connected" if present else "disconnected")
                    last_switch = time.time()
                    mic.stop()
                    _spk.close()
                    refresh_devices()
                    mic.start()
                    await bus.emit("boot", summary=f"microphone: {mic.device_name}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("device watch: %s", e)

    async def _llm_watchdog(self) -> None:
        """Runtime self-healing: recover a dead llama-server without a restart."""
        while True:
            await asyncio.sleep(60)
            if self.sm.state not in (State.IDLE, State.SLEEPING, State.ERROR):
                continue  # never health-poke mid-turn
            if await llama.healthy():
                if self.sm.state == State.ERROR:
                    await self.sm.to(State.IDLE, force=True)
                continue
            log.warning("llama-server unhealthy — attempting recovery")
            if self.sm.state != State.ERROR:
                await self.sm.to(State.ERROR, force=True)
                await bus.emit("error", summary="language model connection lost — recovering")
            if await llama.ensure():
                await self.sm.to(State.IDLE, force=True)
                await bus.emit("boot", summary="language model recovered")

    async def _llm_retry_loop(self) -> None:
        """Self-healing: keep retrying LLM startup with backoff (e.g. after OOM)."""
        delay = 15
        while self.sm.state == State.ERROR:
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
            log.info("retrying llama-server startup")
            if await llama.ensure():
                await bus.emit("boot", summary="language model recovered")
                await stt.warmup()
                try:
                    await tts.warmup()
                except FileNotFoundError:
                    pass
                mic.start()
                if self._loop_task is None or self._loop_task.done():
                    self._loop_task = asyncio.create_task(self._listen_loop())
                await self.sm.to(State.IDLE, force=True)
                return

    # ---------- proactive announcements ----------

    async def announce(self, text: str) -> None:
        """Speak proactively (reminders etc). Only interrupts IDLE; otherwise
        the message still reaches the user via the event stream/transcript."""
        await bus.emit("announcement", text=text)
        memory.log_turn("assistant", text)
        if self.sm.state != State.IDLE:
            return
        await self.sm.to(State.SPEAKING, force=True)
        cancel = asyncio.Event()
        self._speak_cancel = cancel
        try:
            await self.play_sound("attention")  # 'this wasn't asked for'
            await asyncio.sleep(0.3)
            await bus.emit("speaking", text=text)
            async for chunk in tts.synthesize_stream(clean_for_speech(text), cancel):
                if cancel.is_set():
                    break
                await speaker.play_chunk(chunk, tts.sample_rate)
        finally:
            if self.sm.state == State.SPEAKING:
                await self.sm.to(State.IDLE, force=True)

    # ---------- wake word ----------

    async def _wake_loop(self) -> None:
        """Always-listening detector, active while IDLE.

        - keeps a rolling pre-roll so the words spoken *during* wake-word
          detection ("hey jarvis what time is it") are not lost
        - while the conversation window is armed, plain speech opens a turn
        """
        q = mic.subscribe()
        last_fire = 0.0
        preroll: collections.deque = collections.deque(maxlen=int(MIC_RATE * 2.0 / 1024) + 1)
        armed_vad = StreamingVAD(threshold=0.6)
        consec = 0
        try:
            await asyncio.to_thread(wake.warmup)
        except Exception as e:
            log.error("wake model unavailable: %s", e)
            mic.unsubscribe(q)
            return
        try:
            while True:
                block = await q.get()
                preroll.append(block)
                mode = config.get("wake", "mode", default="push_to_talk")
                if mode not in ("wake_word", "both") or self.sm.state != State.IDLE:
                    consec = 0
                    continue
                # follow-up window: speech alone is enough
                if self.armed:
                    probs = armed_vad.feed(block)
                    consec = consec + 1 if any(p >= armed_vad.threshold for p in probs) else 0
                    if consec >= 3:
                        consec = 0
                        log.info("follow-up speech (conversation window)")
                        self._preroll = np.concatenate(list(preroll)[-8:])  # ~0.5 s lead-in
                        self._armed_until = 0.0
                        await bus.emit("conversation", armed=False)
                        self.vad.reset()
                        self._listen_flag.set()
                        continue
                score = await asyncio.to_thread(wake.feed, block)
                if score >= wake.threshold and time.time() - last_fire > 2.0:
                    last_fire = time.time()
                    log.info("wake word detected (%.2f)", score)
                    await bus.emit("wake", score=round(score, 2))
                    wake.reset()
                    self._preroll = np.concatenate(list(preroll))
                    preroll.clear()
                    self.vad.reset()
                    self._listen_flag.set()
                    await self.play_sound("chime")
        except asyncio.CancelledError:
            raise
        finally:
            mic.unsubscribe(q)

    async def shutdown(self) -> None:
        if self._wake_task:
            self._wake_task.cancel()
        if self._loop_task:
            self._loop_task.cancel()
        self._speak_cancel.set()
        mic.stop()
        speaker.close()
        await llama.stop()

    # ---------- listening control (push-to-talk / toggle) ----------

    async def toggle_listen(self) -> None:
        if self.sm.state == State.SPEAKING:
            await self.interrupt()
            return
        if self._listen_flag.is_set():
            self._listen_flag.clear()
        else:
            mic.drain()
            self.vad.reset()
            self._listen_flag.set()

    async def interrupt(self) -> None:
        self._speak_cancel.set()
        speaker.abort()
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        await self.sm.to(State.INTERRUPTED, force=True)
        await bus.emit("interrupted")
        await self.sm.to(State.IDLE)

    # ---------- main listen loop ----------

    async def _listen_loop(self) -> None:
        while True:
            await self._listen_flag.wait()
            if self.sm.state in (State.IDLE, State.INTERRUPTED):
                await self.sm.to(State.LISTENING)
                utterance = await self._capture_utterance()
                self._listen_flag.clear()
                if utterance is None or len(utterance) < MIC_RATE // 4:
                    await self.sm.to(State.IDLE)
                    continue
                self._turn_task = asyncio.create_task(self._run_turn(utterance))
                try:
                    await self._turn_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    log.exception("voice turn failed")
                    await bus.emit("error", summary=f"turn failed: {e}")
                    await self.sm.to(State.IDLE, force=True)
            else:
                await asyncio.sleep(0.05)

    async def _capture_utterance(self) -> np.ndarray | None:
        """Record until VAD detects end-of-speech, PTT released, or timeout."""
        buf: list[np.ndarray] = []
        speech_frames = 0
        last_speech_t: float | None = None
        t0 = time.time()
        self.vad.reset()
        lead_in = self._preroll
        self._preroll = None
        woke_by_name = False
        new_speech_frames = 0
        if lead_in is not None and len(lead_in):
            # the wake phrase (and any words already spoken) live in here;
            # count it as speech so a command said in one breath is kept
            buf.append(lead_in)
            speech_frames = MIN_SPEECH_FRAMES
            last_speech_t = time.time()
            woke_by_name = True
        mic.drain()
        while True:
            if time.time() - t0 > MAX_UTTERANCE_S:
                break
            if not self._listen_flag.is_set() and buf:
                break  # PTT released
            try:
                block = await asyncio.wait_for(mic.queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                if not self._listen_flag.is_set():
                    break
                continue
            buf.append(block)
            probs = self.vad.feed(block)
            if any(p >= self.vad.threshold for p in probs):
                n = sum(1 for p in probs if p >= self.vad.threshold)
                speech_frames += n
                new_speech_frames += n
                last_speech_t = time.time()
            # After a bare "Jarvis" people often pause before the request.
            # Until new speech actually starts, allow a longer grace period
            # instead of the normal end-of-speech silence.
            end_silence = (WAKE_GRACE_S if (woke_by_name and new_speech_frames < MIN_SPEECH_FRAMES)
                           else SILENCE_END_S)
            if (last_speech_t is not None
                    and speech_frames >= MIN_SPEECH_FRAMES
                    and time.time() - last_speech_t > end_silence):
                break
        if speech_frames < MIN_SPEECH_FRAMES:
            return None
        return np.concatenate(buf) if buf else None

    # ---------- one conversational turn ----------

    async def run_text_turn(self, text: str) -> None:
        """Typed input path — same pipeline, no STT."""
        await self.sm.to(State.PROCESSING, force=True)
        self.metrics.begin()
        await bus.emit("transcript", role="user", text=text, source="text")
        try:
            await self._converse(text, time.time())
        except Exception as e:
            # a failed turn must never wedge the state machine
            log.exception("text turn failed")
            await bus.emit("error", summary=f"turn failed: {e}")
            await self.sm.to(State.IDLE, force=True)

    async def _run_turn(self, audio: np.ndarray) -> None:
        await self.sm.to(State.PROCESSING)
        t_start = time.time()
        self.metrics.begin()
        text = await stt.transcribe(audio)
        self.metrics.mark("stt_ms")
        text = WAKE_PHRASE.sub("", text or "", count=1).strip()
        await bus.emit("transcript", role="user", text=text,
                       stt_ms=int((time.time() - t_start) * 1000))
        if not text:
            # just the wake word — acknowledge and open the window
            await self.sm.to(State.SPEAKING, force=True)
            cancel = asyncio.Event()
            self._speak_cancel = cancel
            try:
                async for chunk in tts.synthesize_stream("Yes?", cancel):
                    if cancel.is_set():
                        break
                    await speaker.play_chunk(chunk, tts.sample_rate)
            finally:
                await self.sm.to(State.IDLE, force=True)
            self._arm_conversation()
            return
        if STOP_WORDS.match(text):
            await self.sm.to(State.IDLE)
            return
        await self._converse(text, t_start)
        self._arm_conversation()

    async def _converse(self, text: str, t_start: float) -> None:
        memory.log_turn("user", text)
        # ---- reflex: JARVIS's own brain handles known requests without the LLM ----
        reflex = None
        if config.get("brain", "enabled", default=True):
            try:
                reflex = await brain.decide(text)
            except Exception:
                log.exception("brain decide failed - falling back to the LLM")
        if reflex and not reflex[0].llm_after:
            await self._reflex_turn(text, reflex, t_start)
            return
        try:
            mem_hits = await memory.search(text, top_k=4)
        except Exception:
            log.exception("memory search failed — continuing without recall")
            mem_hits = []
        pinned = memory.list_pinned()
        lines = [f"- {p}" for p in pinned]
        lines += [f"- {m['content']}" for m in mem_hits
                  if m["content"] not in pinned]
        mem_ctx = "\n".join(lines)

        # Static prefix (persona + tools) is identical every turn -> KV-cache hit.
        # Time + memories ride along inside the latest user message instead.
        messages: list[dict] = [{"role": "system", "content": system_prompt()}]
        messages += self._history[-10:]
        messages.append({"role": "user", "content": turn_context(mem_ctx) + chr(10) + text})

        if reflex:
            # brain knew which tool to run; run it now and let the LLM compose the answer
            skill, args, conf = reflex
            brain.stats["reflex"] += 1
            await bus.emit("reflex", skill=skill.name, tool=skill.tool, args=args,
                           confidence=conf, mode="tool_then_llm")
            await self.sm.to(State.SEARCHING if skill.tool in ("web_search", "research")
                             else State.EXECUTING, force=True)
            result = await registry.execute(skill.tool, args)
            call_id = "reflex-" + uuid.uuid4().hex[:8]
            messages.append({"role": "assistant", "content": None, "tool_calls": [
                {"id": call_id, "type": "function",
                 "function": {"name": skill.tool, "arguments": json.dumps(args)}}]})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "content": json.dumps(result, default=str)})
        else:
            brain.stats["llm"] += 1

        await self.sm.to(State.THINKING)
        self._speak_cancel = asyncio.Event()
        speak_queue: asyncio.Queue[str | None] = asyncio.Queue()
        speaker_task = asyncio.create_task(self._speaker_worker(speak_queue))
        full_reply = ""
        try:
            full_reply = await self._llm_with_tools(messages, speak_queue)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("turn failed")
            await bus.emit("error", summary=str(e))
            await speak_queue.put("I hit a problem with that. Give me a moment.")
        finally:
            await speak_queue.put(None)
            try:
                await speaker_task
            except asyncio.CancelledError:
                pass

        if full_reply:
            self._history.append({"role": "user", "content": text})
            self._history.append({"role": "assistant", "content": full_reply})
            self._history = self._history[-20:]
            memory.log_turn("assistant", full_reply)
        breakdown = self.metrics.finish()
        await bus.emit("turn_done", latency_ms=int((time.time() - t_start) * 1000),
                       breakdown=breakdown)
        if self.sm.state != State.ERROR:
            await self.sm.to(State.IDLE, force=True)

    async def _reflex_turn(self, text: str, reflex, t_start: float) -> None:
        """Handle a request JARVIS recognized himself: tool + templated speech, no LLM."""
        skill, args, conf = reflex
        brain.stats["reflex"] += 1
        await bus.emit("reflex", skill=skill.name, tool=skill.tool, args=args,
                       confidence=conf, mode="direct")
        res: dict = {}
        if skill.tool:
            await self.sm.to(State.EXECUTING, force=True)
            out = await registry.execute(skill.tool, args)
            res = out.get("result") if out.get("ok") else {"error": out.get("error", "failed")}
            if not isinstance(res, dict):
                res = {"value": res}
        try:
            reply = skill.speak(args, res)
        except Exception:
            log.exception("reflex speak template failed")
            reply = "Done." if "error" not in res else "That didn't work."
        self.metrics.mark("first_token_ms")
        await bus.emit("assistant_delta", text=reply)
        self._speak_cancel = asyncio.Event()
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        task = asyncio.create_task(self._speaker_worker(queue))
        await queue.put(clean_for_speech(reply))
        await queue.put(None)
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": reply})
        self._history = self._history[-20:]
        memory.log_turn("assistant", reply)
        breakdown = self.metrics.finish()
        breakdown["reflex"] = skill.name
        await bus.emit("turn_done", latency_ms=int((time.time() - t_start) * 1000),
                       breakdown=breakdown, reflex=skill.name)
        if self.sm.state != State.ERROR:
            await self.sm.to(State.IDLE, force=True)

    async def _llm_with_tools(self, messages: list[dict],
                              speak_queue: asyncio.Queue) -> str:
        """Run the LLM, executing tool calls in a loop, streaming sentences to TTS."""
        tools = registry.schemas()
        full_text = ""
        empty_retries = 0
        user_text = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        must_use_tool = bool(SEARCH_INTENT.search(user_text or ""))
        used_tools: list[tuple[str, bool]] = []   # (name, ok) - for self-training
        raw_user = user_text.split(chr(10))[-1] if user_text else ""
        for _round in range(8):
            round_text = ""
            pending = ""
            tool_calls: list[dict] | None = None
            # generous budget: gpt-oss spends tokens on hidden reasoning first —
            # a tight cap silently starves the spoken reply (see Houston notes)
            cancelled = False
            # first round of an explicit search/lookup request: a tool call is required
            choice = "required" if (must_use_tool and _round == 0) else None
            async for chunk in local_llm.stream(messages, tools=tools,
                                                max_tokens=4096, tool_choice=choice):
                if self._speak_cancel.is_set():
                    # user interrupted: stop generating AND stop streaming to the UI
                    cancelled = True
                    break
                if chunk.text:
                    self.metrics.mark("first_token_ms")
                    pending += chunk.text
                    round_text += chunk.text
                    full_text += chunk.text
                    await bus.emit("assistant_delta", text=chunk.text)
                    # flush complete sentences to TTS
                    while True:
                        m = SENTENCE_END.search(pending)
                        if not m:
                            break
                        sentence = pending[: m.end()].strip()
                        pending = pending[m.end():]
                        if sentence:
                            await speak_queue.put(clean_for_speech(sentence))
                if chunk.done:
                    tool_calls = chunk.tool_calls
                    break
            if cancelled:
                return full_text
            if pending.strip():
                await speak_queue.put(clean_for_speech(pending.strip()))

            if not tool_calls:
                if not round_text.strip() and empty_retries < 1:
                    # empty round (reasoning ate the budget) — nudge once
                    empty_retries += 1
                    log.warning("empty LLM round — retrying with a nudge")
                    messages.append({"role": "user", "content":
                                     "(Continue: finish the task or answer now, "
                                     "in one or two spoken sentences.)"})
                    continue
                if not full_text.strip():
                    # never end a turn in silence
                    fallback = "Sorry, I lost my train of thought. Ask me again?"
                    await bus.emit("assistant_delta", text=fallback)
                    await speak_queue.put(clean_for_speech(fallback))
                    return fallback
                await self._maybe_learn(raw_user, used_tools)
                return full_text

            # execute tools, then loop for the model's follow-up
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in tool_calls],
            })
            for tc in tool_calls:
                state = (State.SEARCHING
                         if tc["name"] in ("web_search", "research", "fetch_page")
                         else State.EXECUTING)
                await self.sm.to(state, force=True)
                result = await registry.execute(tc["name"], tc["arguments"])
                used_tools.append((tc["name"], bool(result.get("ok"))))
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                })
            await self.sm.to(State.THINKING, force=True)
        return full_text

    async def _maybe_learn(self, user_text: str, used_tools: list[tuple[str, bool]]) -> None:
        """Self-training: a turn the LLM solved with exactly one successful known tool
        teaches the brain that phrasing -> skill, so next time it's a reflex."""
        if len(used_tools) != 1 or not used_tools[0][1]:
            return
        skill = brain.learned_from_tool(used_tools[0][0])
        if not skill or not user_text.strip():
            return
        try:
            if await brain.learn(user_text, skill):
                await bus.emit("brain_learned", text=user_text, skill=skill,
                               examples=brain.example_count)
        except Exception:
            log.exception("brain learn failed")

    async def _speaker_worker(self, queue: asyncio.Queue) -> None:
        """Consumes sentences, synthesizes and plays them; watches for barge-in."""
        barge_task = asyncio.create_task(self._barge_in_watch())
        spoke = False
        try:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    break
                if self._speak_cancel.is_set():
                    continue
                if not spoke:
                    await self.sm.to(State.SPEAKING, force=True)
                    spoke = True
                await bus.emit("speaking", text=sentence)
                async for chunk in tts.synthesize_stream(sentence, self._speak_cancel):
                    if self._speak_cancel.is_set():
                        break
                    self.metrics.mark("first_audio_ms")
                    await speaker.play_chunk(chunk, tts.sample_rate)
                if self._speak_cancel.is_set():
                    break  # interrupted — drop any remaining queued sentences
        finally:
            barge_task.cancel()

    async def _barge_in_watch(self) -> None:
        """While speaking, watch for the user cutting in.

        interrupt.mode = "wake_word" (default): only his name interrupts him —
        immune to his own voice bleeding from speakers into the mic.
        interrupt.mode = "any_speech": any sustained speech interrupts
        (needs a headset or good echo isolation).
        """
        vad = StreamingVAD(threshold=0.75)
        consec = 0
        q = mic.subscribe()
        try:
            while True:
                block = await q.get()
                if self.sm.state != State.SPEAKING:
                    consec = 0
                    continue
                mode = config.get("interrupt", "mode", default="wake_word")
                fired = False
                if mode == "any_speech":
                    probs = vad.feed(block)
                    consec = (consec + sum(1 for p in probs if p >= vad.threshold)
                              if any(p >= vad.threshold for p in probs) else 0)
                    fired = consec >= 6
                else:
                    score = await asyncio.to_thread(wake.feed, block)
                    fired = score >= wake.threshold
                if fired:
                    log.info("barge-in detected (%s)", mode)
                    self._speak_cancel.set()
                    speaker.abort()
                    wake.reset()
                    await self.sm.to(State.INTERRUPTED, force=True)
                    await bus.emit("interrupted", reason="barge-in")
                    mic.drain()
                    self.vad.reset()
                    self._preroll = None
                    self._listen_flag.set()  # capture what the user is saying
                    await self.sm.to(State.LISTENING, force=True)
                    await self.play_sound("chime")  # same cue: 'listening now'
                    return
        except asyncio.CancelledError:
            raise
        finally:
            mic.unsubscribe(q)


orchestrator = Orchestrator()
