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

    print("\n-- the graphics driver vanished under it (2026-11-19 to 11-21) --")
    from llm import llama_server as LS
    real = "Available devices:\n  Vulkan0: AMD Radeon 780M Graphics (17834 MiB, 16942 MiB free)\n"
    mapped = ("[Vulkan Loader] ERROR: loader_get_json: Failed to open JSON file C:\\x\\amd-vulkan64.json\n"
              "Available devices:\n  Vulkan0: Microsoft Direct3D12 (AMD Radeon 780M Graphics) (14762 MiB, 16972 MiB free)\n")
    check("the device list is read from what the server prints",
          LS.parse_devices(real) == ["AMD Radeon 780M Graphics (17834 MiB, 16942 MiB free)"]
          and LS.parse_devices(mapped)[0].startswith("Microsoft Direct3D12") and LS.parse_devices("") == [],
          (LS.parse_devices(real), LS.parse_devices(mapped)))
    asked = []

    def broken(extra):
        asked.append(dict(extra))
        return LS.parse_devices(real if extra.get("VK_DRIVER_FILES") == "new.json" else mapped)

    check("the vendor driver is there: nothing is touched",
          LS.gpu_env(lambda e: LS.parse_devices(real), ["new.json"]) == ({}, "native"))
    env, state = LS.gpu_env(broken, ["stale.json", "new.json"])
    check("only the mapping layer answers: the vendor's driver from the store is handed to the server",
          env == {"VK_DRIVER_FILES": "new.json"} and state == "repaired", (env, state, asked))
    check("...a driver that does not answer either is passed over", {"VK_DRIVER_FILES": "stale.json"} in asked)
    check("nothing better on disk: said, not hidden",
          LS.gpu_env(lambda e: LS.parse_devices(mapped), []) == ({}, "mapped")
          and LS.gpu_env(lambda e: [], ["x.json"]) == ({}, "none"))
    check("a driver he chose himself is left alone",
          LS.gpu_env(broken, ["new.json"], preset=True) == ({}, "mapped"))
    check("a server that will not say changes nothing - never the reason for no model",
          LS.gpu_env(lambda e: None, ["new.json"]) == ({}, "unknown"))
    check("the start path looks before it launches, and the child gets the driver",
          body.index("gpu_env,") < body.index("subprocess.Popen(") and "**extra," in body)
    check("the real binary's answer is read in a thread, with no window and a time limit",
          "asyncio.to_thread(" in body and "timeout=30" in start_src and "CREATE_NO_WINDOW" in start_src)

    print("\n-- a restart that did not cure it is not repeated all night --")
    gap = Orchestrator.llm_restart_gap
    check("the first two tries are ten minutes apart", gap(0) == 600.0 and gap(1) == 600.0)
    check("after two that did not help: one every six hours, not every twenty minutes",
          gap(2) == 6 * 3600.0 and gap(40) == 6 * 3600.0)
    told, o = [], Orchestrator.__new__(Orchestrator)

    async def fake_say():
        told.append(1)

    async def night():
        import orchestrator as OM
        loads = []

        async def stop():
            loads.append("stop")

        async def ensure(*a, **k):
            return False
        real_stop, real_ensure, real_emit, real_time = OM.llama.stop, OM.llama.ensure, OM.bus.emit, OM.time.time

        async def emit(*a, **k):
            return None
        clock = [1_000_000.0]
        OM.llama.stop, OM.llama.ensure, OM.bus.emit = stop, ensure, emit
        OM.time.time = lambda: clock[0]
        o._say_model_broken = fake_say
        try:
            for _ in range(75):                 # 75 failed probe pairs, twenty minutes apart: the real night
                await o._restart_llm("two liveness probes in a row came back with nothing speakable")
                clock[0] += 1200
        finally:
            OM.llama.stop, OM.llama.ensure, OM.bus.emit, OM.time.time = real_stop, real_ensure, real_emit, real_time
        return len(loads)

    n = asyncio.run(night())
    check("the night of 75 reloads would have been 6: two tries, then one every six hours", n == 6, n)
    check("...and he is TOLD the model is gone", len(told) >= 1, told)
    psrc = src[src.index("async def _probe_model"):src.index("async def _llm_watchdog")]
    check("one working answer clears the count", "self._uncured_restarts = 0" in psrc)
    said = src[src.index("async def _say_model_broken"):src.index("async def _restart_llm")]
    check("he is told once, through the ledger, quietly",
          "if self._told_model_broken:" in said and 'key=f"model:{day}"' in said)
    import notify_policy as NP
    check("...a model note never makes a sound or interrupts",
          NP.kind_of("model", "model:2026-11-21") == "model" and "model" in NP.QUIET_KINDS
          and "model" in NP.NO_INTERRUPT)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
