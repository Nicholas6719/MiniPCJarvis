"""Semantic end-of-turn: decide whether the sentence is FINISHED, not just quiet.

A fixed silence timer is wrong in both directions. Pause to think mid-sentence
and it cuts you off; finish a sentence cleanly and it still makes you wait out
the full window before anything happens.

The field's answer (LiveKit, Pipecat) is a small transformer that reads the
partial transcript and predicts completion. We get most of that for free: we
already run Parakeet at ~140 ms and a brain that scores utterances in ~45 ms. So
when silence begins we transcribe what has been said so far and choose a budget:

  dangling ("what's the weather in ...", "and then")   -> wait longer
  a command the brain recognises, or a complete clause -> cut almost immediately
  anything unclear                                     -> the old fixed window

Nothing here can end a turn early on its own — it only moves the deadline the
capture loop already enforces, and every failure falls back to that window.
"""
from __future__ import annotations

import logging
import re

from config import config

log = logging.getLogger("jarvis.endpoint")

# Budgets in seconds of trailing silence.
FAST = 0.40        # clearly finished
NORMAL = 0.90      # unchanged default
PATIENT = 1.90     # clearly mid-thought — let them finish

# A trailing word that means more is coming. Conjunctions, prepositions,
# articles, auxiliaries and hesitations: no English sentence ends here.
_DANGLING = re.compile(
    r"\b(?:and|but|or|so|because|since|although|while|if|unless|until|whether|"
    r"the|a|an|my|your|our|their|his|her|its|this|that|these|those|"
    r"to|for|with|without|about|from|into|onto|over|under|between|at|by|on|in|of|"
    r"is|are|was|were|am|be|been|being|do|does|did|can|could|will|would|shall|"
    r"should|may|might|must|going|want|need|"
    r"um|uh|erm|hmm|well|like|let|then|maybe|actually|just)\s*$", re.I)
# Deliberately NOT dangling, though they look like function words: pronouns end
# perfectly good sentences ("what time is IT", "read IT", "call HIM") and so do
# politeness words ("...in detail PLEASE"). Listing them cost 1.9 s of dead air
# on some of the most common utterances there are.

# Fixed phrases that are grammatically complete yet obviously unfinished
# requests. No trailing-word rule catches these: "can you" ends in a pronoun,
# "how do i" in a subject.
_STEM_ONLY = re.compile(
    r"^\s*(?:"
    r"(?:can|could|would|will|should|do|does|did)\s+(?:you|i|we)|"
    r"(?:how|what|where|when|why|which)\s+(?:do|does|did|can|could|should|would)\s+(?:i|we|you)|"
    r"(?:i|we)\s+(?:want|need|would like)|"
    r"(?:please|hey|ok|okay)"
    r")\s*[.?!]?\s*$", re.I)

# ...and a trailing comma or dash is the same signal in punctuation.
_OPEN_PUNCT = re.compile(r"[,;:\-–—]\s*$")

# A finished question or statement, by shape.
_TERMINAL = re.compile(r"[.?!]\s*$")
_QUESTION_STEM = re.compile(r"^\s*(?:what|who|when|where|why|which|how|is|are|do|does|"
                            r"did|can|could|would|should|will|tell|show|give|find|"
                            r"open|close|play|set|remind|search|look|read)\b", re.I)


def budget_for(text: str, brain_hit: bool) -> tuple[float, str]:
    """(seconds of silence to require, why) for a partial transcript."""
    t = (text or "").strip()
    if not t:
        return NORMAL, "nothing heard yet"
    # The brain recognising the whole utterance is the strongest signal there is
    # that it is finished — it outranks any surface cue.
    if brain_hit:
        return FAST, "a command the brain recognises"
    if _OPEN_PUNCT.search(t) or _DANGLING.search(t) or _STEM_ONLY.match(t):
        return PATIENT, "trails off mid-thought"
    if _TERMINAL.search(t):
        return FAST, "ends on a full stop"
    words = t.split()
    # A question that opened with a question word and has a subject after it is
    # almost always finished ("what time is it", "how tall is the eiffel tower").
    if _QUESTION_STEM.match(t) and len(words) >= 3:
        return FAST, "a complete question"
    if len(words) >= 8:
        return FAST, "long enough to be a whole thought"
    return NORMAL, "unclear"


async def decide(audio, stt, brain) -> tuple[float, str, str]:
    """Transcribe the utterance so far and pick the silence budget.

    Returns (seconds, why, text). Never raises: on any failure the caller keeps
    the fixed window it would have used anyway.
    """
    if not config.get("wake", "semantic_endpoint", default=True):
        return NORMAL, "disabled", ""
    try:
        text = (await stt.transcribe(audio) or "").strip()
    except Exception:
        log.debug("endpoint transcribe failed", exc_info=True)
        return NORMAL, "transcribe failed", ""
    brain_hit = False
    try:
        if text and config.get("brain", "enabled", default=True):
            brain_hit = await brain.decide(text) is not None
    except Exception:
        brain_hit = False
    secs, why = budget_for(text, brain_hit)
    return secs, why, text
