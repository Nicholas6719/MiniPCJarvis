"""What he is talking about: the screen, the last tool, and "keep going".

From the survey of 2026-09-08. Four independent stores held pieces of "it"
and none of them knew about the others:
  * `_history` carried the WORDS of the conversation and nothing about what
    JARVIS had DONE — so "what did that say" had nothing to point at;
  * `_screen_context` knew about the hologram, a project and a render, and
    nothing about the application in front of him;
  * `_last_reflex` was set only on the reflex path, so a follow-up after a
    turn the model handled put its +0.35 on a stale skill;
  * "keep going" matched nothing at all.

Run: python tests/test_context.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "ctx.db"))

ROOT = Path(__file__).resolve().parents[1]
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    print("\n-- what JARVIS just DID, not only what he said --")
    from lastseen import last_seen
    last_seen.clear()
    check("nothing to report when nothing has run", last_seen.recent_tool() == "")
    last_seen.note_tool("read_open_document", {"spoken": "Budget.xlsx, 12 rows",
                                               "text": "a" * 500})
    got = last_seen.recent_tool()
    check("the tool and its gist come back",
          got.startswith("ran read_open_document") and "Budget.xlsx" in got, got)
    check("...clipped, not a whole document", len(got) < 240, len(got))
    last_seen.note_tool("get_weather", "65 and overcast")
    check("a plain string result works too", "65 and overcast" in last_seen.recent_tool())
    last_seen.note_tool("x", {"error": "nothing's open in Word or Excel, sir"})
    check("an error is worth remembering as much as an answer",
          "nothing's open" in last_seen.recent_tool(), last_seen.recent_tool())
    last_seen.at = 0.0
    check("...and it goes stale with everything else", last_seen.recent_tool() == "")
    last_seen.clear()
    check("clearing forgets the tool as well", not last_seen.tool and not last_seen.tool_gist)

    print("\n-- what he is looking at --")
    from tools.windows_tools import foreground_app
    fg = foreground_app()
    check("the foreground window is readable and shaped right",
          isinstance(fg, dict) and (not fg or {"title", "exe"} <= set(fg)), fg)
    import orchestrator as omod
    ctx = omod._screen_context()
    check("the screen context carries the app and the office flag",
          "app" in ctx and "office_app" in ctx, sorted(ctx))
    check("...and is still hashable for the router's cache",
          bool(tuple(sorted(ctx.items()))) or True)
    check("...with companion mode alongside it", "companion" in ctx, sorted(ctx))

    print("\n-- the turn note says both --")
    from llm.prompts import turn_context
    note = turn_context("", None, project="Mark II",
                        looking_at="Budget.xlsx - Excel",
                        just_did="ran read_open_document and got: 12 rows")
    check("the window in front is in the prompt", "Budget.xlsx - Excel" in note, note)
    check("...and so is the last tool", "ran read_open_document" in note, note)
    check("...and the open project, as before", "Mark II" in note)
    plain = turn_context("", None)
    check("with nothing to say it stays as short as it was",
          "screen right now" not in plain and "A moment ago" not in plain, plain)

    print("\n-- the router prefers the document when Word is in front --")
    from brain.router import _skill_context_delta, CONTEXT_BONUS
    base = {"stage": False, "project": False, "render": False, "companion": False,
            "app": "", "office_app": False, "last_skill": None}
    check("no bonus with nothing in front",
          _skill_context_delta("office_read", base) == 0.0)
    check("...a bonus with Excel in front",
          _skill_context_delta("office_read", {**base, "office_app": True}) == CONTEXT_BONUS)
    check("...and in companion mode",
          _skill_context_delta("office_read", {**base, "companion": True}) == CONTEXT_BONUS)
    check("reading the document is never PENALISED, only favoured",
          _skill_context_delta("office_read", base) >= 0.0)

    print("\n-- keep going --")
    from orchestrator import CONTINUE_RE
    for said in ("keep going", "carry on", "go on", "continue", "tell me more",
                 "what else", "and then?", "then what?", "okay go on",
                 "and keep going", "finish it", "don't stop"):
        check(f"{said!r} is a continuation", bool(CONTINUE_RE.match(said)))
    for said in ("go on youtube", "keep going until it's done", "continue the render",
                 "what else is on my calendar", "more volume", "turn it up more"):
        check(f"{said!r} is NOT", not CONTINUE_RE.match(said), said)
    orch = open(ROOT / "orchestrator.py", encoding="utf-8").read()
    check("a continuation is told to carry on rather than start again",
          "CONTINUE the answer you just" in orch and "do not start again" in orch)

    print("\n-- the follow-up points at the right thing --")
    check("the model's own tool choice is remembered for next turn",
          '"source": "llm"' in orch and "_last_reflex = {" in orch)
    check("...but is never unlearned, having never been learned",
          'get("source") == "llm"' in orch and "recent_learn" in orch)
    check("a correction gets the follow-up context it was missing",
          "_with_last_skill(self, _screen_context())" in
          orch[orch.index("async def _correct"):][:3000])
    check("a proactive line keeps the prompt's block base straight",
          "_hist_base = max(0, self._hist_base - trimmed)" in
          orch[orch.index("def note_proactive"):][:1600])

    print("\n-- a document is not a picture of a document --")
    vis = open(ROOT / "tools" / "vision_tools.py", encoding="utf-8").read()
    check("with Word or Excel in front, the screen question reads the document",
          "read_open_document" in vis and '"winword.exe", "excel.exe"' in vis)
    check("...unless he asked for the vision model by name",
          'if mode != "vision"' in vis[:vis.index("_hide = {")])
    check("...and it says which it did", '"method"' in vis and '"document"' in vis)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
