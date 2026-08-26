"""Turn spoken command phrasing into search keywords.

"show me iron man" must hit DuckDuckGo as "iron man", never verbatim — the user
watched his own words go into the search box. Two layers use this: the brain's
slot extractors (reflex path) and the tools themselves (web_search / show_images
/ research), because the LLM path passes whatever query the model wrote — often
the raw utterance.

Deliberately conservative: a lead verb alone is only stripped when it reads as a
command ("show ME ...", or a verb followed by "pictures of ..."). A query like
"display images in html" keeps its meaning; "how to show images in css" is left
alone entirely (question words anchor real content).
"""
from __future__ import annotations

import re

_MEDIA = r"(?:picture|photo|image|pic|shot|wallpaper)s?"
_COUNT = r"(\d{1,2}|a|an|some|a\s+few|a\s+couple(?:\s+of)?|several)"

_HEY = re.compile(r"^(?:hey\s+|ok\s+|okay\s+)?jarvis[,.!]?\s+", re.I)
_POLITE = re.compile(r"^(?:can\s+you\s+|could\s+you\s+|would\s+you\s+|please\s+)+", re.I)
# a verb is command phrasing when "me" follows, or when the media pattern follows
_VERB_ME = re.compile(
    r"^(?:show|find|get|pull\s+up|bring\s+up|display|give)\s+me\s+", re.I)
_VERB_MEDIA = re.compile(
    r"^(?:show|find|get|pull\s+up|bring\s+up|display|give)\s+"
    rf"(?={_COUNT}\s+{_MEDIA}\s+of\s+|{_MEDIA}\s+of\s+)", re.I)
_MEDIA_OF = re.compile(rf"^(?:{_COUNT}\s+)?{_MEDIA}\s+of\s+", re.I)
# "search"/"research" are also NOUNS ("research methods in psychology") — as bare
# leads they only strip before a determiner-ish word that marks command phrasing.
_SEARCH_LEAD = re.compile(
    r"^(?:"
    r"search(?:\s+the\s+web|\s+online|\s+google)?\s+for|"
    r"look\s+up|look\s+for|google|web\s+search(?:\s+for)?|"
    r"(?:search|research)(?=\s+(?:the|a|an|this|that|some|any|latest|current|best|top|cheap|new|what|who|how\s+much)\b)"
    r")\s+", re.I)
_TRAIL = re.compile(r"[\s,]*\b(?:please|for\s+me|thanks|thank\s+you)\b[.!?\s]*$", re.I)
_TRAIL_MEDIA = re.compile(rf"\s+{_MEDIA}$", re.I)
_QUESTION = re.compile(r"^(?:how|what|why|when|where|who|which|is|are|do|does|can)\b", re.I)

_NUM_WORDS = {"a": 1, "an": 1, "some": None, "a few": 3, "several": 4, "a couple": 2,
              "a couple of": 2}


def _count_of(word: str | None) -> int | None:
    if not word:
        return None
    w = re.sub(r"\s+", " ", word.strip().lower())
    if w.isdigit():
        return max(1, min(12, int(w)))
    return _NUM_WORDS.get(w)


def clean_image_query(text: str) -> tuple[str, int | None]:
    """"show me 5 images of spiderman" -> ("spiderman", 5). Returns the original
    text when nothing command-like leads it."""
    q = _TRAIL.sub("", text.strip().strip(" .?!"))
    q = _HEY.sub("", q)
    if _QUESTION.match(q):
        return q, None
    q = _POLITE.sub("", q)
    q = _VERB_ME.sub("", q)
    q = _VERB_MEDIA.sub("", q)
    count = None
    m = _MEDIA_OF.match(q)
    if m:
        count = _count_of(m.group(1))
        q = _MEDIA_OF.sub("", q, count=1)
    else:
        # "show me spiderman pictures" — the noun trails instead
        q = _TRAIL_MEDIA.sub("", q)
    q = q.strip(" .?!,")
    return (q, count) if len(q) >= 2 else (text.strip(), None)


def clean_search_query(text: str) -> str:
    """"search the web for the best mini pc" -> "the best mini pc"; questions and
    already-clean keyword queries pass through untouched."""
    q = _TRAIL.sub("", text.strip().strip(" .?!"))
    q = _HEY.sub("", q)
    if _QUESTION.match(q):
        return q
    q = _POLITE.sub("", q)
    q = _SEARCH_LEAD.sub("", q, count=1)
    q = _VERB_ME.sub("", q)
    q = q.strip(" .?!,")
    return q if len(q) >= 3 else text.strip()
