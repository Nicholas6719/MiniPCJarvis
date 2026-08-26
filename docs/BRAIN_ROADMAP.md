# The Brain Beyond the LLM — agreed design (discussed 2026-08-26, NOT yet built)

Goal: JARVIS answers mainly from his own brain — fast AND accurate — with the LLM
as a last resort instead of a default. Realistic target: 70–80% of turns never
touch the LLM, and LLM turns get faster via tiering. "No LLM ever" is not the
goal; generation and personality need a model.

## The three realms (Nicholas's rule)

| Realm | What | Answered by | Example |
| --- | --- | --- | --- |
| 1. Timeless facts | Can never change | Brain fact store, ~0.3 s, provenance kept | "How tall is the Eiffel Tower" |
| 2. Changeable facts | Can change | LIVE WEB every time; LLM only *voices* the result in persona | "What's the newest Spider-Man movie" |
| 3. Generation | Conversation, synthesis, creative | LLM (later: small-model tier for easy turns) | "Write me a toast" |

Hard rules:
- Realm 2 is NEVER answered from the brain and NEVER from the LLM's memory —
  the LLM's training data is just another stale cache. `_NEEDS_LIVE_WEB` is the
  seed of this; it generalizes into first-class realm selection.
- A fast wrong answer is worse than a slow right one. When unsure which realm:
  treat as realm 2.

## Who decides a fact is timeless — three lines of defense
1. Trigger words force realm 2: latest / newest / current / today / price /
   best / "who is the [title]" ... (machinery exists).
2. Hand-written safe categories from us (heights, historical dates, math,
   definitions) + the LLM classifying the rest at temp 0: "still true in ten
   years?"
3. THE NIGHTLY AUDIT (the empirical backstop — catches what 1+2 got wrong,
   e.g. "Jupiter's moons" which everyone would call timeless but changes).

## The nightly fact audit (agreed: possible, wanted)
- Runs in sleep mode, inside quiet hours (22:00–08:00), machine idle; about
  twice a week; per-night budget (~30–50 facts, oldest-verified first) so the
  GPU/fans don't run all night. Pauses instantly if the user wakes anything.
- Per fact: re-fetch its ORIGINAL sources first (provenance is stored for this).
  Source still agrees → stamp re-verified. Source changed/vanished → fresh
  search, then LLM at temp 0 compares stored fact vs fresh extracts:
  same / changed / unclear.
- Verdicts: confirmed → freshness stamp. changed → demote to realm 2 NOW +
  flag in morning report. unclear → retry next audit, two strikes → demote.
  Demotion is the default posture.

## Learning sources (agreed boundaries)
- From the LLM ("distillation"): ONLY immutable verified facts — must come from
  a sourced research answer (not LLM memory), pass the timeless classifier, and
  survive audits. Also: nightly paraphrase generation to widen brain ROUTING
  (not facts) from real usage.
- From JARVIS's OWN conversation transcript: YES — fair game for deciding which
  topics to pre-fetch/pre-learn during idle hours.
- From the user's Brave browsing history: NEVER. Not negotiable.
- Open web crawling: no. Learning is anchored to what the user actually asks.

## Sequence when we build (each stage gated like everything else)
1. Instrument: a week of real usage telling us which turns burn LLM time.
2. Q→A cache + paraphrase distillation (cheapest; reuses existing machinery).
3. Fact store with provenance + realm classifier + nightly audit.
4. Idle-hours pre-fetch keyed to transcript topics.
5. Small-model tier for easy realm-3 turns (brain picks the tier).
6. Keep growing plain reflexes (see feature-gap list in HANDOFF.md).

## Resolved (Nicholas, 2026-08-26)
- Provenance in speech: NEVER mention sources unless asked. Receipts stay
  inspectable ("how do you know that?" → he cites source + verified date).
  Same principle for the overnight audit: log findings in Settings → History,
  no unprompted morning announcements — he answers if asked.
- Footprint: approved. Claude's assessment: ~10 GB RAM headroom today
  (20B model ≈ 12 GB + app ≈ 21/31 GB used); a 2–4 GB small model fits
  comfortably and should run ~4–5× faster than the 20B on the 780M
  (bandwidth-bound). Verify with a bakeoff (scripts/model_trial.ps1 pattern)
  before locking the choice — measure, don't assume.
