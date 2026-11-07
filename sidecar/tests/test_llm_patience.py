"""Slow is not dead: the model server gets patience, and never a load that cannot finish.

On 2026-10-29 and 10-30, with memory at 94-97% (his other apps held 8-10 GB),
the 15 GB model server answered /health a little slowly. One 2-second miss was
enough for the watchdog to kill it and start a fresh 12 GB load into memory
that was not there; the machine thrashed and JARVIS was gone for 6, 7, 24 and
6 minutes. Now a running server gets a patient second look and five minutes,
an exited one is restarted at once, and no load starts without the room to
finish.

Offline: no model, no server. Run: python tests/test_llm_patience.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "patience.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from orchestrator import Orchestrator
    act = Orchestrator.llm_recovery_action
    P = 300.0

    print("\n-- what the watchdog does after a quick /health miss --")
    check("answered the patient look: nothing to do", act(True, True, None, 1000.0, P) == ("ok", None))
    check("...and a slow spell is forgotten", act(True, True, 900.0, 1000.0, P) == ("ok", None))
    a, since = act(True, False, None, 1000.0, P)
    check("running but silent: WAIT, and the clock starts", a == "wait" and since == 1000.0, (a, since))
    check("still silent after four minutes: still waiting", act(True, False, 1000.0, 1240.0, P) == ("wait", 1000.0))
    check("silent for five minutes while running: replaced", act(True, False, 1000.0, 1300.0, P) == ("replace", None))
    check("the process has exited: restarted at once", act(False, False, None, 1000.0, P) == ("restart", None))
    check("...even mid slow-spell", act(False, False, 900.0, 1000.0, P) == ("restart", None))

    print("\n-- the watchdog uses it, with a patient timeout --")
    src = (Path(__file__).resolve().parents[1] / "orchestrator.py").read_text(encoding="utf-8")
    wd = src[src.index("async def _llm_watchdog"):src.index("async def _warm_prompts")]
    check("the second look waits up to 15 s, not 2", "healthy(timeout=15.0)" in wd)
    check("the decision is the tested one", "self.llm_recovery_action(" in wd)
    check("a wait is a `continue`, never a reload",
          wd.index('if action == "wait"') < wd.index("await llama.ensure()"))

    print("\n-- no load starts without room to finish --")
    from llm.llama_server import LlamaServer
    srv = LlamaServer.__new__(LlamaServer)
    weights = Path(tempfile.mkdtemp()) / "fake.gguf"
    weights.write_bytes(b"\0" * 1024)          # ~0 GB + 2.5 GB headroom needed
    seq = [1.0, 1.0, 9.0]
    srv._available_gb = lambda: seq.pop(0) if seq else 9.0
    async def fast_sleep(_s):
        return None
    real_sleep = asyncio.sleep
    asyncio.sleep = fast_sleep
    try:
        ok = asyncio.run(srv._wait_for_memory(weights, deadline_s=60))
        check("it waits while memory is short, then loads when it returns", ok is True and seq == [], seq)
        srv._available_gb = lambda: 1.0
        ok = asyncio.run(srv._wait_for_memory(weights, deadline_s=0))
        check("it gives up at the deadline rather than thrash", ok is False)
        srv._available_gb = lambda: None
        ok = asyncio.run(srv._wait_for_memory(weights, deadline_s=0))
        check("memory it cannot read is never the reason for no model", ok is True)
        ok = asyncio.run(srv._wait_for_memory(Path("Z:/no/such/model.gguf"), deadline_s=0))
        check("...nor is a model file it cannot stat", ok is True)
    finally:
        asyncio.sleep = real_sleep
    start_src = (Path(__file__).resolve().parents[1] / "llm" / "llama_server.py").read_text(encoding="utf-8")
    body = start_src[start_src.index("async def _start"):]
    check("the start path waits for memory before it launches",
          body.index("_wait_for_memory(") < body.index("subprocess.Popen("))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
