"""LLMProvider abstraction. Phase 1 ships the local llama-server implementation;
cloud providers slot in behind the same interface later."""
from __future__ import annotations

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

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int = 1024,
        tool_choice: str | None = None,
        sampling: dict | None = None,
    ) -> AsyncIterator[Chunk]:
        model_name = llama.model_name or config.get("llm", "active_model")
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
        headers = {"Authorization": f"Bearer {llama.api_key}"} if llama.api_key else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10),
                                     headers=headers) as c:
            async with c.stream(
                "POST", f"{llama.base_url}/v1/chat/completions", json=body
            ) as resp:
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
                        yield Chunk(done=True, finish_reason=finish,
                                    tool_calls=calls or None)
                        return
        yield Chunk(done=True, finish_reason="stop")


local_llm = LocalLLM()
