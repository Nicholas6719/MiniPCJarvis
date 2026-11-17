# The Brain

JARVIS answers most requests without asking the language model. The part that
makes that possible is called **the brain**: a small, learned intent router that
sits between speech-to-text and the LLM. This document explains what it is, how
it works, and how to rebuild it in another assistant. It is written so that
someone who has never opened this repository can follow it.

> **Reading this for Nova?** Nova routes with a first-match-wins chain of regex
> intent modules and has no equivalent of this. Jump to
> [Porting the brain to Nova](#porting-the-brain-to-nova) after the first three
> sections.

## Contents

1. [What it is, in one page](#what-it-is-in-one-page)
2. [Where it sits in a turn](#where-it-sits-in-a-turn)
3. [The five ideas](#the-five-ideas)
4. [A skill, end to end](#a-skill-end-to-end)
5. [How a sentence is classified](#how-a-sentence-is-classified)
6. [What happens when it is not sure](#what-happens-when-it-is-not-sure)
7. [How it learns](#how-it-learns)
8. [The rest of the brain package](#the-rest-of-the-brain-package)
9. [Storage](#storage)
10. [Tests that keep it honest](#tests-that-keep-it-honest)
11. [Numbers](#numbers)
12. [Porting the brain to Nova](#porting-the-brain-to-nova)
13. [Mistakes already made, so you need not repeat them](#mistakes-already-made-so-you-need-not-repeat-them)

---

## What it is, in one page

The brain is a **nearest-neighbour classifier over example sentences**.

- Every request JARVIS knows how to handle is a **skill**: a name, the tool it
  runs, a handful of example phrasings ("seeds"), a function that pulls the
  arguments out of the sentence, and a function that turns the tool's result
  into a spoken sentence.
- At start-up every example phrasing is turned into a vector with a small
  embedding model (`BAAI/bge-small-en-v1.5`, about 10 ms per sentence on a CPU).
- When he speaks, the new sentence is embedded the same way and compared with
  every stored example. The closest example names the skill.
- If the match is confident **and** the skill can extract its arguments, JARVIS
  runs the tool directly and speaks a template. No LLM is involved. This is a
  **reflex**.
- If the match is weak, the sentence goes to the LLM as before.
- When the LLM then solves the request with exactly one known tool, the
  sentence is saved as a new example for that skill. Next time it is a reflex.

That is the whole design. The value is in the details below, almost all of
which exist because a specific real sentence went wrong.

**Why bother.** A reflex answers in a few milliseconds. The local model takes
about 5 seconds to start answering a tool request on this hardware, 12 at the
slow end. Over 45 days, 229 of 284 real turns were reflexes. A reflex is also
deterministic: it cannot invent a fact, promise something it will not do, or
pick the wrong tool on a whim.

## Where it sits in a turn

`sidecar/orchestrator.py`, `_converse()`. In order, first match wins:

```
speech -> text
  1. an open "did you mean X?" question          -> this may be its answer
  2. an open clarifying question                 -> this may be its answer
  3. the request is two requests                 -> one short question
  4. "say that again"                            -> repeat the last reply
  5. a phrase he taught ("lights out")           -> run its steps      brain.match_command
  6. THE BRAIN                                   -> a reflex            brain.decide
  7. a stray word / the television               -> dismissed in silence
  8. it nearly knew                              -> "Did you mean ...?" brain.unsure
  9. a stored, verified, timeless fact           -> answered from the fact store
 10. the LLM, with tools                         -> _llm_with_tools
       ...and afterwards: learn from what the LLM did              brain.learn
```

Steps 5, 6, 8, 9 and the learning at the end are the brain. Everything else is
the orchestrator.

## The five ideas

**1. Examples, not rules.** A skill is defined by sentences a person would say,
not by a regular expression that tries to anticipate them. Adding coverage means
adding a sentence.

**2. Canonical form.** Before embedding, a sentence is normalised so the vector
encodes the *intent* and not the *object*. "Remind me in 10 minutes to stretch"
and "remind me in 3 hours to call mom" both become `remind me at TIME to TASK`.
Without this the embedder treats every city, number and company as a different
sentence and you need thousands of seeds.

**3. Guards.** A skill's argument extractor may return `None`, meaning "this
looks like mine but I cannot act on it". The brain then offers the turn to the
runner-up skill instead of giving up. Guards are how two skills with similar
wording stay apart.

**4. Honest uncertainty.** Three outcomes, not two: confident (act), nearly
(ask "did you mean ...?"), no idea (LLM). A "no" to the question is remembered
so the same wrong guess is never offered for that sentence again.

**5. Learn only what could be executed.** The LLM's choices are lessons only
when the brain's own skill could have run that exact sentence. Everything else
the LLM does is ignored.

## A skill, end to end

All skills live in `sidecar/brain/skills.py`. The dataclass:

```python
@dataclass
class Skill:
    name: str
    tool: str | None                 # registry tool to run (None = no tool, e.g. a greeting)
    seeds: list[str]                 # example phrasings
    slots: Callable[[str], dict | None] = lambda t: {}       # sentence -> tool arguments, or None to refuse
    speak: Callable[[dict, dict], str] = lambda s, r: "Done." # (arguments, tool result) -> spoken sentence
    fixed_args: dict = field(default_factory=dict)            # arguments that never vary
    llm_after: bool = False          # run the tool, then let the LLM compose the answer
    direct_if: Callable[[str], bool] | None = None            # an llm_after skill may still answer directly
    speak_first: bool = False        # say "Opening X." before running the tool
```

The simplest kind has no arguments at all:

```python
Skill("scanner_rules", "_scanner", [
    "when will you notify me about calls", "how do scanner alerts work",
    "what scanner calls do you send me", "when do scanner calls make a sound"],
    fixed_args={"ask": "rules"}, speak=say_scanner),
```

One with arguments:

```python
Skill("reminder", "set_reminder", [
    "set a timer for ten minutes", "remind me in 10 minutes to stretch",
    "set a reminder for 5 pm to call mom", "remind me at 9 to take my meds"],
    slots=slots_reminder, speak=say_reminder),
```

`slots_reminder("remind me in 10 minutes to stretch")` returns
`{"minutes_from_now": 10, "text": "stretch"}`. Given "remind me about that thing" it
returns `None`, and the turn moves on.

One with no tool, only a reply:

```python
Skill("company", None, ["i'm bored", "i'm so tired", "i can't sleep", "rough day"],
      slots=slots_greeting, speak=say_company),
```

Three registries are built from the list: `SKILLS`, `SKILL_BY_NAME`, and
`TOOL_TO_SKILL` (tool name -> skill, only where exactly one skill owns the tool;
this is what lets the brain learn from the LLM's tool calls).

## How a sentence is classified

`sidecar/brain/router.py`, `Brain.classify()` and `Brain.decide()`.

```
text
 -> _ticker_to_company()   "what's AAPL at" -> "what's apple at"   (capitals are the only signal, so first)
 -> _light()               lowercase, strip "hey jarvis", strip "please / can you", fix "you tube"
 -> _CANON rules           first matching pattern rewrites objects to placeholders
 -> embed (bge-small, L2-normalised, cached per sentence)
 -> sims = example_matrix @ query           cosine similarity to every stored example
 -> + context nudges       what is on screen argues for or against some skills
 -> + lexical nudges       a few skills get a small push from a telling word
 -> + follow-up pull       "what about now" leans toward the skill he just used
 -> best skill = top example's skill
 -> confidence = top similarity, cut when the best example of a DIFFERENT skill is close
```

The confidence rule, exactly:

```python
margin = top - rival                       # rival = best example of another skill
confidence = top if margin >= 0.06 else top - (0.06 - margin) * 3.0
```

So a sentence that sits between two skills is never confident, however close it
is to both.

`decide()` then applies the thresholds (all in config under `brain`):

| Setting | Default | Meaning |
| --- | --- | --- |
| `threshold` | 0.82 | at or above: act, if the slots extract |
| `ask_threshold` | 0.66 | the raw top similarity at or above this, but below `threshold`: keep it as "nearly knew" |
| `general_hint_threshold` | 0.70 | how sure it must be that a sentence is a plain knowledge question |

```python
for _ in range(3):                         # bounded: a chain of refusals still ends at the LLM
    name, conf = await self.classify(text, exclude=refused, context=context)
    if not name or conf < threshold:
        ...record self.unsure if it nearly knew...
        return None
    slots = SKILL_BY_NAME[name].slots(light)
    if slots is not None:
        return skill, {**skill.fixed_args, **slots}, conf
    refused.add(name)                      # the guard said "not mine": try the runner-up
return None
```

**The `general` skill** is a real skill with seeds like "how many legs does a
spider have" and "write me a toast". It has no tool. When it wins, `classify`
returns no skill, and `general_level()` tells the orchestrator how firmly to
stop the LLM from reaching for web search on a question it should answer from
its own head.

**Context** (`_skill_context_delta`) nudges, never filters: `+0.15` when the
screen state fits a skill, `-0.20` when it does not. "Make it bigger" means the
3D model when one is on stage and the interface when none is. Nothing is ever
removed for want of context, so he can still ask about a project with none open
and be told so.

## What happens when it is not sure

- **Nearly knew** (`brain.unsure`): the orchestrator asks one short question,
  "Did you mean check the scanner is working, sir?", using the skill's label
  from `_LABELS`. A "yes" runs it and stores the sentence as a `user` example,
  the strongest kind. A "no" calls `brain.reject(text, skill)`.
- **Rejections persist** (`brain_rejections`, keyed on sentence *and* skill).
  Being asked the same wrong question tomorrow is the same annoyance as being
  asked it twice in a row.
- **Corrections**: "no, I meant ..." after a reflex un-learns the example that
  caused it (`brain.unlearn`) if that example was learned rather than seeded.
- **No idea**: the LLM takes the turn. This is the right outcome for anything
  new, and it is how new examples are found.

## How it learns

Three sources, in descending trust (`source` column):

| Source | Where it comes from | Checked how |
| --- | --- | --- |
| `seed` | written by hand in `skills.py` | ground truth; a learned row that contradicts a seed is deleted at load |
| `user` | he said yes to "did you mean", or taught it by voice | stored as is |
| `learned` | the LLM resolved a turn with exactly one tool that maps to a skill | every rule below |

`Brain.learn()` refuses a `learned` example when:

- the sentence is longer than 14 words;
- it is only an answer ("yes", "no thanks") - `_ANSWER_ONLY`. "Yes." was once
  learned as "render again" because the model's next tool call happened to be
  one;
- it contains a make verb but the skill does not create anything - a wrong tool
  choice by the model is not a lesson;
- **the skill's own slot extractor cannot run that sentence** (`_executable`).
  This is the main safety bar: the brain only learns what it could have done
  itself;
- the brain was already fairly sure it was a plain question (`general` at 0.7
  or more) - the tool use was the model's whim.

At every start `load()` re-validates the table: seeds that changed are dropped
and re-embedded, learned rows that contradict a seed or can no longer be
executed are deleted.

**Taught commands** are separate (`brain_commands`). "When I say lights out,
mute the volume and lock the PC" stores a phrase and a list of steps. Matching
is near-exact only (cosine 0.92 or more), because a taught phrase runs several
actions.

**Night school** (`night_school.py`) widens routing while he sleeps: for turns
the LLM handled with a tool, it asks the LLM offline for paraphrases and teaches
the router only the ones the slot extractor can execute.

## The rest of the brain package

| File | What it is |
| --- | --- |
| `router.py` | the classifier, learning, rejections, taught commands (this document) |
| `skills.py` | every skill: seeds, slot extractors, speak templates, plus gates like `not_for_me`, `stray_followup`, `addressed_to_me` |
| `facts.py` | the fact store: timeless, web-verified facts answered in ~0.3 s with their sources kept ("how do you know?") |
| `night_school.py` | sleep-time jobs: fact audit, overnight curiosity, paraphrase distillation |
| `mathskill.py`, `units.py` | arithmetic and unit conversion done in code, never by the model |
| `persona.py`, `wit.py` | greetings and stock lines in his voice, so a reflex still sounds like JARVIS |
| `protocols.py` | named routines ("lockdown protocol") on top of taught commands |
| `overheard.py` | offering to act on something said to someone else |

The roadmap and its reasoning ("three realms": timeless facts from the store,
changeable facts always from the live web, generation from the LLM) are in
`docs/BRAIN_ROADMAP.md`.

## Storage

One SQLite file, three tables:

```sql
brain_examples   (id, ts, text UNIQUE, skill, source, embedding BLOB)   -- text is the CANONICAL form
brain_rejections (text, skill, ts, PRIMARY KEY (text, skill))
brain_commands   (id, ts, phrase UNIQUE, steps JSON, embedding BLOB)
```

Embeddings are float32, L2-normalised, 384 wide, stored as raw bytes. At load
they are stacked into one in-memory matrix, so classification is a single
matrix-vector product.

## Tests that keep it honest

| Test | What it proves |
| --- | --- |
| `tests/seed_collisions.py` | no two skills own the same canonical seed. Canonicalisation can silently turn a seed into another skill's sentence; `load()` resolves that with "first listed wins" and says nothing, so this gate is the loud failure |
| `tests/test_intent.py` | several hundred real sentences each land on the named skill (or on none). Every sentence he said that went wrong is added here with the date |
| `tests/test_brain.py` | thresholds, guards handing over to the runner-up, learning rules, rejections surviving a restart |
| `tests/test_facts.py` | what may enter the fact store and what may be served from it |
| `tests/check_names.py` | static check that catches a typo inside a `try/except` that would otherwise fail silently |

The working rule: **a sentence he actually said that misrouted becomes a test
case before it is fixed.**

## Numbers

- 142 skills, about 1,760 seeds, about 1,580 canonical examples after
  normalisation.
- Embedding: ~10 ms per sentence, single-threaded on purpose (it leaves the CPU
  to the model server). Nearest-neighbour search: negligible.
- Reflex first response: median 3 ms. LLM with tools: median 5.3 s, p90 11.8 s.
  (45 days of real turns, `turn_stats` table.)
- 229 of 284 real turns in that period were reflexes.

---

## Porting the brain to Nova

Nova's backend (`nova_backend/`) is Python too, so most of this ports directly.
What differs is the platform (macOS, MLX instead of llama.cpp) and the shape of
the existing routing.

### What Nova has today

`nova.py` runs a first-match-wins chain: system commands, calendar follow-up,
pending file question, calendar intents, file intents, memory intents,
fast-path intents, tool intents, RAG, then the LLM. Each stage is a regex
module (`calendar_intents.py`, `file_intents.py`, `market_intents.py`,
`weather_intents.py`, `actuation_intents.py`). `confidence.py` gates on how
sure the *transcription* was, scaled by consequence.

That works, and its weaknesses are the ones the brain was built to remove:

- order decides ties, so adding an intent can break an earlier one;
- coverage is whatever the regex author thought of;
- there is no "nearly knew": a miss falls straight through to the LLM;
- nothing is learned from the turns the LLM handles.

### The mapping

| JARVIS | Nova equivalent | Notes |
| --- | --- | --- |
| `brain/router.py` | new `nova_backend/brain.py` | copy the design; ~400 lines without the JARVIS-specific context and canon rules |
| `brain/skills.py` | new `nova_backend/skills.py` | one `Skill` per existing intent |
| `Skill.slots` | the *extraction* half of each `*_intents.py` | the regexes that pull out a date, a filename, a ticker are still exactly what is needed. Keep them; drop the detection half |
| `Skill.speak` | the reply templates in those modules | |
| tool registry (`tools/registry.py`) | `tools.py` | Nova's tools are plain functions. A skill needs a name to call; a dict of `{name: function}` is enough |
| `fastembed` bge-small | the same, or reuse Nova's embedder | fastembed runs on Apple Silicon. Nova already ships ChromaDB for RAG, so an embedding model is present; one model for both is cleaner |
| SQLite `brain_examples` | a table in the database `memory.py` already opens | |
| `brain.threshold` etc. | `config.json` | |
| `confidence.py` | keep it | it answers a different question (was the *transcript* right?). The brain answers "which intent is this?". Use both: act only when both are satisfied, and let consequence raise the bar as it does now |

### A minimal port, in order

1. **Define `Skill`** and write one skill per current intent. Seeds: take the
   phrases in `tests/adversarial_phrases.txt` and the routing corpus
   (`tests/test_routing_corpus.py`) that Nova already has. That corpus is the
   hard part and it already exists.
2. **Write `_light()` and `_norm()`.** Start with only lowercasing, wake-word
   stripping and number/time placeholders. Add a canon rule only when a real
   sentence needs it.
3. **Write `Brain.load()`, `classify()`, `decide()`.** Copy the confidence and
   margin rule exactly; the constants were tuned on real speech.
4. **Insert it into `nova.py` as one stage**, after the pending-question stages
   (2 and 2b) and system commands, in place of stages 3 to 7. Keep the old
   stages behind it at first as a fallback, and log every turn where the two
   disagree. Remove a regex stage only when the brain has matched it on the
   corpus.
5. **Add the seed-collision test and an intent test** before adding learning.
6. **Add `learn()`** last, with the "only what the slot extractor could
   execute" rule from day one.
7. **Then** "did you mean" and rejections. Nova has no transcript on screen
   (see `confidence.py`), so a spoken clarifying question matters more there
   than it does here.

### What not to bring across

- The JARVIS-specific context sets (`_STAGE_SKILLS`, `_OFFICE_SKILLS`, ...) and
  most of `_CANON`. They encode this product's features.
- The fact store and night school. They are a separate project; the router is
  useful without them.
- `mathskill.py` is worth taking as is if Nova lacks one (the old Swift
  `MathRouter` was removed).

### Platform notes

- Nova's LLM is MLX with a carried KV cache. A reflex bypasses the LLM
  entirely, so it does not disturb that cache.
- Run the embedding on one thread. It is 10 ms either way, and the model server
  needs the cores more.
- JARVIS's brain is `async` because the sidecar is FastAPI. Nova's pipeline is
  threaded; a synchronous `classify()` is fine and simpler.

## Mistakes already made, so you need not repeat them

Each of these cost a day or more here. They are recorded in the code comments
next to the fix, and at length in `docs/HANDOFF.md`.

1. **An uncommitted write held SQLite's only write lock for the life of the
   process.** `load()` deleted stale rows and committed only inside a later
   `if`. Every other writer got "database is locked" forever; the reminder
   scheduler could not mark a reminder as sent and re-sent it about 2,600
   times overnight. Commit every write immediately, and never hold a
   transaction across an `await` or a thread hop.
2. **Stored rows are canonical; validators must know it.** `learn()` validated
   the raw sentence and stored the canonical one ("ping me in N minutes"). On
   the next start the validator saw `N`, could not parse a number, and deleted
   every confirmed example. Substitute stand-in values for placeholders before
   re-validating.
3. **A canon rule can move a seed into another skill.** Adding "no more for
   now" to the sleep skill made it own the canonical form of every correction
   ("no, I meant ..."), so corrections put him to sleep at confidence 1.00.
   The seed-collision test exists because of this.
4. **The model's tool choice is not ground truth.** "Yes." learned as a render
   command; a "render me X" learned as "show the thing already on screen".
   Learn only what the skill could execute, never bare answers.
5. **A near-tie used to become silence.** The runner-up was computed, used to
   shrink the confidence, and thrown away. Keep it: it is the question to ask.
6. **A guard is not an outcome.** A skill refusing a sentence is only correct
   if the sentence then lands somewhere sensible. Test where a sentence *ends
   up*, not that a skill declined it.
7. **Words the embedder cannot see.** Tickers (NVDA, AAPL) are unrelated rare
   tokens to an embedding model; seeding one teaches nothing about the next.
   Rewrite them to something it knows before embedding.
8. **Speech-to-text spelling.** "You Tube" as two words missed every pattern
   for "youtube". Normalise the recogniser's habits in `_light()`.
9. **`np.frombuffer` is read-only.** The in-memory matrix is written when a
   phrase is re-taught. Copy it at load.
