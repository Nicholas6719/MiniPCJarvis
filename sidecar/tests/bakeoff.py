"""LLM bake-off: speed (llama-bench) + smarts (tool-calling / instruction quality)
for each candidate model on THIS machine. Run:
    .venv/Scripts/python.exe tests/bakeoff.py [--skip-bench]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

LLAMA_DIR = Path(r"C:\AI\llama.cpp")
BENCH = LLAMA_DIR / "llama-bench.exe"
SERVER = LLAMA_DIR / "llama-server.exe"
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"

MODELS = {
    "gpt-oss-20b": {
        "path": r"C:\AI\models\gpt-oss-20b-MXFP4.gguf",
        "template_kwargs": {"reasoning_effort": "low"},
        "bench_args": ["-ngl", "999", "-t", "8", "-fa", "1"],
        "server_args": ["-ngl", "999", "-t", "8", "-fa", "on", "--jinja"],
    },
    "qwen3.6-35b-a3b": {
        "path": r"C:\AI\models\Qwen3.6-35B-A3B-UD-Q3_K_XL.gguf",
        "template_kwargs": {"enable_thinking": False},
        # 16.8 GB > ~17.8 GB Vulkan UMA window with KV/compute buffers:
        # keep MoE expert weights on CPU (same DRAM), attention on iGPU.
        "bench_args": ["-ngl", "999", "-ncmoe", "999", "-t", "8", "-fa", "1"],
        "server_args": ["-ngl", "999", "--cpu-moe", "-t", "8", "-fa", "on", "--jinja"],
    },
}

TOOLS = [
    {"type": "function", "function": {
        "name": "open_application",
        "description": "Launch an application on this PC by name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "get_system_stats",
        "description": "Get current CPU, RAM, disk and battery statistics for this PC.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the web for current information.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file from the user's folders.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "remember_fact",
        "description": "Store a lasting fact or preference about the user.",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string"}}, "required": ["content"]}}},
]

SYSTEM = ("You are JARVIS, a voice assistant on the user's Windows PC. Replies are "
          "spoken aloud: keep them to one or two short sentences, no markdown. Use "
          "tools when the request calls for action or live data; answer directly "
          "when you already know.")

# (prompt, expected_tool_or_None, check)
CASES = [
    ("Open Spotify.", "open_application", lambda a: "spotify" in json.dumps(a).lower()),
    ("How much RAM am I using right now?", "get_system_stats", lambda a: True),
    ("What's the weather in Boston today?", "web_search", lambda a: "weather" in json.dumps(a).lower() or "boston" in json.dumps(a).lower()),
    ("Remember that I prefer short answers.", "remember_fact", lambda a: "short" in json.dumps(a).lower()),
    ("Can you put on some music?", "open_application", lambda a: "spotify" in json.dumps(a).lower() or "music" in json.dumps(a).lower()),
    ("Read the file called notes.txt in my documents.", "read_file", lambda a: "notes" in json.dumps(a).lower()),
    ("What is the capital of Australia?", None, lambda t: "canberra" in t.lower()),
    ("What's 17 times 24?", None, lambda t: "408" in t.replace(",", "")),
    ("If a train leaves at 3:15 PM and the trip takes 2 hours 50 minutes, when does it arrive?", None, lambda t: "6:05" in t or "six oh five" in t.lower() or "18:05" in t),
    ("Say exactly three words.", None, lambda t: len([w for w in t.strip().rstrip('.').split() if w]) == 3),
    ("Who wrote Pride and Prejudice?", None, lambda t: "austen" in t.lower()),
    ("I'm heading out, is it worth checking anything before a road trip?", None, lambda t: len(t) > 10),
]


def wait_healthy(timeout=180) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def run_bench(name: str, path: str, args: list[str]) -> dict:
    out = subprocess.run(
        [str(BENCH), "-m", path, *args, "-o", "json"],
        capture_output=True, text=True, timeout=1200)
    try:
        data = json.loads(out.stdout)
        res = {}
        for row in data:
            key = "pp512" if row.get("n_prompt") else "tg128"
            res[key] = round(row.get("avg_ts", 0), 1)
        return res
    except Exception:
        return {"error": out.stderr[-500:] if out.stderr else out.stdout[-500:]}


def run_quality(name: str, cfg: dict) -> dict:
    proc = subprocess.Popen(
        [str(SERVER), "-m", cfg["path"], *cfg["server_args"],
         "-c", "16384", "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    results = {"tool_pass": 0, "tool_total": 0, "qa_pass": 0, "qa_total": 0,
               "avg_first_ms": 0, "avg_total_ms": 0, "details": []}
    lat_first, lat_total = [], []
    try:
        if not wait_healthy():
            results["error"] = "server never became healthy"
            return results
        for prompt, expect_tool, check in CASES:
            body = {
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": prompt}],
                "tools": TOOLS, "max_tokens": 384, "stream": True,
                "chat_template_kwargs": cfg.get("template_kwargs", {}),
            }
            t0 = time.time()
            first = None
            text = ""
            calls: dict[int, dict] = {}
            try:
                with httpx.stream("POST", f"{BASE}/v1/chat/completions",
                                  json=body, timeout=120) as r:
                    for line in r.iter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            break
                        obj = json.loads(payload)
                        delta = (obj.get("choices") or [{}])[0].get("delta") or {}
                        if delta.get("content") or delta.get("tool_calls"):
                            if first is None:
                                first = time.time() - t0
                        if delta.get("content"):
                            text += delta["content"]
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", 0),
                                                    {"name": "", "arguments": ""})
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["arguments"] += fn["arguments"]
            except Exception as e:
                results["details"].append({"prompt": prompt, "error": str(e)})
                continue
            total = time.time() - t0
            lat_first.append(first or total)
            lat_total.append(total)

            if expect_tool is not None:
                results["tool_total"] += 1
                got = [c["name"] for c in calls.values()]
                ok = expect_tool in got
                if ok:
                    try:
                        args = json.loads(list(calls.values())[0]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    ok = bool(check(args))
                results["tool_pass"] += int(ok)
                results["details"].append(
                    {"prompt": prompt, "want": expect_tool, "got": got,
                     "ok": ok, "s": round(total, 1)})
            else:
                results["qa_total"] += 1
                ok = bool(check(text)) and not calls
                results["qa_pass"] += int(ok)
                results["details"].append(
                    {"prompt": prompt, "text": text[:120], "ok": ok,
                     "s": round(total, 1)})
        results["avg_first_ms"] = int(sum(lat_first) / max(len(lat_first), 1) * 1000)
        results["avg_total_ms"] = int(sum(lat_total) / max(len(lat_total), 1) * 1000)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-bench", action="store_true")
    ap.add_argument("--only")
    args = ap.parse_args()

    report = {}
    for name, cfg in MODELS.items():
        if args.only and args.only != name:
            continue
        if not Path(cfg["path"]).exists():
            print(f"[skip] {name}: model file missing")
            continue
        print(f"\n=== {name} ===")
        entry = {}
        if not args.skip_bench:
            print("  running llama-bench…")
            entry["bench"] = run_bench(name, cfg["path"], cfg["bench_args"])
            print("  bench:", entry["bench"])
        print("  running quality suite…")
        q = run_quality(name, cfg)
        entry["quality"] = q
        print(f"  tools: {q['tool_pass']}/{q['tool_total']}  "
              f"qa: {q['qa_pass']}/{q['qa_total']}  "
              f"first-token avg {q['avg_first_ms']} ms  total avg {q['avg_total_ms']} ms")
        report[name] = entry
        time.sleep(3)

    out = Path(__file__).parent / "bakeoff-results.json"
    out.write_text(json.dumps(report, indent=2), "utf-8")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    main()
