"""What a click must never be allowed to hit, wherever it happens.

One list, because two places click something by its name - a native control
through UI Automation (tools/uia.py), a web element through Playwright
(browser/session.py) - and a wrong click in either is the same category of
mistake: not "the wrong song", but a purchase, a subscription, an account
gone, a drive wiped. Two copies would drift; both import this one.

The owner's decision (2026-10): these are refused OUTRIGHT. No "yes" overrides
a refusal, because the confirmation question is the thing that fails when it
matters - a "Do it" tapped on a phone, a "yes" meant for a different question.
JARVIS says whose click it is and leaves it alone.

The list is tested against the MATCHED element's real name, never the words
he used: "click the big yellow button" matched "Buy now" on a real page, and
the request contained no dangerous word at all.

Decisions worth recording, because each one is a judgment call:

  * "Delete" alone is ALLOWED. It deletes a line, a row, an email to the bin -
    reversible things he asks for all day. "Delete account", "delete
    permanently" and "delete forever" are refused.
  * "Format" alone is REFUSED. Explorer's context menu on a drive says
    exactly "Format..." and nothing more. "Format Painter" and "Format
    Cells" (Word, Excel) change how text looks and are allowed.
  * "PayPal", "Apple Pay", "Google Pay" are pay buttons and refused; "Payment
    methods" is a settings page and allowed.
  * "Save" is allowed; "Don't save" and "Close without saving" are refused:
    they throw his work away.
  * "Remove" alone is allowed (a row, a tag); "Remove account" is not.
"""
from __future__ import annotations

import re

# Word-bounded, case-insensitive. Each alternative is a whole phrase, so
# "paypal" is not "pay" and "formatting" is not "format".
DANGEROUS = re.compile(
    r"\b(?:"
    r"buy(?:\s+now|\s+again|\s+it\s+now)?|purchase(?!\s+(?:history|orders?))|"
    r"complete\s+(?:purchase|order)|"
    r"order\s+now|place\s+(?:your\s+|the\s+|my\s+|this\s+)?order|"
    r"pay(?:\s+now)?|paypal|apple\s+pay|google\s+pay|"
    r"check\s?out|proceed\s+to\s+check\s?out|"
    r"confirm\s+(?:payment|purchase|order|and\s+pay)|"
    r"add\s+to\s+(?:cart|bag|basket)|"
    r"redeem|apply\s+coupon|subscribe(?:\s+(?:now|&|and)\s*\w*)?|"
    r"unsubscribe|cancel\s+(?:my\s+)?(?:subscription|membership|plan)|"
    r"(?:delete|remove|close|deactivate)\s+(?:my\s+|your\s+|this\s+)?account|"
    r"delete\s+(?:permanently|forever)|permanently\s+delete|"
    r"deactivate|uninstall|"
    # "Format..." on a drive's context menu is refused; Word's "Format Painter"
    # and Excel's "Format Cells" only change how text looks and stay clickable.
    r"format(?!\s+(?:painter|text|cells?|paragraphs?|shapes?|pictures?|fonts?|"
    r"background|as\s+table|table))|"
    r"erase|wipe|"
    r"empty\s+(?:the\s+)?(?:trash|recycle\s+bin|bin)|"
    r"discard(?:\s+changes)?|close\s+without\s+saving|"
    r"don'?t\s+save|do\s+not\s+save"
    r")\b",
    re.I)


def is_dangerous(name: str) -> str | None:
    """The dangerous phrase inside a control's real name, or None if it is fine."""
    m = DANGEROUS.search((name or "").replace("’", "'"))
    return m.group(0) if m else None


def refusal(control: str, matched: str | None = None) -> dict:
    """The result both click sites return: an error the orchestrator speaks as-is."""
    label = (control or "").strip()[:80]
    return {
        "error": f"I won't click '{label}' myself, sir. That one is yours.",
        "refused": True,
        "control": label,
        "matched": matched or is_dangerous(label) or "",
    }
