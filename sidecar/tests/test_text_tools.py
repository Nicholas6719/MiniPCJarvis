"""Proofread / rewrite / summarise / translate "this" - the clipboard, through
the local model, with the text never leaving the machine.

What is pinned: the result goes BACK on the clipboard and only a short
confirmation is spoken; the summary is the exception and is spoken; past
4,000 characters he is told the cap; a locked screen is reported in his words;
a Word selection is preferred and replaced in place; code fences and quotes
the model adds are stripped; and the clipboard text appears in no log line and
no result dict (those go to the HUD and to Telegram).

The model is a stub; the clipboard and Word are stand-ins.
Run: python tests/test_text_tools.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "text.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:
            self.lines.append(str(record.msg))


SECRET = "teh cat sat on teh mat hunter2"      # what is on the clipboard


def main() -> int:
    from tools import text_tools as T
    from tools import windows_tools as W
    from tools import office

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "tools", "text_tools.py"), encoding="utf-8").read()

    cap = _Capture()
    logging.getLogger().addHandler(cap)
    logging.getLogger().setLevel(logging.DEBUG)

    # ---- stand-ins
    state = {"clip": SECRET, "clip_error": "", "set": [], "model": "The cat sat on the mat.",
             "prompts": [], "raise": False, "word": None, "edits": []}

    def fake_get():
        if state["clip_error"]:
            return {"error": state["clip_error"], "locked": True}
        t = state["clip"]
        return {"text": t[:W.CLIPBOARD_CAP], "truncated": len(t) > W.CLIPBOARD_CAP,
                "length": len(t)}

    def fake_set(text):
        state["set"].append(text)
        return {"copied": len(text)}

    async def fake_model(prompt, max_tokens):
        state["prompts"].append((prompt, max_tokens))
        if state["raise"]:
            raise ConnectionError("llama-server is down")
        return state["model"]

    def fake_edit(text="", mode="insert", style=""):
        state["edits"].append((text, mode))
        return {"ok": True, "mode": mode}

    saved = (W.get_clipboard, W.set_clipboard, T._ask_model, T._word_selection,
             office.edit_open_document)
    W.get_clipboard, W.set_clipboard = fake_get, fake_set
    T._ask_model = fake_model
    T._word_selection = lambda: state["word"]
    office.edit_open_document = fake_edit

    def run(coro):
        return asyncio.run(coro)

    try:
        print("\n-- proofread: back on the clipboard, confirmation only --")
        res = run(T.proofread_clipboard())
        check("the corrected text went back on the clipboard",
              state["set"] == ["The cat sat on the mat."], str(state["set"]))
        check("the spoken line is a short confirmation naming the clipboard",
              "clipboard" in res.get("spoken", "") and "The cat" not in res["spoken"],
              str(res.get("spoken")))
        blob = json.dumps(res)
        check("neither the source nor the result is in the result dict (it goes to the HUD and the phone)",
              SECRET not in blob and "hunter2" not in blob and "The cat sat" not in blob, blob[:200])
        check("the model saw the text, framed as data between markers",
              SECRET in state["prompts"][-1][0] and "---" in state["prompts"][-1][0])
        state["model"] = SECRET
        res = run(T.proofread_clipboard())
        check("nothing to correct is said as such, and the clipboard is left alone",
              "nothing to correct" in res.get("spoken", "") and len(state["set"]) == 1,
              str(res.get("spoken")))

        print("\n-- the cap: four thousand characters, and he is told --")
        state["clip"] = "word " * 1800            # 9,000 characters
        state["model"] = "shorter"
        res = run(T.rewrite_clipboard("shorter"))
        body = state["prompts"][-1][0].split("---")[1]
        check("the model is given at most 4,000 characters", len(body.strip()) <= T.MAX_CHARS, str(len(body)))
        check("...and the confirmation says so",
              "first four thousand characters" in res.get("spoken", ""), str(res.get("spoken")))
        check("...after naming the style", "shorter" in res.get("spoken", ""))
        state["clip"] = SECRET
        res = run(T.rewrite_clipboard("more formal"))
        check("under the cap there is no note", "four thousand" not in res.get("spoken", ""))

        print("\n-- a locked screen is said in his words, and the model is not called --")
        n = len(state["prompts"])
        state["clip_error"] = W.CLIPBOARD_UNREACHABLE
        res = run(T.summarise_clipboard())
        check("the error is the clipboard wording",
              res.get("error") == "I can't reach the clipboard; the screen may be locked."
              and res.get("spoken") == res["error"], str(res))
        check("...and no model call was made", len(state["prompts"]) == n)
        state["clip_error"] = ""
        state["clip"] = "   "
        res = run(T.proofread_clipboard())
        check("an empty clipboard says so", res.get("error") == T.NOTHING_TO_DO, str(res))
        state["clip"] = SECRET

        print("\n-- the summary is SPOKEN, not copied --")
        n_set = len(state["set"])
        state["model"] = "A cat sat on a mat. That is all."
        res = run(T.summarise_clipboard())
        check("the summary is the spoken line",
              res.get("spoken") == "A cat sat on a mat. That is all.", str(res.get("spoken")))
        check("...and the clipboard was not touched", len(state["set"]) == n_set)

        print("\n-- translate --")
        res = run(T.translate_clipboard(""))
        check("no language -> it asks, and reads nothing",
              "language" in res.get("error", "").lower(), str(res))
        state["model"] = "El gato."
        res = run(T.translate_clipboard("spanish"))
        check("the translation went back and the language is named, capitalised",
              state["set"][-1] == "El gato." and "Spanish" in res.get("spoken", ""), str(res))

        print("\n-- the model's wrapping is stripped --")
        for wrapped, want in (("```\nThe cat.\n```", "The cat."), ("```text\nThe cat.```", "The cat."),
                              ('"The cat."', "The cat."), ("“The cat.”", "The cat."),
                              ("'''The cat.'''", "The cat.")):
            state["model"] = wrapped
            run(T.proofread_clipboard())
            check(f"{wrapped!r} -> {want!r}", state["set"][-1] == want, repr(state["set"][-1]))

        print("\n-- a Word selection is preferred and replaced in place --")
        state["word"] = "teh dog"
        state["model"] = "The dog."
        n_set = len(state["set"])
        res = run(T.proofread_clipboard())
        check("the selection was replaced through edit_open_document(mode='replace')",
              state["edits"] == [("The dog.", "replace")], str(state["edits"]))
        check("...the clipboard was not touched", len(state["set"]) == n_set)
        check("...and the confirmation says 'in the document'",
              "in the document" in res.get("spoken", ""), str(res.get("spoken")))
        state["word"] = None

        print("\n-- a model failure names the kind, never the text --")
        state["raise"] = True
        res = run(T.proofread_clipboard())
        check("the failure is an error he can hear",
              "didn't answer" in res.get("error", "") and "ConnectionError" in res["error"], str(res))
        state["raise"] = False

        print("\n-- nothing of the clipboard reaches a log line --")
        leaked = [l for l in cap.lines if SECRET in l or "hunter2" in l or "The cat" in l
                  or "El gato" in l or "teh dog" in l]
        check("no log line carries the text", not leaked, str(leaked)[:200])
        check("the source never logs the text (no log call with text/result/prompt)",
              "log.info(" not in src and "log.debug(\"%s\", text" not in src
              and "exc_info=True)" in src and "log.exception" not in src)
        check("it uses the foreground side call, not wait_for_quiet",
              "wait_for_quiet(" not in src and "import wait_for_quiet" not in src
              and "local_llm.stream(" in src)
        check("it never talks to Telegram", "remote_telegram" not in src and "_send(" not in src)

        print("\n-- the reflexes: 'this' is the clipboard, and only 'this' --")
        from brain.skills import SKILLS, slots_rewrite, slots_this, slots_translate
        names = {s.name: s for s in SKILLS}
        for n_, tool in (("proofread", "proofread_clipboard"), ("rewrite", "rewrite_clipboard"),
                         ("summarise", "summarise_clipboard"), ("translate", "translate_clipboard")):
            check(f"skill {n_} -> {tool}", n_ in names and names[n_].tool == tool)
        check("slots: 'rewrite this to be more formal' -> more formal",
              slots_rewrite("rewrite this to be more formal") == {"style": "more formal"},
              str(slots_rewrite("rewrite this to be more formal")))
        check("slots: 'make this shorter' -> shorter",
              slots_rewrite("make this shorter") == {"style": "shorter"})
        check("slots: 'simplify this' -> simpler",
              slots_rewrite("simplify this") == {"style": "simpler"})
        check("slots: 'translate this into spanish' -> spanish",
              slots_translate("translate this into spanish") == {"language": "spanish"})
        check("slots: 'put this in italian' -> italian",
              slots_translate("put this in italian") == {"language": "italian"})
        check("slots: 'translate this' -> {} (the tool asks)", slots_translate("translate this") == {})
        check("'fix this bug' is NOT the clipboard", slots_this("fix this bug") is None)
        check("'summarise this article' is NOT the clipboard", slots_this("summarise this article") is None)
        check("'proofread my essay' is NOT the clipboard", slots_this("proofread my essay") is None)
        check("'fix this' is", slots_this("fix this") == {})
        check("'proofread what i copied' is", slots_this("proofread what i copied") == {})

        from brain.router import _norm
        for said in ("proofread this", "fix this", "summarise this", "tidy this up",
                     "translate this into spanish", "rewrite this to be more formal"):
            check(f"_CANON keeps 'this' in {said!r}", "this" in _norm(said), _norm(said))
    finally:
        (W.get_clipboard, W.set_clipboard, T._ask_model, T._word_selection,
         office.edit_open_document) = saved
        logging.getLogger().removeHandler(cap)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
