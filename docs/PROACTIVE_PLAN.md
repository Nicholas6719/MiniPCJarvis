# Proactive JARVIS — design plan

Agreed with Nicholas 2026-08-30. Three features, built in this order. Work-with-me
mode is explicitly deferred until the first two are solid.

The governing idea: **JARVIS is always working, awake or asleep, and where he
reaches Nicholas follows where Nicholas IS.**

- **At the PC, working with him:** he may speak up. Interrupting a spreadsheet to
  say "there is breaking news" is welcome when it is urgent — that is what being
  present means. (Corrected 2026-08-30: an earlier draft sent everything to
  Telegram unconditionally, which was wrong.)
- **Away from the PC, or not working together:** Telegram, always. He must never
  wake a dark screen or speak into an empty room.
- **On request, either way:** "send it to my phone" / "send it through Telegram" /
  "send it to me" hands the thing off to Telegram. "Give me the article" opens it
  in the browser. Whatever he has just told Nicholas about is the subject — the
  handoff needs no repetition of what it refers to.

---

## Phase 1 — Idle, sleep, and summon

**What it should feel like.** He withdraws when he is not needed and arrives when
called. The wake word stops being a microphone trigger and becomes a summons.

**Behaviour**

- After `presence.idle_sleep_minutes` (default **2**) with no turn, no speech and
  no work in flight, he minimises himself and enters sleep mode.
- Sleep mode is his working shift, not an off switch — night school, research and
  the proactive watch all run there.
- On the wake word, from minimised or from behind whatever is focused: restore,
  **take the foreground**, and answer.

**What already exists.** Sleep mode, minimise on sleep, restore on wake, and the
wake word staying live while asleep — all covered by `tests/sleep_e2e.py`.

**What is new**

1. An idle timer that enters sleep by itself. "Idle" must mean *JARVIS* is idle,
   not the machine: Nicholas working in Excel for an hour is exactly when he
   should be asleep. Guards: never sleep mid-turn, mid-tool, mid-dictation, or
   while a confirmation is pending.
2. Reliable foreground restore. Windows deliberately resists foreground stealing
   (`SetForegroundWindow` fails for a process that is not already foreground).
   The known-good approaches are `AttachThreadInput` to the foreground thread, or
   a brief `HWND_TOPMOST` toggle. **This must be tested against a real focused
   app (Excel, a browser), not assumed** — it is the whole point of the feature.

**Gate.** Extend `sleep_e2e.py`: he sleeps on his own after the timer; the wake
word restores AND focuses him while another window holds focus; he does not sleep
while a turn, tool or confirmation is in flight.

---

## Phase 2 — The proactive engine

The plumbing that lets him reach out, with the judgement to do it rarely.

**Delivery rule.** Presence decides the channel. Present at the PC -> he says it
out loud, and may interrupt to do so at Alert tier and above. Away -> Telegram.
He must never wake a dark screen or speak into an empty room. (Presence detection
already exists from keyboard/mouse activity; the camera idea is much later and
not required for this.)

**The four tiers.** The whole design lives or dies on the interrupt bar. An
assistant that reports everything is one you stop reading inside a day.

| Tier | What it is | When it arrives |
| --- | --- | --- |
| **Brief** | The scheduled digest | Fixed times only (below) |
| **Notable** | Worth knowing today, not worth interrupting | Rolled into the next brief |
| **Alert** | Breaking national news; a large market move | Immediately, **including quiet hours** |
| **Urgent** | Life-safety or an extraordinary market event | Immediately + escalation |

**Schedule (Eastern).** Pre-market **07:30**, midday **12:30**, close **16:15**,
evening wrap **20:00**. Quiet hours suppress *briefs* only — Alerts and Urgent
always go out, because "there is breaking national news at 3am and I want to
know" was explicit.

**Escalation, and an honest problem.** The requirement is: if an Urgent message
goes unanswered for five minutes, call him. **Telegram bots cannot place calls** —
the Bot API has no such method; calls are user-to-user. Options, in the order I
would pick them:

1. **Pushover** (~$5 one-off, per platform). Built for exactly this: "emergency"
   priority repeats until acknowledged and bypasses silent mode. Small, no phone
   number, no per-message cost. *Recommended.*
2. **Twilio voice call.** Actually rings the phone, ~1.3¢/min, needs an account
   and a number. The only option that is literally a call.
3. **Escalating Telegram pings.** Free, no new dependency, but cannot bypass a
   silenced phone — which is the case that matters at 3am.

Whichever is chosen, the acknowledgement mechanism is the same: an Urgent message
carries a button, and the escalation is cancelled the moment he taps it or
replies.

**Gates.** Tier classification is unit-tested against fixture stories. Delivery is
tested through `/debug/telegram`. Quiet-hours behaviour is tested both ways: a
brief is held, an alert is not.

---

## Phase 3 — Market analysis worth reading

The part Nicholas asked to be thought through properly. His words: *"I don't want
him analyzing 5,000+ stocks. I want him expertly analyzing those stocks... 'here
are the top eight stocks experts are talking about today', or 'Nvidia is up right
now, so you shouldn't buy in'. He needs to be proactive to me, not just give me a
sum of data."*

So the unit of work is **a judgement about a small number of stocks**, not a
sweep of the market.

**Why not scan everything.** ~5,000 US listings against a 60-calls-per-minute key
is 80+ minutes per pass, and it answers the wrong question anyway. Nobody wants a
ranked list of 5,000 things. The interesting signal is *what the market is
talking about*, which is a much smaller set and is exactly what the news carries.

**The pipeline, per brief**

1. **Listen for the conversation.** Search the news for what the market is
   discussing — upgrades, downgrades, price targets, earnings, halts, guidance.
   Count which companies recur across independent outlets. Frequency across
   *sources* is the signal; a single article is not.
2. **Shortlist** the top ~10 by that count, plus anything from his own list
   (below) that moved sharply.
3. **Enrich** each with Finnhub: live quote and day move, analyst consensus
   (buy/hold/sell counts), and its recent company news. ~3 calls per name — about
   30 calls per brief, comfortably inside the rate limit.
4. **Judge, per name.** This is where the LLM earns its place. Given the numbers
   and what is being said, produce a *stance*, not a summary: has the move already
   happened, does the analyst view agree with the price action, is this news or
   noise. The output shape is deliberately opinionated —
   *"NVIDIA, up 6% on an upgrade; 64 of 68 analysts already say buy. The move has
   happened — chasing it here is paying for yesterday's news."*
5. **Rank and cut.** Eight names maximum, fewer when there is little to say. A
   brief that says "quiet day, nothing worth acting on" is a good brief.

**Guardrails, written into the prompt and the tests**

- Every claim is attributed to what was actually fetched. No numbers from model
  memory — this is the fabrication failure `research_e2e.py` already guards.
- Stance, not advice: what is happening and how it reads, not "buy this".
- Say when a source is thin. "Two outlets mention it" is honest; silence is not.

**Overnight.** Futures and index ETFs, plus any large after-hours move in a name
he follows. This is where "huge stock changes overnight at 2 in the morning"
lands, as an Alert.

**His holdings AND the wider market — both, not either.** He will supply a list of
tickers he owns or follows. That list does NOT narrow what he hears about: the
news-driven pass above still surfaces whatever experts are discussing, owned or
not. In his words: *"I do want him to be able to say, hey, experts are saying buy
into Nike stock even though I don't own it."*

So the list does two things and no more:
  * a lower alert bar for names he holds — a 5% move in his own position is worth
    interrupting for; the same move in a name he has never mentioned is not
  * a line in every brief on how his positions are doing, whether or not they are
    in the news

---

## Local and national news

- **Local:** Massachusetts, weighted to Middlesex and specifically **Framingham,
  Sudbury, Marlborough, Maynard, Natick**. Whole-state coverage preferred, those
  five prioritised. Google News search feeds per town and for the state, deduped
  against each other and against the national feed.
- **National:** judged on SIGNIFICANCE, not distance. A gas leak at an Ohio
  facility is worth knowing; a road closure in Ohio is not. Breaking and serious
  goes at Alert tier; the rest waits for a brief.
- Local rides a lower bar — a road closure in Natick earns a line in the morning
  brief precisely because it is his road.

---

## Phase 4 (later) — Work-with-me mode

Deferred by agreement. The shape, recorded so it is not lost:

- JARVIS collapses to the arc-reactor orb alone, docked bottom-left, always on
  top, small and out of the way. "Minimise yourself for a second" dismisses it.
- **He acts in Nicholas's context, not his own.** "Show me an image of Iron Man"
  while a browser is focused opens it *in that browser* — he does not restore
  himself fullscreen to answer. This is the hard half and the reason the mode is
  worth having.

---

## Built so far (2026-08-30)

- **Phase 1 — DONE.** He minimises and sleeps after two quiet minutes; his name
  restores him to the front. `presence.idle_sleep_minutes`, gated in `sleep_e2e`.
  UNVERIFIED: whether the foreground grab really beats a focused Excel window.
  Windows resists it and the ALT-key nudge is a workaround, not a guarantee —
  Nicholas has been asked to try it by hand.
- **Phase 2 — DONE.** `delivery.py` (where), `significance.py` (whether),
  `briefing.py` (when), `lastseen.py` + `tools/handoff.py` (what "it" means).
  Gates: test_delivery, test_significance, test_briefing, test_handoff.

Five things only showed up by looking at real output, and are worth remembering:

1. His 07:30 brief would NEVER have fired — the proactive quiet window ends at
   08:00. Briefing keeps its own (22:00-07:00). Two of his own decisions
   conflicted and nothing but the gate noticed.
2. The watch fired on startup and treated the whole feed as breaking, so every
   restart would have dumped the day at him. It PRIMES on the first pass now.
3. Nothing older than `alert_max_age_minutes` (180) can raise an alert.
4. One Framingham killing filled a brief four times, four outlets, four
   wordings, almost no shared words. Same town + a death on the same day now
   collapses to one story.
5. "Killing" and "deadly" were not recognised as deaths at all, so those same
   headlines did not register as fatalities. Fixed without matching "deadline".

`/debug/brief` composes a brief (or runs the watch) WITHOUT sending anything.
Build against that, not against his phone.

## Order of work

1. Phase 1 — idle sleep and summon. Smallest, most visible, mostly exists.
2. Phase 2 — the engine, the tiers, Telegram delivery, quiet-hours rules.
3. Phase 3 — market analysis; the largest single piece.
4. Phase 4 — escalation channel, once Nicholas has chosen the mechanism.
5. Later — work-with-me mode.

## Decisions needed from Nicholas

1. ~~Escalation channel~~ **DECIDED: escalating Telegram pings.** His phone is
   never silenced, so the one weakness of that option does not apply to him. No
   new dependency, no cost. An Urgent message carries an acknowledge button;
   unacknowledged, it repeats — and the repeats stop the moment he taps or replies.
2. **A personal ticker list** — offered and explained; he may supply symbols later.
   Not a blocker: news-driven selection works without one.
3. ~~Brief times~~ **DECIDED: 07:30 / 12:30 / 16:15 / 20:00 Eastern.**
