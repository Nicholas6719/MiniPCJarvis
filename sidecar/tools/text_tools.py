"""Working on "this": proofread, rewrite, summarise, translate what he has copied.

"This" is the clipboard - or, when Word is open with something highlighted,
the selection, which is replaced in place. Everything runs through the local
model, so the text never leaves the machine; that matters more here than
anywhere, because the clipboard is where passwords and half-written messages
live. Three rules follow from it:

  * The text is NEVER logged, and never put in the tool's result dict: that
    dict goes out on the event bus to the HUD and to the Telegram bridge.
    Only counts and a short confirmation travel.
  * The result goes BACK on the clipboard (or into the selection) so he can
    paste it, and the spoken reply is a short confirmation - having four
    paragraphs read aloud is not what he asked for. The summary is the one
    exception: it IS the answer, so it is spoken and not copied.
  * Past 4,000 characters the model loses the thread; the first 4,000 are
    worked on and he is told so, rather than silently handed a fragment.

The model call is the foreground side call (llm.provider.local_llm.stream on
the side slot) - not wait_for_quiet, which is for background work and would
hold his own request until he stopped talking.
"""
from __future__ import annotations

import logging
import re

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.text")

MAX_CHARS = 4000
CAP_NOTE = " I worked on the first four thousand characters; it was longer than that."
NOTHING_TO_DO = "There's nothing on the clipboard to work on, sir."
NO_SELECTION_LANGUAGE = "Which language should I translate it into, sir?"


# ------------------------------------------------------------ the model

async def _ask_model(prompt: str, max_tokens: int) -> str:
    """One foreground side call; the text and nothing else. Stubbed in the gate."""
    from llm.provider import local_llm
    out = ""
    async for ch in local_llm.stream([{"role": "user", "content": prompt}],
                                     max_tokens=max_tokens,
                                     sampling={"temperature": 0.1, "top_p": 0.9}):
        out += ch.text or ""
        if ch.done:
            break
    return out


def _tokens_for(text: str) -> int:
    """Roughly a token per three characters, with headroom - and a floor, since
    gpt-oss spends part of the budget thinking before it answers."""
    return max(400, min(1800, int(len(text) / 3) + 200))


def _unwrap(result: str) -> str:
    """Models wrap the answer in a fence or quotes despite being told not to."""
    out = (result or "").strip()
    out = re.sub(r"^```[a-zA-Z]*\s*\n?", "", out)
    out = re.sub(r"\n?```\s*$", "", out).strip()
    for fence in ("'''",):
        if out.startswith(fence) and out.endswith(fence) and len(out) > 6:
            out = out[3:-3].strip()
    if len(out) > 1 and out[0] == out[-1] and out[0] in "\"'“”":
        out = out[1:-1].strip()
    if out.startswith("“") and out.endswith("”"):
        out = out[1:-1].strip()
    return out


async def _run(instruction: str, text: str, budget: int) -> tuple[str, str, str]:
    """(result, note, problem). The note is the cap warning; problem is spoken as-is."""
    truncated = len(text) > MAX_CHARS
    body = text[:MAX_CHARS]
    prompt = (f"{instruction}\n\n"
              f"Return only the resulting text. No preamble, no explanation, "
              f"no quotation marks around it, no code fence.\n\n"
              f"---\n{body}\n---")
    try:
        raw = await _ask_model(prompt, budget)
    except Exception as e:
        # The TYPE of failure, never the text that was in flight.
        log.warning("text tool model call failed: %s", type(e).__name__)
        return "", "", f"The model didn't answer, so I've left it alone: {type(e).__name__}."
    result = _unwrap(raw)
    if not result:
        return "", "", "The model returned nothing, so I've left it alone."
    return result, (CAP_NOTE if truncated else ""), ""


# ------------------------------------------------------------ the source

def _word_selection() -> str | None:
    """The text highlighted in Word, or None when Word is closed or nothing is
    selected. Never starts Word (office._word attaches only)."""
    try:
        from tools import office
        wd = office._word()
        if wd is None or int(wd.Documents.Count) == 0:
            return None
        sel = str(wd.Selection.Text or "")
        sel = sel.replace("\r", "\n").replace("\x07", " ")
        return sel if len(sel.strip()) > 1 else None
    except Exception:
        log.debug("could not read Word's selection", exc_info=True)
        return None


class _Source:
    """What "this" is: the text, where it came from, how long it really was."""
    __slots__ = ("text", "origin", "problem", "length")

    def __init__(self, text: str = "", origin: str = "clipboard",
                 problem: str = "", length: int | None = None) -> None:
        self.text, self.origin, self.problem = text, origin, problem
        self.length = len(text) if length is None else int(length)

    @property
    def cap_note(self) -> str:
        return CAP_NOTE if self.length > MAX_CHARS else ""

    @property
    def where(self) -> str:
        return "in the document" if self.origin == "word" else "on your clipboard"


def _source() -> _Source:
    sel = _word_selection()
    if sel:
        return _Source(sel, "word")
    from tools.windows_tools import get_clipboard
    res = get_clipboard()
    if "error" in res:
        return _Source(problem=str(res["error"]))
    text = str(res.get("text") or "")
    if not text.strip():
        return _Source(problem=NOTHING_TO_DO)
    # get_clipboard caps at the same 4,000 and reports the real length, so
    # the cap note stays honest about a 9,000-character paste.
    return _Source(text, "clipboard", length=int(res.get("length") or len(text)))


def _put_back(result: str, origin: str) -> str:
    """Write the result where the text came from. Returns a problem or ''."""
    if origin == "word":
        from tools import office
        got = office.edit_open_document(result, mode="replace")
        if "error" in got:
            return f"I have it, but couldn't put it into Word: {got['error']}"
        return ""
    from tools.windows_tools import set_clipboard
    got = set_clipboard(result)
    if "error" in got:
        return "I have it, but couldn't put it back on the clipboard; the screen may be locked."
    return ""


def _reply(spoken: str, **extra) -> dict:
    out = {"spoken": spoken,
           "instruction": "Say only this confirmation. Never read the text back."}
    out.update(extra)
    return out


def _problem(problem: str) -> dict:
    return {"error": problem, "spoken": problem}


# ------------------------------------------------------------ the tools

async def proofread_clipboard() -> dict:
    """Fix spelling, grammar and punctuation; keep the wording."""
    src = _source()
    if src.problem:
        return _problem(src.problem)
    result, note, problem = await _run(
        "Correct any spelling, grammar and punctuation mistakes in the text "
        "below. Keep the wording, tone and meaning exactly as they are; change "
        "only what is wrong.", src.text, _tokens_for(src.text))
    if problem:
        return _problem(problem)
    note = note or src.cap_note
    if result.strip() == src.text[:MAX_CHARS].strip():
        return _reply("I found nothing to correct; it already reads correctly." + note,
                      changed=False, chars=src.length, origin=src.origin)
    problem = _put_back(result, src.origin)
    if problem:
        return _problem(problem)
    return _reply(f"Proofread, sir; the corrected text is {src.where}.{note}",
                  changed=True, chars=src.length, copied=len(result), origin=src.origin)


async def rewrite_clipboard(style: str = "clearer") -> dict:
    """Rewrite in a style: formal, friendlier, shorter, simpler, professional."""
    src = _source()
    if src.problem:
        return _problem(src.problem)
    style = " ".join((style or "clearer").split())[:60] or "clearer"
    result, note, problem = await _run(
        f"Rewrite the text below to be {style}. Preserve the meaning and any "
        f"facts, names and numbers exactly.", src.text, _tokens_for(src.text))
    if problem:
        return _problem(problem)
    note = note or src.cap_note
    problem = _put_back(result, src.origin)
    if problem:
        return _problem(problem)
    return _reply(f"Rewritten to be {style}, sir; it's {src.where}.{note}",
                  style=style, chars=src.length, copied=len(result), origin=src.origin)


async def summarise_clipboard() -> dict:
    """Two sentences, spoken. The one tool whose result IS the answer."""
    src = _source()
    if src.problem:
        return _problem(src.problem)
    result, note, problem = await _run(
        "Summarise the text below in at most two sentences, as you would say "
        "it out loud to the person who wrote it.", src.text, 600)
    if problem:
        return _problem(problem)
    note = note or src.cap_note
    summary = " ".join(result.split())
    return {"spoken": summary + note, "summary": summary, "chars": src.length,
            "origin": src.origin,
            "instruction": "This is the summary; say it as it is."}


async def translate_clipboard(language: str = "") -> dict:
    """Translate into a named language; the translation replaces the source."""
    language = " ".join((language or "").split()).strip(" .!?").title()
    if not language:
        return _problem(NO_SELECTION_LANGUAGE)
    src = _source()
    if src.problem:
        return _problem(src.problem)
    result, note, problem = await _run(
        f"Translate the text below into {language}. Keep names, numbers and "
        f"formatting as they are.", src.text, _tokens_for(src.text))
    if problem:
        return _problem(problem)
    note = note or src.cap_note
    problem = _put_back(result, src.origin)
    if problem:
        return _problem(problem)
    return _reply(f"Translated into {language}, sir; it's {src.where}.{note}",
                  language=language, chars=src.length, copied=len(result),
                  origin=src.origin)


def register_all() -> None:
    no_args = {"type": "object", "properties": {}, "required": []}
    registry.register(Tool(
        name="proofread_clipboard",
        description="Fix spelling, grammar and punctuation in the text he has copied "
                    "(or selected in Word) and put the corrected text back. For "
                    "'proofread this', 'fix this', 'tidy this up'. Speak only the "
                    "confirmation it returns.",
        parameters=no_args, risk=Risk.LOW, handler=proofread_clipboard, timeout=120))
    registry.register(Tool(
        name="rewrite_clipboard",
        description="Rewrite the copied (or Word-selected) text in a style - formal, "
                    "friendlier, shorter, simpler, more professional - and put it back. "
                    "Speak only the confirmation it returns.",
        parameters={"type": "object", "properties": {
            "style": {"type": "string", "description": "e.g. 'more formal', 'shorter'"}},
            "required": []},
        risk=Risk.LOW, handler=rewrite_clipboard, timeout=120))
    registry.register(Tool(
        name="summarise_clipboard",
        description="Summarise the copied (or Word-selected) text in two sentences and "
                    "say them. For 'summarise this', 'give me the gist'.",
        parameters=no_args, risk=Risk.LOW, handler=summarise_clipboard, timeout=120))
    registry.register(Tool(
        name="translate_clipboard",
        description="Translate the copied (or Word-selected) text into a language and "
                    "put the translation back. Speak only the confirmation it returns.",
        parameters={"type": "object", "properties": {
            "language": {"type": "string", "description": "e.g. 'Spanish'"}},
            "required": ["language"]},
        risk=Risk.LOW, handler=translate_clipboard, timeout=120))
