"""Model bake-off against a running llama-server, using JARVIS's real system prompt and
tool schemas. Measures speed (prompt eval / generation / first token, warm cache) and
behaviour (tool choice + args on voice-style requests, direct answers on knowledge Qs).

  python tests/model_bench.py http://127.0.0.1:PORT [API_KEY] [--kwargs '{"enable_thinking": false}']
"""
import asyncio, json, sys, time, re, argparse
import httpx

sys.path.insert(0, ".")
from llm.prompts import system_prompt, turn_context  # noqa: E402
from tools.registry import registry  # noqa: E402
from tools import builtin, memory_tools, windows_tools, web_tools, task_tools, vision_tools, browser_tools, file_tools  # noqa: E402

for m in (builtin, memory_tools, windows_tools, web_tools, task_tools, vision_tools, browser_tools, file_tools):
    m.register_all()
TOOLS = registry.schemas()

# (request, expected tool or None, required arg fragments)
TOOL_CASES = [
    ("search the web for the best budget mechanical keyboard", "web_search", ["keyboard"]),
    ("open spotify", "open_application", ["spotify"]),
    ("remind me in 20 minutes to take the laundry out", "set_reminder", ["laundry"]),
    ("remember that my sister's birthday is march 3rd", "remember_fact", ["march"]),
    ("what's on my screen right now", "analyze_screen", []),
    ("turn the volume down to 20", "set_volume", ["20"]),
    ("show me pictures of the northern lights", "show_images", ["northern"]),
    ("what's the weather in boston right now", "web_search", ["boston"]),
    ("open youtube.com", "open_url", ["youtube"]),
    ("take a screenshot and save it to my desktop", "take_screenshot", ["desktop"]),
    ("find the file called budget in my documents", "find_files", ["budget"]),
    ("what did i tell you about my coffee", "recall", ["coffee"]),
    ("how many legs does a spider have", None, ["eight"]),
    ("what's the capital of australia", None, ["canberra"]),
    ("write me a two line poem about rain", None, []),
    ("what year did world war two end", None, ["1945"]),
    ("explain in one sentence why the sky is blue", None, ["scatter"]),
    ("is a tomato a fruit or a vegetable", None, ["fruit"]),
]


async def chat(c, base, key, messages, tools=None, kwargs=None, max_tokens=512):
    body = {"messages": messages, "max_tokens": max_tokens, "stream": True, "cache_prompt": True}
    if tools:
        body["tools"] = tools
    if kwargs:
        body["chat_template_kwargs"] = kwargs
    t0 = time.time(); first = None; text = ""; calls = {}; usage = None; reasoning = ""
    async with c.stream("POST", f"{base}/v1/chat/completions", json=body,
                        headers={"Authorization": f"Bearer {key}"} if key else {}) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            if not line.startswith("data: ") or line.strip() == "data: [DONE]":
                continue
            obj = json.loads(line[6:])
            if obj.get("usage"):
                usage = obj["usage"]
            ch = (obj.get("choices") or [{}])[0]
            d = ch.get("delta") or {}
            if d.get("reasoning_content") or d.get("reasoning"):
                reasoning += d.get("reasoning_content") or d.get("reasoning") or ""
            if d.get("content"):
                first = first or time.time() - t0
                text += d["content"]
            for tc in d.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"name": "", "arguments": ""})
                if first is None and (tc.get("function") or {}).get("name"):
                    first = time.time() - t0
                fn = tc.get("function") or {}
                slot["name"] = slot["name"] or fn.get("name", "")
                slot["arguments"] += fn.get("arguments", "") or ""
    return {"text": text, "calls": list(calls.values()), "first": first, "total": time.time() - t0,
            "usage": usage, "reasoning_chars": len(reasoning)}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("key", nargs="?", default="")
    ap.add_argument("--kwargs", default=None); ap.add_argument("--label", default="")
    a = ap.parse_args()
    kwargs = (json.load(open(a.kwargs)) if a.kwargs and a.kwargs.endswith(".json") else json.loads(a.kwargs)) if a.kwargs else None
    sysmsg = {"role": "system", "content": system_prompt()}
    async with httpx.AsyncClient(timeout=300) as c:
        # warm the prefix
        await chat(c, a.base, a.key, [sysmsg, {"role": "user", "content": turn_context("") + "\nhi"}], TOOLS, kwargs, 8)
        # ---- speed: generation on a known-long answer ----
        r = await chat(c, a.base, a.key, [sysmsg, {"role": "user", "content": turn_context("") +
                       "\nList twelve common kitchen tools, one per line, no commentary."}], TOOLS, kwargs, 300)
        gen_tok = (r["usage"] or {}).get("completion_tokens") or max(1, len(r["text"]) // 4)
        gen_tps = gen_tok / max(0.001, r["total"] - (r["first"] or 0))
        print(f"[{a.label}] generation ~{gen_tps:.1f} tok/s ({gen_tok} tok in {r['total']:.1f}s, first {r['first'] and round(r['first'], 2)}s, reasoning {r['reasoning_chars']} chars)")
        # ---- behaviour ----
        ok = 0; firsts = []; totals = []; rows = []
        for req, want, frags in TOOL_CASES:
            r = await chat(c, a.base, a.key, [sysmsg, {"role": "user", "content": turn_context("") + "\n" + req}], TOOLS, kwargs, 400)
            got = r["calls"][0]["name"] if r["calls"] else None
            args = (r["calls"][0]["arguments"] if r["calls"] else r["text"]).lower()
            good = (got == want) and all(f in args for f in frags)
            ok += good; firsts.append(r["first"] or r["total"]); totals.append(r["total"])
            rows.append(f"  {'PASS' if good else 'FAIL'} {req[:44]:44} -> {str(got):16} {round(r['first'] or r['total'], 2)}s  {(r['calls'][0]['arguments'] if r['calls'] else r['text'].strip())[:60]!r}")
        print("\n".join(rows))
        firsts.sort(); totals.sort()
        print(f"[{a.label}] behaviour {ok}/{len(TOOL_CASES)} | median first {firsts[len(firsts)//2]:.2f}s | median total {totals[len(totals)//2]:.2f}s")
        # ---- multi-turn tool result composition ----
        tool_res = {"ok": True, "result": {"results": [{"title": "Burj Khalifa - Wikipedia", "snippet": "The Burj Khalifa in Dubai is the tallest building in the world at 828 m."}]},
                    "note": "Answer the user from these results now, in one or two spoken sentences."}
        msgs = [sysmsg, {"role": "user", "content": turn_context("") + "\nsearch the web for the tallest building in the world"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": "tallest building in the world"})}}]},
                {"role": "tool", "tool_call_id": "c1", "content": json.dumps(tool_res)}]
        r = await chat(c, a.base, a.key, msgs, None, kwargs, 200)
        print(f"[{a.label}] compose-from-results: {round(r['first'] or 0, 2)}s first | {r['text'].strip()[:100]!r}")

asyncio.run(main())
