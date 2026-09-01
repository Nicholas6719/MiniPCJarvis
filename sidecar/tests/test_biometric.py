"""Phase 4: the face is a second opinion, and it can only ever say no.

This is the most sensitive thing in the handoff, so the test that matters most is
not "does it recognise him" — it is **a matching face must not be able to run a
HIGH-risk tool on its own.** If someone later moves the face check above the
spoken gate, or lets it set `approved`, an additive signal silently becomes a
replaceable one and a photograph held to the lens becomes a password. That is the
case this file exists to fail on.

Also gated:
  * MEDIUM risk is untouched — the scope is HIGH only, by instruction;
  * when the signal is UNAVAILABLE (nobody enrolled, camera busy, model missing)
    the spoken gate stands alone. Failing closed there would lock him out of his
    own machine the first time a webcam driver misbehaved — a denial of service
    caused by an optional feature. This is the documented judgement call, and it
    is tested so it stays a decision rather than becoming an accident;
  * a face that is definitely NOT his refuses the action;
  * nothing in the path can raise into a turn.

Offline: no camera, no model. Run: python tests/test_biometric.py
"""
import asyncio
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "p4.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    from tools import biometric as B
    from tools.registry import Risk, Tool, ToolRegistry

    ran = []

    async def handler(**kw):
        ran.append(kw)
        return {"did": "the dangerous thing"}

    def make(risk):
        r = ToolRegistry()
        r.confirm_timeout = 2
        r.register(Tool(name="wipe", description="d",
                        parameters={"type": "object", "properties": {}, "required": []},
                        risk=risk, handler=handler))
        return r

    async def answer(reg, value):
        """Resolve whatever confirmation is pending, the way the UI does."""
        for _ in range(40):
            if reg._pending:
                cid = next(iter(reg._pending))
                reg.resolve_confirmation(cid, value) if hasattr(reg, "resolve_confirmation") \
                    else reg._pending[cid].set_result(value)
                return True
            await asyncio.sleep(0.05)
        return False

    # ---------------------------------------------------------------- THE test
    # A matching face, and NO spoken answer. This must not run.
    ran.clear()
    reg = make(Risk.HIGH)
    B_orig = B.second_signal
    B.second_signal = lambda t: asyncio.sleep(0, result=(True, "face confirmed"))
    try:
        out = await reg.execute("wipe", {})       # nobody ever answers
        check("a matching face CANNOT run a HIGH-risk tool without the spoken yes",
              out.get("ok") is False and not ran,
              f"{out} — an additive signal has become a replaceable one")
        check("...and it is reported as unconfirmed, not as approved",
              out.get("unconfirmed") or out.get("declined"), out)

        # A spoken NO with a matching face must still refuse.
        ran.clear()
        reg = make(Risk.HIGH)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, False)
        out = await task
        check("a matching face cannot override a spoken NO",
              out.get("ok") is False and not ran, out)

        # Spoken yes + matching face = it runs.
        ran.clear()
        reg = make(Risk.HIGH)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, True)
        out = await task
        check("spoken yes AND a matching face runs it", out.get("ok") is True and ran, out)

        # Spoken yes + a face that is NOT his = refused.
        B.second_signal = lambda t: asyncio.sleep(0, result=(False, "that is not your face"))
        ran.clear()
        reg = make(Risk.HIGH)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, True)
        out = await task
        check("a spoken yes with the WRONG face is refused",
              out.get("ok") is False and not ran, out)
        check("...and says why", out.get("face_failed") is True, out)

        # MEDIUM is out of scope and must be untouched.
        ran.clear()
        reg = make(Risk.MEDIUM)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, True)
        out = await task
        check("MEDIUM risk never consults the face — scope is HIGH only",
              out.get("ok") is True and ran, out)

        # An unavailable signal must not lock him out.
        B.second_signal = lambda t: asyncio.sleep(0, result=(True, "unavailable: no face is enrolled"))
        ran.clear()
        reg = make(Risk.HIGH)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, True)
        out = await task
        check("with nobody enrolled, the spoken gate still works alone",
              out.get("ok") is True and ran,
              "failing closed here locks him out of his own machine")

        # A second signal that EXPLODES must not wall him off either.
        def boom(t):
            raise RuntimeError("webcam on fire")
        B.second_signal = boom
        ran.clear()
        reg = make(Risk.HIGH)
        task = asyncio.create_task(reg.execute("wipe", {}))
        await answer(reg, True)
        out = await task
        check("a face check that raises falls back to the spoken gate",
              out.get("ok") is True and ran, out)
    finally:
        B.second_signal = B_orig

    # --------------------------------------------- face_confirm's own behaviour
    # Point the profile at a scratch path. Without this the gate reads HIS real
    # face profile and its result depends on whether he happens to be enrolled —
    # and it opens his actual webcam. An offline gate must touch neither.
    import vision_identity as VI
    scratch = tempfile.mkdtemp()
    real_path = VI._profile_path
    VI._profile_path = lambda: os.path.join(scratch, "face_profile.json")
    real_identity = VI.identity
    VI.identity = VI.Identity()
    try:
        res = await B.face_confirm(1.0)
        check("with nobody enrolled it reports unavailable, never a match",
              res["match"] is False and res["available"] is False, res)
        check("...and says why", "enrolled" in res["reason"], res)

        # Enrolled, but the camera gives nothing: still unavailable, still not a
        # match. "Could not look" must never read as "looked and it was him".
        VI.identity._profile = [[0.0] * 128]
        import camera as CAM
        real_cam = CAM.camera

        class DeadCamera:
            is_on = True

            def frame(self):
                return None

            def start(self):
                return {"ok": True}

            def stop(self):
                return {"ok": True}

        CAM.camera = DeadCamera()
        try:
            res = await B.face_confirm(1.0)
            check("a camera that yields nothing is NOT a match",
                  res["match"] is False, res)
            check("...and is reported as unavailable, so the spoken gate stands",
                  res["available"] is False, res)
        finally:
            CAM.camera = real_cam
    finally:
        VI._profile_path = real_path
        VI.identity = real_identity

    from vision_identity import Identity
    ident = Identity()
    ident._profile = None
    v, s = ident.check_once(object(), None)
    check("no face in frame is 'no_face', not a match", v == "no_face" and s == 0.0)
    v, s = ident.check_once(object(), [[0] * 15])
    check("no profile means it cannot tell — None, which is NOT a match", v is None)

    # ----------------------------------------------- the shape of the code
    src = inspect.getsource(sys.modules["tools.registry"])
    i_appr = src.index("if not approved:")
    i_face = src.index("tool.risk is Risk.HIGH")
    check("the face check sits AFTER the spoken gate in the source",
          i_face > i_appr,
          "above it, a face could grant permission on its own")
    check("the face check can only refuse — it never assigns approved",
          "approved =" not in src[i_face:i_face + 900])
    bsrc = inspect.getsource(B)
    check("there is no config flag that disables the spoken gate",
          "bypass" not in bsrc.lower() and "instead_of" not in bsrc.lower())
    check("face_confirm restores the camera it opened",
          "if opened_here" in bsrc and "camera.stop" in bsrc,
          "a confirmation must not leave his webcam running")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
