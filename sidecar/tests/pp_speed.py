"""Cold prompt-processing cost with JARVIS's real prefix: python tests/pp_speed.py BASE [KEY] [kwargs.json]"""
import json, sys, time, httpx
sys.path.insert(0, ".")
from llm.prompts import system_prompt, turn_context
from tools.registry import registry
from tools import builtin, memory_tools, windows_tools, web_tools, task_tools, vision_tools, browser_tools, file_tools
for m in (builtin, memory_tools, windows_tools, web_tools, task_tools, vision_tools, browser_tools, file_tools):
    m.register_all()
base = sys.argv[1]; key = sys.argv[2] if len(sys.argv) > 2 else ""
kw = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
H = {"Authorization": f"Bearer {key}"} if key else {}
for label, cache in (("cold (no cache)", False), ("warm (cache_prompt)", True), ("warm again", True)):
    body = {"messages": [{"role": "system", "content": system_prompt()},
                         {"role": "user", "content": turn_context("") + "\nsay ok"}],
            "tools": registry.schemas(), "max_tokens": 4, "stream": False, "cache_prompt": cache}
    if kw: body["chat_template_kwargs"] = kw
    t = time.time()
    r = httpx.post(base + "/v1/chat/completions", json=body, headers=H, timeout=900).json()
    tim = r.get("timings", {})
    print(f"{label:22} {time.time()-t:6.1f}s | prompt {tim.get('prompt_n')} tok @ {tim.get('prompt_per_second', 0):.0f} t/s"
          + (f" (cached {tim.get('cache_n')})" if tim.get('cache_n') is not None else ""))
