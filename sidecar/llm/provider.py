"""LLMProvider abstraction. Phase 1 ships the local llama-server implementation;
cloud providers slot in behind the same interface later."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from config import config
from llm.llama_server import llama

log = logging.getLogger("jarvis.llm")


@dataclass
class Chunk:
    text: str = ""
    tool_calls: list[dict] | None = None
    done: bool = False
    finish_reason: str | None = None


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)


class LocalLLM:
    """Streams chat completions from the managed llama-server."""

    # What the LAST call cost, from llama-server's own final chunk: prompt
    # tokens (and how many the cache already had), generated tokens, the
    # rates, and how many characters of reasoning came before the answer.
    # The orchestrator files it with the turn (2026-09-18) - the model's
    # time is the only part of a turn that was still a guess.
    last_call: dict = {}
    # A call in flight. Prompt processing emits nothing for tens of seconds
    # on a cold cache; the deaf watch read that as a wedged turn and reset
    # him 35 s into a 41 s read (2026-09-21 20:44).
    working_since: float = 0.0

    # BACKGROUND CALLS WAIT THEIR TURN. Set by the orchestrator: a coroutine
    # that returns once he has been idle for a few seconds. See wait_for_quiet.
    quiet_hook = None

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        tool_choice: str | None = None,
        sampling: dict | None = None,
        slot: int = 2,
        server=None,
    ) -> AsyncIterator[Chunk]:
        """`slot` 0 is the CONVERSATION with tools and its cached prefix, 1 the
        conversation without tools; anything else is a side call (fact
        classifier, night school, newsroom, the market story, part generation)
        and goes to the LAST slot the server has, so it cannot evict either
        conversation cache. On a two-slot server that is slot 1 again; on a
        one-slot server the field is omitted."""
        # `server` picks the process: the big conversation server (default)
        # or the draft (llm.draft_model) for plain knowledge questions.
        srv = server or llama
        import time as _time
        LocalLLM.working_since = _time.time()
        model_name = srv.model_name or config.get("llm", "active_model")
        mcfg = config.get("llm", "models", default={}).get(model_name, {})
        body: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "cache_prompt": True,   # reuse the KV cache for the shared prefix
        }
        # Send sampling explicitly. Omitting it silently accepted llama-server's
        # creative-writing defaults, which made simple factual answers vary run to run.
        body.update(config.get("llm", "sampling", default={}) or {})
        if mcfg.get("sampling"):
            body.update(mcfg["sampling"])       # per-model override
        if sampling:
            body.update(sampling)               # per-call override (benchmarks)
        tk = mcfg.get("template_kwargs")
        if tk:
            body["chat_template_kwargs"] = tk
        if tools:
            body["tools"] = tools
            if tool_choice:
                body["tool_choice"] = tool_choice
        n_slots = max(1, int(getattr(srv, "n_slots", 1) or 1))
        if n_slots > 1:
            body["id_slot"] = 0 if slot == 0 else min(int(slot), n_slots - 1)

        # Accumulate streamed tool-call fragments by index.
        pending_tools: dict[int, dict] = {}
        # A REASONING MODEL SPENDS max_tokens ON THINKING FIRST. gpt-oss-20b
        # streams its analysis in `reasoning_content` and its answer in
        # `content`, and both come out of the SAME budget — so a max_tokens set
        # for the size of the answer can be consumed entirely by the thinking and
        # yield an empty string with no error anywhere.
        #
        # Measured, not theorised: "a 20 mm cube with a 2 mm chamfer" at
        # max_tokens=700 returned finish_reason=length, 2,443 characters of
        # reasoning and ZERO characters of content, and `generate_part` reported
        # "the model returned no source" — which named the symptom and hid the
        # cause. At 1,600 the same prompt finished in 561 tokens.
        saw_content = False
        reasoned = 0
        headers = {"Authorization": f"Bearer {srv.api_key}"} if srv.api_key else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10),
                                     headers=headers) as c:
            async with c.stream(
                "POST", f"{srv.base_url}/v1/chat/completions", json=body
            ) as resp:
                if resp.status_code >= 400:
                    # READ IT BEFORE RAISING. The body is the only place the
                    # server says what it objected to, and on a streaming
                    # response raise_for_status() discards it unread.
                    try:
                        detail = (await resp.aread()).decode(errors="replace")[:600]
                    except Exception:
                        detail = "<body unreadable>"
                    log.error("llama-server %s refused the request: %s",
                              resp.status_code, detail.replace(chr(10), " "))
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    finish = choice.get("finish_reason")
                    if delta.get("reasoning_content"):
                        reasoned += len(delta["reasoning_content"])
                    if delta.get("content"):
                        saw_content = True
                        yield Chunk(text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = pending_tools.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    if obj.get("timings") or obj.get("usage"):
                        tm, us = obj.get("timings") or {}, obj.get("usage") or {}
                        LocalLLM.last_call = {
                            "prompt_tokens": int(us.get("prompt_tokens") or tm.get("prompt_n") or 0),
                            "cached_tokens": int(tm.get("cache_n") or 0),
                            "gen_tokens": int(us.get("completion_tokens") or tm.get("predicted_n") or 0),
                            "prompt_ms": int(tm.get("prompt_ms") or 0),
                            "gen_ms": int(tm.get("predicted_ms") or 0),
                            "gen_tps": round(float(tm.get("predicted_per_second") or 0), 1),
                            "reasoning_chars": reasoned, "slot": body.get("id_slot"),
                        }
                        if tm:
                            log.info("llm: %d prompt tok (%d cached) in %d ms, %d gen tok at %.1f t/s, %d chars of reasoning",
                                     LocalLLM.last_call["prompt_tokens"], LocalLLM.last_call["cached_tokens"],
                                     LocalLLM.last_call["prompt_ms"], LocalLLM.last_call["gen_tokens"],
                                     LocalLLM.last_call["gen_tps"], reasoned)
                    if finish:
                        if finish == "length" and not saw_content and reasoned:
                            log.warning(
                                "the model thought past its budget: max_tokens=%d "
                                "spent entirely on %d chars of reasoning, no answer. "
                                "Raise max_tokens for this call.", max_tokens, reasoned)
                        calls = [
                            {"id": t["id"] or f"call_{i}", "name": t["name"],
                             "arguments": t["arguments"]}
                            for i, t in sorted(pending_tools.items())
                            if t["name"]
                        ]
                        LocalLLM.working_since = 0.0
                        yield Chunk(done=True, finish_reason=finish,
                                    tool_calls=calls or None)
                        return
        LocalLLM.working_since = 0.0
        yield Chunk(done=True, finish_reason="stop")


local_llm = LocalLLM()


async def wait_for_quiet(max_wait: float = 90.0) -> None:
    """For BACKGROUND side calls only (the fact classifier, the newsroom, the
    market story): wait until he has been idle a few seconds, bounded. A
    fact-classifier call three seconds after a search halved the next answer's
    generation speed (13.5 vs 24 t/s, release 74, 2026-09-18). Calls that
    belong to a turn must never use this."""
    hook = getattr(local_llm, "quiet_hook", None)
    if hook is None:
        return
    try:
        await asyncio.wait_for(hook(), timeout=max_wait)
    except asyncio.TimeoutError:
        log.info("side call waited %.0f s for a quiet moment; going ahead", max_wait)
    except Exception:
        log.debug("quiet hook failed", exc_info=True)
