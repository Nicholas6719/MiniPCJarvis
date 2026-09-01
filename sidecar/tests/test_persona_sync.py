"""The persona document and the running persona must agree about who he is.

For months JARVIS_PERSONA.md was loaded by NOTHING. It was a careful, detailed
specification that no code had ever read, while the persona actually shipping
lived in llm/prompts.py — and the two disagreed. The document said "sir" should
be rare and optional; the running system says it 37% of the time, which is the
measured film rate and the behaviour he actually wants. Nobody noticed, because
nothing could notice.

The visible cost was small and awful: he asked JARVIS who he was and was told
"user". The prompt named him zero times and said "the user" six times, and every
memory about him was written in the third person ("The user's favorite color
is..."), which taught it that "the user" was his identity.

So this gate is narrow and blunt on purpose. It does not try to diff prose — a
spec should be free to say things more fully than a prompt. It asserts that the
FACTS about him survive in both places, and that neither has quietly reverted to
calling him a user.

Offline, no model, no camera. Run: python tests/test_persona_sync.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DOC = os.path.join(REPO, "JARVIS_PERSONA.md")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from llm.prompts import system_prompt, turn_context

    check("the persona document exists where the gate expects it",
          os.path.exists(DOC), DOC)
    doc = open(DOC, encoding="utf-8").read() if os.path.exists(DOC) else ""
    prompt = system_prompt()

    # --- he has a name, in both places -------------------------------------
    check("the running prompt names him", "Nicholas" in prompt)
    check("the document names him", "Nicholas" in doc)
    check("the prompt answers 'who am I' explicitly",
          "You're Nicholas" in prompt or "you're nicholas" in prompt.lower())

    # --- and is never called "the user" ------------------------------------
    # One mention survives on purpose: the instruction FORBIDDING the phrase.
    stray = [m.start() for m in re.finditer(r"the user", prompt)]
    allowed = [i for i in stray
               if "Never call him" in prompt[max(0, i - 120):i + 20]]
    check("the prompt never refers to him as 'the user'",
          len(stray) == len(allowed),
          f"{len(stray) - len(allowed)} stray mention(s)")
    check("...nor does the memory header he sees every turn",
          "the user" not in turn_context("- he likes blue"),
          turn_context("- he likes blue"))

    # --- the facts he corrected by hand, in both places --------------------
    # He struck these himself on 2026-09-01 after JARVIS recited a memory table
    # full of things that were simply not true about him. They must not come
    # back, in either file, ever.
    for wrong in ("Natick", "Regina", "coffee black", "navy", "desk lamp",
                  "retainer"):
        check(f"the document does not claim: {wrong!r}",
              wrong.lower() not in doc.lower())
        check(f"the prompt does not claim: {wrong!r}",
              wrong.lower() not in prompt.lower())

    for right, where in (("Framingham", "his town"), ("Sudbury", "his town"),
                         ("blue", "his colour")):
        check(f"the prompt keeps {where}: {right}", right.lower() in prompt.lower())
        check(f"the document keeps {where}: {right}", right.lower() in doc.lower())

    # --- the stance he asked for, not a five-ticker cage -------------------
    check("the prompt says his interest is the market broadly",
          "stock market broadly" in prompt)
    check("...and still refuses personalised advice",
          "not a licensed adviser" in prompt or "licensed adviser" in prompt)

    # --- the rule that caused this whole exchange --------------------------
    check("the prompt forbids inventing facts about him",
          "NEVER INVENT A FACT ABOUT HIM" in prompt)
    check("...and gives it the words to say instead",
          "I don't know that about you" in prompt)
    check("the document carries the same rule",
          "Never invent a fact about him" in doc)

    # --- quiet hours agree with config -------------------------------------
    from config import config
    qe = str(config.get("briefing", "quiet_end", default=""))
    check("config quiet hours end at 05:30", qe == "05:30", qe)
    check("...and the document says the same",
          "05:30" in doc, "document and config disagree about quiet hours")

    # --- the honorific contradiction that went unnoticed for months --------
    # The old document said "sir" was optional and rare while the shipped
    # system used it at the measured film rate. A spec that contradicts the
    # machine is worse than no spec.
    check("the document no longer calls the honorific rare/optional",
          not re.search(r"\*\*.sir..\*\* is optional, rare", doc))
    check("the document states the measured rate instead", "37%" in doc)

    # --- the prompt must stay static, or the KV cache dies -----------------
    check("the system prompt is byte-identical across calls",
          system_prompt() == prompt,
          "a varying prefix costs ~10 s of first-token latency")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
