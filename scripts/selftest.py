"""Run the e2e suites against the running app and write a JSON report.
Usage: selftest.py PORT TOKEN OUT.json"""
import json, os, subprocess, sys, time

def _find_app():
    """Port/token from the running jarvis-sidecar.exe command line."""
    import re
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info["name"] or "").lower() == "jarvis-sidecar.exe":
                cl = " ".join(p.info["cmdline"] or [])
                m1, m2 = re.search(r"--port (\d+)", cl), re.search(r"--token ([0-9a-f]+)", cl)
                if m1 and m2:
                    return m1.group(1), m2.group(1)
    except Exception:
        pass
    return None, None


if len(sys.argv) >= 4:
    port, tok, out = sys.argv[1], sys.argv[2], sys.argv[3]
else:
    out = sys.argv[1]
    port, tok = _find_app()
    if not port:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "ok": None, "skipped": True, "results": [],
                       "error": "JARVIS was not running at self-test time"}, f)
        print("JARVIS is not running - self-test skipped"); sys.exit(0)
print(f"selftest against :{port} at {time.strftime('%H:%M:%S')}")
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
py = os.path.join(root, "sidecar", ".venv", "Scripts", "python.exe")
SUITES = ["brain_e2e.py", "files_e2e.py", "teach_e2e.py", "general_e2e.py", "voice_ux_e2e.py"]
PASS_MARK = {"brain_e2e.py": "/8 passed", "files_e2e.py": "FILES: PASS", "teach_e2e.py": "TEACH/ROUTINE/CORRECTION: PASS",
             "general_e2e.py": "none (good)", "voice_ux_e2e.py": "PREROLL: PASS | CONVERSATION WINDOW: PASS | BARE JARVIS: PASS"}
results = []
try:
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{port}/debug/silence", data=b'{"seconds": 1500}',
                                 headers={"X-Jarvis-Token": tok, "Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()
except Exception as e:
    print("could not silence speaker:", e)
for s in SUITES:
    t0 = time.time()
    try:
        r = subprocess.run([py, os.path.join(root, "sidecar", "tests", s), port, tok], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900, cwd=os.path.join(root, "sidecar"), env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        text = (r.stdout or "") + (r.stderr or "")
        ok = PASS_MARK[s] in text and (s != "brain_e2e.py" or "8/8 passed" in text)
        tail = [l for l in text.strip().splitlines() if l.strip()][-3:]
    except Exception as e:
        ok, tail = False, [str(e)]
    results.append({"suite": s, "ok": ok, "seconds": round(time.time() - t0), "tail": tail})
    print(f"{'PASS' if ok else 'FAIL'} {s} ({round(time.time() - t0)}s)")
# tidy up: the suites create a test reminder ("stretch") - don't let it fire at dawn
try:
    import urllib.request
    H = {"X-Jarvis-Token": tok}
    tasks = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/tasks", headers=H), timeout=5).read())
    for t in tasks.get("tasks", []):
        if "stretch" in str(t.get("text", "")).lower():
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/tasks/{t['id']}", headers=H, method="DELETE"), timeout=5).read()
except Exception as e:
    print("cleanup failed:", e)
report = {"ts": time.time(), "ok": all(r["ok"] for r in results), "results": results}
with open(out, "w", encoding="utf-8") as f:
    json.dump(report, f, indent=1)
print("SELFTEST", "OK" if report["ok"] else "FAILED")
