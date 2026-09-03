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
_COUNT = (r"(\d{1,2}|a|an|some|a\s+few|a\s+couple(?:\s+of)?|several|"
          r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)")

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

# SPELLED-OUT NUMBERS COUNT TOO. "5 images of spiderman" gave ("spiderman", 5)
# and "two images of iron man" gave ("two images of iron man", None) — the digit
# was understood and the word was not, so the count was lost AND the phrase
# "two images of" went to the search engine as part of the subject. He heard it
# back as "Here are some pictures of two images of iron man."
_NUM_WORDS = {"a": 1, "an": 1, "some": None, "a few": 3, "several": 4, "a couple": 2,
              "a couple of": 2,
              "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
              "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
              "twelve": 12}


def _count_of(word: str | None) -> int | None:
    if not word:
        return None
    w = re.sub(r"\s+", " ", word.strip().lower())
    if w.isdigit():
        return max(1, min(12, int(w)))
    return _NUM_WORDS.get(w)


# HE STARTS SENTENCES TWICE. Dictation captures it verbatim:
#
#   "Show me th show me three images of Tom Hall and Spider Man"
#
# ...and DuckDuckGo was duly asked for "th show me three images of tom hall and
# spider man". People restart mid-phrase constantly and the recogniser has no
# idea it happened; only the repetition gives it away.
#
# Deliberately narrow. It fires ONLY when a command lead-in appears twice near
# the start with almost nothing between the two — the shape of a false start.
# "Show me the show me your work sign" is not that, and neither is any sentence
# where the repeat is further in.
_RESTART_LEAD = (r"(?:show|tell|give|find|get|make|create|bring|pull)\s+me"
                 r"|can\s+you|could\s+you|i\s+want|i\s+need|search\s+for"
                 r"|look\s+up|hey\s+jarvis|jarvis")
_RESTART = re.compile(rf"^\s*(?:{_RESTART_LEAD})\b[\s,]*"
                      rf"(?P<gap>(?:[\w']+[\s,]+){{0,2}})"
                      rf"(?=(?:{_RESTART_LEAD})\b)", re.I)

# What may sit between the false start and the restart. A DROPPED FRAGMENT
# ("th"), or an audible hesitation — nothing that carries meaning.
#
# Checked rather than left to a length rule, because a short word is not the
# same as a fragment: "search for how to show me the money" has "how to" in that
# position and lost it, which is a real query mangled to fix an imagined one.
_RESTART_FILLER = {"uh", "um", "er", "erm", "ah", "eh", "hmm", "mm", "like",
                   "sorry", "wait", "no", "actually", "i", "mean"}


def _is_false_start(gap: str) -> bool:
    """Is what sits between the two lead-ins junk rather than words?"""
    for w in re.findall(r"[\w']+", gap.lower()):
        if w in _RESTART_FILLER:
            continue
        # A fragment: too short to be a word he meant, and not one of the short
        # words that genuinely carry meaning.
        if len(w) <= 3 and w not in {"the", "a", "an", "to", "of", "in", "on",
                                     "at", "it", "is", "my", "us", "you", "how",
                                     "why", "who", "and", "for", "but", "not",
                                     "all", "any", "new", "top", "one", "two",
                                     "six", "ten", "up", "me", "we", "so"}:
            continue
        return False
    return True


def strip_restart(text: str) -> str:
    """Drop a false start, keeping the sentence he actually finished."""
    out = (text or "").strip()
    for _ in range(2):            # "show me, uh, show me..." — twice, no more
        m = _RESTART.match(out)
        if not m or not _is_false_start(m.group("gap")):
            break
        new = out[m.end():].strip()
        if len(new) < 3:
            break
        out = new
    return out or (text or "").strip()


def clean_image_query(text: str) -> tuple[str, int | None]:
    """"show me 5 images of spiderman" -> ("spiderman", 5). Returns the original
    text when nothing command-like leads it."""
    q = strip_restart(text)
    q = _TRAIL.sub("", q.strip().strip(" .?!"))
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


# "MORE" IS NOT A SUBJECT. After "show me two images of Iron Man", the natural
# follow-up is "show me three more images" — and cleaning that gives the keywords
# "three more", which went to the search engine literally. He got pictures of the
# words "three more" and was told "Here are some pictures of three more."
#
# Matched against the ALREADY-CLEANED query, so the lead verb is gone by now.
_MORE = re.compile(
    rf"^(?:{_COUNT}\s+)?(?:more|other|another|extra|additional)"
    rf"(?:\s+(?:one|ones|{_MEDIA}))?"
    rf"(?:\s+of\s+(?:it|them|those|these|that|him|her|the\s+same))?$", re.I)


def more_request(cleaned: str) -> tuple[bool, int | None]:
    """Is this "some more of what we were just looking at", and how many?

    Returns (False, None) for anything that names a subject of its own, so only
    a bare follow-up inherits the previous one.
    """
    q = re.sub(r"\s+", " ", (cleaned or "").strip().strip(" .?!,"))
    m = _MORE.match(q)
    if not m:
        return False, None
    return True, _count_of(m.group(1))


def clean_search_query(text: str) -> str:
    """"search the web for the best mini pc" -> "the best mini pc"; questions and
    already-clean keyword queries pass through untouched."""
    q = strip_restart(text)
    q = _TRAIL.sub("", q.strip().strip(" .?!"))
    q = _HEY.sub("", q)
    if _QUESTION.match(q):
        return q
    q = _POLITE.sub("", q)
    q = _SEARCH_LEAD.sub("", q, count=1)
    q = _VERB_ME.sub("", q)
    q = q.strip(" .?!,")
    return q if len(q) >= 3 else text.strip()


# --- video ------------------------------------------------------------------
# The same job the image cleaner does, for things to watch. He asked for "a You
# Tube video of someone playing Iron Man PS3" and YouTube was searched for
# "someone playing iron man ps3" — his sentence, not his subject. The words that
# describe the KIND of video are not part of what he is looking for.
_VIDEO_NOUN = r"(?:video|clip|trailer|gameplay|playthrough|walkthrough|footage|montage)s?"
_SERVICE = r"(?:youtube|you\s?tube|netflix|spotify)"
_VIDEO_LEAD = re.compile(
    rf"^(?:show|find|get|pull\s+up|bring\s+up|play|put\s+on|watch|search)\s+"
    rf"(?:me\s+)?(?:a|an|some|the)?\s*(?:{_SERVICE}\s+)?(?:{_VIDEO_NOUN}\s+)?"
    r"(?:of|for|about|by|with|from)?\s*", re.I)
# "someone playing X" is HOW he described it; the subject is X, and keeping the
# filler put "someone" in the search box. Only PLAYING becomes "gameplay" — the
# other verbs carry meaning and have to survive, or "a guy building a pc" turns
# into "pc gameplay", which is a different video entirely.
_SOMEONE_DOING = re.compile(
    r"^(?:some(?:one|body)|a\s+(?:guy|girl|man|woman|person|kid)|people)\s+"
    r"(playing|doing|building|making|reviewing|explaining|repairing|cooking)\s+(.+)$",
    re.I)
_BARE_SERVICE = re.compile(rf"^{_SERVICE}\s+", re.I)
_TRAIL_SERVICE = re.compile(rf"\s+(?:on|in|from)\s+{_SERVICE}\s*$", re.I)
_LEAD_ARTICLE = re.compile(r"^(?:a|an|the)\s+", re.I)


def clean_video_query(text: str) -> str:
    """"find me a you tube video of someone playing iron man ps3"
    -> "iron man ps3 gameplay".

    Conservative in the same way as the image cleaner: it only strips a lead verb
    that reads as a command, and if it would leave nothing it gives the original
    back rather than searching for an empty string.
    """
    q = _TRAIL.sub("", (text or "").strip().strip(" .?!"))
    q = _HEY.sub("", q)
    q = _POLITE.sub("", q)
    q = _TRAIL_SERVICE.sub("", q)
    q = _VIDEO_LEAD.sub("", q, count=1)
    q = _BARE_SERVICE.sub("", q, count=1)      # "youtube lofi beats"
    m = _SOMEONE_DOING.match(q.strip())
    if m:
        verb, subject = m.group(1).lower(), m.group(2).strip()
        # "someone playing X" is X gameplay; everything else keeps its verb,
        # because the verb IS the subject there ("building a pc").
        q = (f"{_LEAD_ARTICLE.sub('', subject)} gameplay" if verb == "playing"
             else f"{verb} {subject}")
    q = _LEAD_ARTICLE.sub("", q).strip(" .?!,")
    return q if len(q) >= 2 else (text or "").strip()
