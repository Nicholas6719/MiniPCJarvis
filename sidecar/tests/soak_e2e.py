"""Does he survive being used?

Every other suite asks whether an answer was right. None of them asks whether
the process was still the same process at the end — and on 2026-08-29 it very
often was not: a use-after-free in the audio watcher was killing the sidecar
with an access violation nine times an afternoon. Every feature test stayed
green throughout, because a dead process restarts in forty seconds and answers
the next question perfectly.

So this one exercises the paths that touch native code — COM for audio, COM for
UI Automation, PortAudio for the microphone, the browser, the recogniser — over
and over, and then asks one question: is this the same sidecar we started with.

Run: python tests/soak_e2e.py PORT TOKEN [seconds]
"""
import asyncio
import sys
import time

import httpx
import psutil

PORT = sys.argv[1] if len(sys.argv) > 1 else "8790"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "devtoken123"
SECONDS = float(sys.argv[3]) if len(sys.argv) > 3 else 100.0
BASE, H = f"http://127.0.0.1:{PORT}", {"X-Jarvis-Token": TOKEN}

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def sidecar() -> tuple[int, float] | None:
    """(pid, started) of the running sidecar — a crash shows up as a new pid."""
    for p in psutil.process_iter(["name", "pid", "create_time"]):
        if (p.info["name"] or "").lower() == "jarvis-sidecar.exe":
            return p.info["pid"], p.info["create_time"]
    return None


async def main() -> int:
    before = sidecar()
    check("the sidecar is running to begin with", before is not None)
    if before is None:
        print("\nSOAK: FAIL")
        return 1

    rounds = 0
    errors: list[str] = []
    t0 = time.time()
    async with httpx.AsyncClient(timeout=60) as c:
        async def once(method, url, **kw):
            """One request, with a single retry on a CONNECTION-level failure.

            Keep-alive has an unavoidable race: the server closes an idle pooled
            socket at the same moment the client sends on it, and httpx raises
            ReadError. That is an HTTP client artefact, not the sidecar failing —
            the process check below is what actually catches a sidecar in
            trouble. A retry on transport errors only; an HTTP error still counts.
            """
            try:
                return await getattr(c, method)(url, **kw)
            except httpx.TransportError:
                await asyncio.sleep(0.5)
                return await getattr(c, method)(url, **kw)

        async def tool(name, **args):
            r = await once("post", f"{BASE}/debug/tool", headers=H,
                           json={"tool": name, "args": args})
            return r.json()

        # PACE MATTERS. An earlier version of this ran flat out and reported
        # failures constantly — but at ~60x anything a person does, and half of
        # those "crashes" were the supervisor restarting a sidecar that was
        # merely busy, not broken. A test that can only fail by being unfair
        # teaches you to ignore it. This is the shape of real use: a question
        # every few seconds, diagnostics now and then, a look at a window.
        last_diag = last_uia = 0.0
        while time.time() - t0 < SECONDS:
            rounds += 1
            try:
                await once("post", f"{BASE}/text", headers=H,
                           json={"text": "what time is it"})
                now = time.time()
                if now - last_diag > 30:       # COM: audio sessions, if enabled
                    await once("get", f"{BASE}/diagnostics", headers=H)
                    last_diag = now
                if now - last_uia > 15:        # COM: UI Automation, the other apartment
                    await tool("list_controls", window="", limit=10)
                    last_uia = now
                await once("get", f"{BASE}/health", headers=H)
            except Exception as e:                       # noqa: BLE001
                errors.append(f"round {rounds}: {type(e).__name__}: {e}")
            await asyncio.sleep(5.0)

    after = sidecar()
    check(f"it answered every time ({rounds} rounds in {int(time.time() - t0)}s)",
          not errors, "; ".join(errors[:2]))
    check("it is still running", after is not None)
    if after and before:
        # A crash is invisible in the log — the process simply dies and comes
        # back. The pid is the only honest witness.
        check("and it is the SAME process — it never crashed and restarted",
              after[0] == before[0],
              f"pid {before[0]} became {after[0]}")

    print(f"\nSOAK: {'PASS' if not fails else f'FAIL ({len(fails)})'}")
    return 0 if not fails else 1


sys.exit(asyncio.run(main()))
