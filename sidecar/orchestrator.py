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

import numpy as np

from audio.io import mic, speaker, MIC_RATE
from audio.stt import stt
from audio.tts import tts
from audio.vad import StreamingVAD
from audio.wake import wake
from events import bus
from llm.llama_server import llama
from llm.prompts import system_prompt
from llm.provider import local_llm
from memory.store import memory
from state_machine import State, StateMachine
from tools.registry import registry

log = logging.getLogger("jarvis.orchestrator")

STOP_WORDS = re.compile(r"^\s*(stop|cancel|never\s*mind|nevermind|shut\s*up|quiet|that's\s+enough)\W*$", re.I)
SENTENCE_END = re.compile(r"([.!?…]+[\s\"')\]]*)")

MAX_UTTERANCE_S = 30
SILENCE_END_S = 0.9          # end of speech after this much silence
MIN_SPEECH_FRAMES = 3        # ~100ms of speech to count as real


class Orchestrator:
    def __init__(self) -> None:
        self.sm = StateMachine()
        self.vad = StreamingVAD()
        self._history: list[dict] = []
        self._turn_task: asyncio.Task | None = None
        self._listen_flag = asyncio.Event()   # push-to-talk pressed / listening on
        self._speak_cancel = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._wake_task: asyncio.Task | None = None
        self.sm.on_change(self._announce_state)

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
        await stt.warmup()
        try:
            await tts.warmup()
        except FileNotFoundError as e:
            log.error("tts voice missing: %s", e)
        mic.start()
        self._loop_task = asyncio.create_task(self._listen_loop())
        self._wake_task = asyncio.create_task(self._wake_loop())
        await self.sm.to(State.IDLE)
        await bus.emit("boot", summary="ready")

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

    # ---------- wake word ----------

    async def _wake_loop(self) -> None:
        """Always-listening 'hey jarvis' detector; active only while IDLE."""
        q = mic.subscribe()
        last_fire = 0.0
        try:
            await asyncio.to_thread(wake.warmup)
        except Exception as e:
            log.error("wake model unavailable: %s", e)
            mic.unsubscribe(q)
            return
        try:
            while True:
                block = await q.get()
                mode = config.get("wake", "mode", default="push_to_talk")
                if mode not in ("wake_word", "both") or self.sm.state != State.IDLE:
                    # stay subscribed but ignore audio; cheap enough
                    continue
                score = await asyncio.to_thread(wake.feed, block)
                if score >= wake.threshold and time.time() - last_fire > 2.0:
                    last_fire = time.time()
                    log.info("wake word detected (%.2f)", score)
                    await bus.emit("wake", score=round(score, 2))
                    wake.reset()
                    mic.drain()
                    self.vad.reset()
                    self._listen_flag.set()
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
            else:
                await asyncio.sleep(0.05)

    async def _capture_utterance(self) -> np.ndarray | None:
        """Record until VAD detects end-of-speech, PTT released, or timeout."""
        buf: list[np.ndarray] = []
        speech_frames = 0
        last_speech_t: float | None = None
        t0 = time.time()
        self.vad.reset()
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
                speech_frames += sum(1 for p in probs if p >= self.vad.threshold)
                last_speech_t = time.time()
            if (last_speech_t is not None
                    and speech_frames >= MIN_SPEECH_FRAMES
                    and time.time() - last_speech_t > SILENCE_END_S):
                break
        if speech_frames < MIN_SPEECH_FRAMES:
            return None
        return np.concatenate(buf) if buf else None

    # ---------- one conversational turn ----------

    async def run_text_turn(self, text: str) -> None:
        """Typed input path — same pipeline, no STT."""
        await self.sm.to(State.PROCESSING, force=True)
        await bus.emit("transcript", role="user", text=text, source="text")
        await self._converse(text, time.time())

    async def _run_turn(self, audio: np.ndarray) -> None:
        await self.sm.to(State.PROCESSING)
        t_start = time.time()
        text = await stt.transcribe(audio)
        await bus.emit("transcript", role="user", text=text,
                       stt_ms=int((time.time() - t_start) * 1000))
        if not text:
            await self.sm.to(State.IDLE)
            return
        if STOP_WORDS.match(text):
            await self.sm.to(State.IDLE)
            return
        await self._converse(text, t_start)

    async def _converse(self, text: str, t_start: float) -> None:
        memory.log_turn("user", text)
        mem_hits = await memory.search(text, top_k=4)
        mem_ctx = "\n".join(f"- {m['content']}" for m in mem_hits)

        messages: list[dict] = [{"role": "system", "content": system_prompt(mem_ctx)}]
        messages += self._history[-10:]
        messages.append({"role": "user", "content": text})

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
        await bus.emit("turn_done", latency_ms=int((time.time() - t_start) * 1000))
        if self.sm.state != State.ERROR:
            await self.sm.to(State.IDLE, force=True)

    async def _llm_with_tools(self, messages: list[dict],
                              speak_queue: asyncio.Queue) -> str:
        """Run the LLM, executing tool calls in a loop, streaming sentences to TTS."""
        tools = registry.schemas()
        full_text = ""
        for _round in range(6):
            pending = ""
            tool_calls: list[dict] | None = None
            async for chunk in local_llm.stream(messages, tools=tools):
                if chunk.text:
                    pending += chunk.text
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
                            await speak_queue.put(sentence)
                if chunk.done:
                    tool_calls = chunk.tool_calls
                    break
            if pending.strip():
                await speak_queue.put(pending.strip())

            if not tool_calls:
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
                state = State.SEARCHING if tc["name"] == "web_search" else State.EXECUTING
                await self.sm.to(state, force=True)
                result = await registry.execute(tc["name"], tc["arguments"])
                messages.append({
                    "role": "tool", "tool_call_id": tc["id"],
                    "content": json.dumps(result, default=str),
                })
            await self.sm.to(State.THINKING, force=True)
        return full_text

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
                    await speaker.play_chunk(chunk, tts.sample_rate)
                if self._speak_cancel.is_set():
                    break  # interrupted — drop any remaining queued sentences
        finally:
            barge_task.cancel()

    async def _barge_in_watch(self) -> None:
        """While speaking, watch the mic for user speech → interrupt."""
        vad = StreamingVAD(threshold=0.75)
        consec = 0
        q = mic.subscribe()
        try:
            while True:
                block = await q.get()
                if self.sm.state != State.SPEAKING:
                    consec = 0
                    continue
                probs = vad.feed(block)
                consec = consec + sum(1 for p in probs if p >= vad.threshold) \
                    if any(p >= vad.threshold for p in probs) else 0
                if consec >= 6:  # ~200ms of sustained speech over TTS output
                    log.info("barge-in detected")
                    self._speak_cancel.set()
                    speaker.abort()
                    await self.sm.to(State.INTERRUPTED, force=True)
                    await bus.emit("interrupted", reason="barge-in")
                    mic.drain()
                    self.vad.reset()
                    self._listen_flag.set()  # capture what the user is saying
                    await self.sm.to(State.LISTENING, force=True)
                    return
        except asyncio.CancelledError:
            raise
        finally:
            mic.unsubscribe(q)


orchestrator = Orchestrator()
