"""What counts as him speaking, and what is only the room.

2026-09-08, release 61. Two suite runs were abandoned one line before the
barge-in test because /health.last_voice_s said he was talking:

  - a television woke him at 0.71 and said "You meat puncher! Got a speeding
    corvette over 80", which went to the model as a request;
  - voice_ux_e2e's own injected wake audio was recorded as his voice, so the
    guard built to keep the suites off his machine stopped them on their own
    noise.

Both of those write last_voice_ts, and the release script and suites.ps1 both
refuse to run while it is recent - so anything that gets it wrong costs a
release. The phrases below are the real ones out of .agent/logs.

Run: python tests/test_wake_guard.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "wake.db"))

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain.skills import MARGINAL_WAKE, addressed_to_me, not_for_me

    print("\n-- the television speaks in whole sentences --")
    # Every one of these is out of his transcript, after a wake he did not say.
    for tv in ("You meat puncher! Got a speeding corvette over 80",
               "tie bow tie polo tie I don't even wear tie I don't go no",
               "Stranger.",
               "The shit.",
               "as like this is the challenge? Wait, I need to go on easier."):
        check(f"{tv[:44]!r} is not for him", not addressed_to_me(tv))
        # ...and the 2026-09-05 fragment rule never caught any of them, which
        # is why this file exists. 883 wake fires in the log, not one dismissal.
        check("   ...and the fragment rule alone would have let it through",
              not not_for_me(tv, 0.71))

    print("\n-- his own speech has a shape --")
    for said in ("what time is it", "turn off the camera", "open chrome",
                 "show me that render of the duck again", "volume up", "louder",
                 "go to sleep", "zoom in", "let me use my hands",
                 "remember that i park in the north garage",
                 "tell me about the roman empire",
                 "how many legs does a spider have", "what's the weather",
                 "jarvis, can you see me", "message me on telegram when it's done"):
        check(f"{said[:44]!r} is his", addressed_to_me(said))
    check("a bare wake word is the acknowledgement's job, not this gate's",
          addressed_to_me(""))

    print("\n-- and it is the LAST gate, not the first --")
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    conv = orch[orch.index("async def _converse"):]
    i_gate = conv.index("addressed_to_me(text)")
    i_ask = conv.index("_ask_if_unsure")
    i_reflex = conv.index("await self._reflex_turn")
    check("a skill the brain knows never reaches it", i_reflex < i_gate, (i_reflex, i_gate))
    check("...and it runs before the near-miss question, which would ask the room",
          i_gate < i_ask, (i_gate, i_ask))
    check("only a marginal wake is judged",
          "ws < MARGINAL_WAKE" in conv and MARGINAL_WAKE == 0.85)
    check("...and the score is consumed, so a follow-up is not judged twice",
          "self._wake_score_fresh = getattr(self, \"_wake_score_fresh\", None), None" in conv)
    loop = orch[:orch.index("async def _converse")]
    check("a conversation-window follow-up carries no wake score at all",
          "follow-up speech (conversation window)" in loop
          and "self._wake_score_fresh = None" in
          loop[loop.index("follow-up speech (conversation window)"):][:400])
    check("the dismissal is silent but not invisible",
          'reason="not addressed"' in conv and "wake_suppressed" in conv)

    print("\n-- a test's audio is not his voice --")
    from audio.io import mic
    check("the microphone can say the audio in it was injected",
          hasattr(mic, "injected_until"), type(mic).__name__)
    main_py = (ROOT / "main.py").read_text(encoding="utf-8")
    inj = main_py[main_py.index("async def debug_inject_audio"):][:2000]
    check("...and the inject endpoint says so before it feeds a single block",
          inj.index("mic.injected_until") < inj.index("mic._put"))
    check("...for longer than the audio, so the capture after it is covered too",
          re.search(r"injected_until = _t\.time\(\) \+ len\(audio\) / 16000 \+ \d", inj) is not None)

    import time
    import orchestrator as O
    o = O.__dict__.get("orchestrator")
    check("there is one orchestrator to ask", o is not None)
    if o is not None:
        o.last_voice_ts = 0.0
        mic.injected_until = time.time() + 30
        o._voice_heard()
        check("injected audio does not count as him speaking", o.last_voice_ts == 0.0,
              o.last_voice_ts)
        mic.injected_until = 0.0
        o._voice_heard()
        check("...and his own voice still does", o.last_voice_ts > 0.0)

    print("\n-- ...and it is only recorded once the turn is accepted --")
    turn = orch[orch.index("text = WAKE_PHRASE.sub"):]
    check("the dismissal comes first", turn.index("not_for_me(text") < turn.index("self._voice_heard()"),
          "a dismissed television line still said 'he is using JARVIS'")

    print("\n-- the raise he made in August reaches the machine --")
    import config as C
    check("the default is still the raised one", C.DEFAULTS["wake"]["threshold"] == 0.60,
          C.DEFAULTS["wake"]["threshold"])
    check("the config version moved with the migration", C.Config.CONFIG_VERSION >= 8,
          C.Config.CONFIG_VERSION)
    d = Path(tempfile.mkdtemp())
    saved = {"wake": {"mode": "both", "threshold": 0.45}, "config_version": 7}
    (d / "config.json").write_text(json.dumps(saved), encoding="utf-8")
    cfg = C.Config.__new__(C.Config)
    cfg.data = json.loads((d / "config.json").read_text(encoding="utf-8"))
    changed = cfg._migrate()
    check("a stale 0.45 is lifted to the default", cfg.data["wake"]["threshold"] == 0.60,
          cfg.data["wake"]["threshold"])
    check("...and the change is reported so it gets saved", changed)
    # Every fire between 0.45 and 0.59 in his log was followed by no speech at
    # all - 61 of them. Above it, the wakes are real.
    cfg2 = C.Config.__new__(C.Config)
    cfg2.data = {"wake": {"threshold": 0.75}, "config_version": 7}
    cfg2._migrate()
    check("a bar he set HIGHER himself is left alone", cfg2.data["wake"]["threshold"] == 0.75,
          cfg2.data["wake"]["threshold"])

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
