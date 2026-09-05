"""Arithmetic as a reflex. "What's 17 times 23" went to the model and took
seventeen seconds; his verdict: "that should be instant."

Pure text in, a number out. No eval(): the sentence is normalised (number
words to digits, operator words to symbols) and parsed by a small recursive
descent, so nothing he says can run as code. Anything that is not clearly a
sum returns None and routes as before — "set the volume to 50 percent" has a
number and the word percent and is not a sum.
"""
from __future__ import annotations

import math
import re

_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
         "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
         "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
         "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90}
_SCALE = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}

_LEAD = re.compile(
    r"^(?:hey |hi |ok |okay )?(?:jarvis[,!.]?\s*)?"
    r"(?:(?:can|could|would) you\s+)?(?:please\s+)?"
    r"(?:what(?:'s| is| does)\s+|how much is\s+|calculate\s+|compute\s+|work out\s+|"
    r"tell me\s+|give me\s+)?(?:the\s+(?:answer|result|value)\s+(?:of|to)\s+)?",
    re.I)
_TRAIL = re.compile(r"(?:\s*(?:,|please|sir|equal(?:s)?|make|come to|be|is|equal to|\?|\.|!))+$", re.I)

# Operator words to symbols. Order matters: longer phrases first.
_OPS = [
    (r"\bto the power of\b|\braised to(?: the power of)?\b|\bto the\b(?=\s*\d+(?:st|nd|rd|th)\b)", " ^ "),
    (r"\bthe square root of\b|\bsquare root of\b|\bthe root of\b|\broot of\b|\bsqrt\b", " sqrt "),
    (r"\bsquared\b", " ^ 2 "),
    (r"\bcubed\b", " ^ 3 "),
    (r"\bpercent of\b|% of\b|\bpercentage of\b", " %of "),
    (r"\bhalf of\b|\ba half of\b", " 0.5 * "),
    (r"\ba third of\b", " 0.333333333333 * "),
    (r"\ba quarter of\b", " 0.25 * "),
    (r"\bdouble\b", " 2 * "),
    (r"\btriple\b", " 3 * "),
    (r"\bdivided by\b|\bover\b|\bdivide\b|/|÷", " / "),
    (r"\bmultiplied by\b|\btimes\b|\bmultiply\b|×|\*|(?<=\d)\s*x\s*(?=\d)", " * "),
    (r"\bplus\b|\badded to\b|\badd\b|\+", " + "),
    (r"\bminus\b|\bsubtract\b|\btake away\b|\bless\b|(?<=\d)\s*-\s*(?=\d)", " - "),
    (r"\bmod(?:ulo)?\b|\bremainder of\b", " mod "),
    (r"\bpoint\b", " . "),
]
_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)\b")


def _words_to_digits(t: str) -> str:
    """"seventeen times twenty three" -> "17 times 23"; "two hundred and five" -> 205."""
    toks = t.split()
    out: list[str] = []
    i = 0
    while i < len(toks):
        w = toks[i]
        if w in _ONES or w in _TENS or w in _SCALE or (w == "a" and i + 1 < len(toks)
                                                          and toks[i + 1] in _SCALE):
            total, cur = 0, 0
            j = i
            while j < len(toks):
                x = toks[j]
                if x == "a" and j + 1 < len(toks) and toks[j + 1] in _SCALE:
                    cur = 1
                elif x in _ONES:
                    cur += _ONES[x]
                elif x in _TENS:
                    cur += _TENS[x]
                elif x in _SCALE:
                    cur = (cur or 1) * _SCALE[x]
                    if _SCALE[x] >= 1000:
                        total += cur
                        cur = 0
                elif x == "and" and j + 1 < len(toks) and (toks[j + 1] in _ONES or toks[j + 1] in _TENS):
                    pass
                else:
                    break
                j += 1
            out.append(str(total + cur))
            i = j
        else:
            out.append(w)
            i += 1
    return " ".join(out)


_TOKEN = re.compile(r"\d+(?:\.\d+)?|\^|sqrt|%of|mod|[+\-*/()]|\.")


def _tokens(t: str) -> list[str] | None:
    body = _TOKEN.findall(t)
    # Every non-space character must be accounted for: a stray word means this
    # is a sentence about numbers, not a sum.
    rest = _TOKEN.sub("", t).replace(" ", "")
    if rest:
        return None
    # "3 . 5" (from "three point five") -> 3.5
    joined: list[str] = []
    k = 0
    while k < len(body):
        if k + 2 < len(body) and body[k + 1] == "." and body[k].replace(".", "").isdigit() \
                and body[k + 2].isdigit():
            joined.append(f"{body[k]}.{body[k + 2]}")
            k += 3
        elif body[k] == ".":
            return None
        else:
            joined.append(body[k])
            k += 1
    return joined


class _Parser:
    def __init__(self, toks: list[str]) -> None:
        self.t = toks
        self.i = 0

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> str:
        v = self.t[self.i]
        self.i += 1
        return v

    def expr(self) -> float:
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            r = self.term()
            v = v + r if op == "+" else v - r
        return v

    def term(self) -> float:
        v = self.factor()
        while self.peek() in ("*", "/", "mod", "%of"):
            op = self.take()
            r = self.factor()
            if op == "*":
                v = v * r
            elif op == "/":
                if r == 0:
                    raise ZeroDivisionError
                v = v / r
            elif op == "mod":
                if r == 0:
                    raise ZeroDivisionError
                v = math.fmod(v, r)
            else:                       # a percent of b
                v = v / 100.0 * r
        return v

    def factor(self) -> float:
        b = self.base()
        if self.peek() == "^":
            self.take()
            e = self.factor()           # right-associative
            if abs(e) > 64 or abs(b) > 1e6:
                raise OverflowError
            b = b ** e
        return b

    def base(self) -> float:
        p = self.peek()
        if p is None:
            raise ValueError
        if p == "sqrt":
            self.take()
            v = self.base()
            if v < 0:
                raise ValueError
            return math.sqrt(v)
        if p == "-":
            self.take()
            return -self.base()
        if p == "(":
            self.take()
            v = self.expr()
            if self.peek() != ")":
                raise ValueError
            self.take()
            return v
        if re.fullmatch(r"\d+(?:\.\d+)?", p):
            return float(self.take())
        raise ValueError


_SAY = {"+": "plus", "-": "minus", "*": "times", "/": "divided by", "^": "to the power of",
        "mod": "mod", "%of": "percent of", "sqrt": "the square root of", "(": "", ")": ""}


def fmt(v: float) -> str:
    if abs(v) >= 1e15:
        return f"{v:.3g}"
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}"
    s = f"{v:,.4f}".rstrip("0").rstrip(".")
    return s


def parse(text: str) -> dict | None:
    """{"expr": "17 times 23", "value": 391.0, "said": "17 times 23 is 391."} or None."""
    # A unit conversion is arithmetic with a table - "how many milliliters in
    # a cup" went to the model's near-miss path and came back as "did you
    # mean render that in 3D". Tried first: its shapes are narrower.
    from brain import units
    conv = units.convert(text)
    if conv:
        return conv
    t = (text or "").strip().lower()
    t = _LEAD.sub("", t, count=1)
    t = _TRAIL.sub("", t)
    t = _words_to_digits(t)
    for pat, rep in _OPS:
        t = re.sub(pat, rep, t)
    t = _ORDINAL.sub(r"\1", t)          # after the ops: "to the 10th" needs its "th"
    t = re.sub(r"\s+", " ", t).strip()
    if not re.search(r"\d", t):
        return None
    toks = _tokens(t)
    if not toks:
        return None
    if not any(x in ("+", "-", "*", "/", "^", "mod", "%of", "sqrt") for x in toks):
        return None
    numbers = [x for x in toks if re.fullmatch(r"\d+(?:\.\d+)?", x)]
    if not numbers or (len(numbers) < 2 and "sqrt" not in toks and "^" not in toks):
        return None
    p = _Parser(toks)
    try:
        v = p.expr()
    except ZeroDivisionError:
        return {"expr": _spoken(toks), "error": "division by zero",
                "said": "I'm afraid that divides by zero, sir."}
    except (ValueError, OverflowError, IndexError):
        return None
    if p.i != len(toks) or not math.isfinite(v):
        return None
    if abs(v) > 1e18:
        return None
    expr = _spoken(toks)
    return {"expr": expr, "value": v, "said": f"{expr} is {fmt(v)}."}


def _spoken(toks: list[str]) -> str:
    out: list[str] = []
    for x in toks:
        if re.fullmatch(r"\d+(?:\.\d+)?", x):
            out.append(fmt(float(x)))
        else:
            w = _SAY.get(x, x)
            if w:
                out.append(w)
    return " ".join(out)


def slots(text: str) -> dict | None:
    return parse(text)


def say(slots_: dict, _res: dict) -> str:
    return str(slots_.get("said") or "I couldn't work that out, sir.")
