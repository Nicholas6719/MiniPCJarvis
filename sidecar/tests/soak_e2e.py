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


def rss_mb(pid: int) -> float:
    """Resident memory, so a leak shows up as a number rather than as a crash
    three days from now."""
    try:
        return psutil.Process(pid).memory_info().rss / 1024 / 1024
    except Exception:
        return 0.0


# HOW MUCH THE RESIDENT PATHS MAY GROW, per minute of soak. Generous on purpose:
# Python's allocator does not return everything, the recogniser caches, and a
# threshold tight enough to catch nothing but a leak would fail on a slow GC. A
# real leak in a camera loop reading ten frames a second is orders of magnitude
# above this — the last one showed up as hundreds of megabytes an hour.
MAX_GROWTH_MB_PER_MIN = 12.0


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

        async def tool(_tool, **args):
            # `_tool`, not `name`: half the tools in this app take a `name`
            # argument, and the collision is a TypeError at the call site rather
            # than anywhere near this line.
            r = await once("post", f"{BASE}/debug/tool", headers=H,
                           json={"tool": _tool, "args": args})
            return r.json()

        # PACE MATTERS. An earlier version of this ran flat out and reported
        # failures constantly — but at ~60x anything a person does, and half of
        # those "crashes" were the supervisor restarting a sidecar that was
        # merely busy, not broken. A test that can only fail by being unfair
        # teaches you to ignore it. This is the shape of real use: a question
        # every few seconds, diagnostics now and then, a look at a window.
        # ---- the ALWAYS-RESIDENT hologram paths -----------------------------
        # The plan named this outright: run the soak after phase E, because the
        # landmark stream is a new always-resident path. Running the soak without
        # it satisfies the sentence and misses the point — it would exercise
        # audio, COM and the browser while the one genuinely new long-lived loop
        # sat idle. So a model goes up and hand tracking arms for the duration,
        # and the camera reads ten frames a second the whole way through.
        holo_up = hands_up = False
        try:
            made = await tool("generate_part", description="a 30 mm cube",
                              name="soak-cube")
            if not (made.get("result") or {}).get("error"):
                shown = await tool("show_hologram", name="soak-cube")
                holo_up = bool((shown.get("result") or {}).get("on_stage"))
            if holo_up:
                armed = await tool("hand_control", on=True)
                hands_up = bool((armed.get("result") or {}).get("armed"))
        except Exception as e:                           # noqa: BLE001
            errors.append(f"setup: {type(e).__name__}: {e}")
        check("a model is on the stage for the duration", holo_up)
        check("...and his hands are being watched the whole time", hands_up,
              "the landmark stream is the new resident path; a soak that does "
              "not run it is not the soak the plan asked for")

        # BASELINE AFTER A WARM-UP, NOT AT t=0. A sidecar that started a minute
        # ago is still settling — the first run of this measured 1,749 MB
        # falling to 819 MB and cheerfully reported "no leak" on the strength of
        # a 930 MB DROP. A number that can only be flattered is not a
        # measurement, so the baseline is taken a fifth of the way in, once the
        # caches and the recogniser have stopped moving.
        warmup = min(30.0, SECONDS * 0.2)
        start_rss = 0.0

        last_diag = last_uia = last_holo = 0.0
        while time.time() - t0 < SECONDS:
            rounds += 1
            if not start_rss and time.time() - t0 >= warmup:
                start_rss = rss_mb(before[0])
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
                if holo_up and now - last_holo > 10:
                    # The two endpoints the HUD hits, at the rate a person
                    # actually looks at a model: geometry is a megabyte of
                    # base64 and printcheck re-parses the mesh, so if either
                    # leaks or blocks, this is where it shows.
                    await once("get", f"{BASE}/holo/geometry?name=soak-cube",
                               headers=H)
                    await once("get", f"{BASE}/holo/printcheck?name=soak-cube",
                               headers=H)
                    last_holo = now
                await once("get", f"{BASE}/health", headers=H)
            except Exception as e:                       # noqa: BLE001
                errors.append(f"round {rounds}: {type(e).__name__}: {e}")
            await asyncio.sleep(5.0)

        # STILL ARMED? A tracker that quietly stood down is as bad as a crash and
        # far harder to notice: the badge goes out, his hands stop working, and
        # nothing is logged. It idles off after IDLE_OFF_S with nobody in frame,
        # so this only asserts it when the soak was shorter than that.
        end_rss = rss_mb(before[0])
        if hands_up:
            st = (await tool("hand_status")).get("result") or {}
            print(f"  hand tracker after the soak: {st}")
            # ARMED IS A FLAG; FRAMES IS THE WITNESS. A tracking loop that died
            # leaves `armed` true forever — the badge stays lit, his hands stop
            # working, and nothing is logged anywhere. Only the frame counter
            # can tell a live loop from a dead one.
            #
            # NOT "still armed at the end", which is what this asserted first and
            # is the opposite of the design: the tracker stands down after
            # IDLE_OFF_S with nobody in frame, and nobody IS in frame during an
            # unattended soak. It duly stood down after 369 frames and the test
            # called that a failure. The fatigue rule working is a pass.
            check("the tracking loop really turned, rather than only being flagged",
                  st.get("frames", 0) > 0,
                  "a dead loop leaves armed=True and frames frozen at zero")
            if st.get("armed"):
                print("    (still armed — someone was in frame)")
            else:
                # Standing down is only correct AFTER it has actually run. An
                # immediate disarm with no frames read is a broken tracker
                # wearing the fatigue rule as a disguise.
                check("...and if it stood down, it did so having run first",
                      st.get("frames", 0) > 50,
                      f"disarmed after only {st.get('frames')} frames")
            await tool("hand_control", on=False)
        # Leave nothing running. He may walk back to this machine at any moment
        # and a camera left on by a test is exactly the surprise this project
        # spends so much effort avoiding.
        try:
            await tool("set_camera", on=False)
            await tool("hide_hologram")
        except Exception:                                # noqa: BLE001
            pass

        if start_rss:
            mins = max((SECONDS - warmup) / 60.0, 0.1)
            grew = end_rss - start_rss
            print(f"  memory {start_rss:.0f} MB -> {end_rss:.0f} MB "
                  f"({grew:+.0f} MB over {mins:.1f} min, {grew / mins:+.1f} MB/min)")
            check("the resident paths do not leak",
                  grew / mins < MAX_GROWTH_MB_PER_MIN,
                  f"{grew / mins:.1f} MB/min with a camera loop and a hologram up")
        else:
            # Say so rather than passing silently: a soak too short to take a
            # baseline has not tested for a leak, and should not look as if it has.
            check("the soak was long enough to measure memory at all", False,
                  f"{SECONDS:.0f}s is shorter than the {warmup:.0f}s warm-up")

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
