"""Generation speed: python tests/gen_speed.py BASE [KEY] [kwargs.json]"""
import json, sys, time, httpx
base = sys.argv[1]; key = sys.argv[2] if len(sys.argv) > 2 else ""
kw = json.load(open(sys.argv[3])) if len(sys.argv) > 3 else None
H = {"Authorization": f"Bearer {key}"} if key else {}
body = {"messages": [{"role": "user", "content": "Write a 250 word story about a lighthouse keeper. Plain prose."}],
        "max_tokens": 400, "stream": False, "cache_prompt": True}
if kw: body["chat_template_kwargs"] = kw
for i in range(2):
    t = time.time()
    r = httpx.post(base + "/v1/chat/completions", json=body, headers=H, timeout=600).json()
    dt = time.time() - t
    u = r.get("usage", {}); tim = r.get("timings", {})
    print(f"run{i}: {u.get('completion_tokens')} tok in {dt:.1f}s -> {u.get('completion_tokens', 0)/dt:.1f} tok/s wall | "
          f"server: pp {tim.get('prompt_per_second', 0):.0f} t/s, gen {tim.get('predicted_per_second', 0):.1f} t/s"
          + (f", draft acc {tim.get('draft_n_accepted')}/{tim.get('draft_n')}" if tim.get('draft_n') else ""))
