"""Night school — what the brain does while JARVIS sleeps (docs/BRAIN_ROADMAP.md).

Three jobs, all inside the quiet-hours window, all silent (findings go to the
activity feed / Settings -> History, never spoken — the user's rule):

1. THE FACT AUDIT: re-check stored facts against their ORIGINAL sources; the
   LLM at temp 0 compares. SAME -> re-stamp. CHANGED -> demote to realm 2 now.
   UNCLEAR -> strike (two strikes demote). Demotion is the default posture.
2. OVERNIGHT CURIOSITY: recent questions he answered WITHOUT sources (pure LLM
   memory) get researched properly against the live web; the normal fact intake
   then decides if a verified timeless fact was born. Anchored to the user's own
   conversation — never his browser history, never open crawling.
3. PARAPHRASE DISTILLATION: for turns where the brain routed a tool via the LLM
   path, ask the LLM (offline) for paraphrases and teach the ROUTER the ones its
   slot extractors can actually execute. Widens routing, not facts.

Runs at most every AUDIT_GAP_H hours, only while SLEEPING inside quiet hours
(or via the JARVIS_DEBUG endpoint). Aborts between items the moment he wakes.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time

from brain.facts import facts
from brain.router import brain
from config import config
from events import bus

log = logging.getLogger("jarvis.nightschool")

AUDIT_GAP_H = 24          # nightly (was 72: the router learned from the day's
                          # model-routed turns twice a week; now every night)
FACT_BUDGET = 40          # per night
CURIOSITY_BUDGET = 5      # researched questions per night
DISTILL_BUDGET = 8        # new routing examples per night

_COMPARE_PROMPT = (
    "A stored fact is being re-verified.\nQuestion: {q}\nStored answer: {a}\n\n"
    "Fresh text from the original source:\n{extract}\n\n"
    "Does the fresh text still support the stored answer? Reply exactly one "
    "word: SAME if it clearly supports it, CHANGED if it clearly contradicts or "
    "supersedes it, UNCLEAR otherwise.")

_PARA_PROMPT = (
    'Give {n} short, natural, different ways a person might say this to a voice '
    'assistant: "{u}"\nOne per line, no numbering, no quotes.')

_SYNTH_PROMPT = (
    "Question: {q}\n\nSources:\n{extracts}\n\n"
    "Answer the question in ONE short spoken sentence using ONLY the sources. "
    "If the sources do not clearly answer it, reply exactly: UNKNOWN")


class NightSchool:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.last_report: dict = {}
        self._fetch = None
        self._compare = None
        facts.db.execute("CREATE TABLE IF NOT EXISTS night_meta "
                         "(key TEXT PRIMARY KEY, value TEXT)")
        facts.db.commit()

    # ---------------------------------------------------------------- schedule
    def start(self, orchestrator) -> None:
        self._orch = orchestrator
        self._task = asyncio.create_task(self._loop())

    def _last_run_ts(self) -> float:
        row = facts.db.execute(
            "SELECT value FROM night_meta WHERE key='last_run'").fetchone()
        return float(row[0]) if row else 0.0

    def _mark_run(self) -> None:
        facts.db.execute("INSERT OR REPLACE INTO night_meta (key, value) VALUES "
                         "('last_run', ?)", (str(time.time()),))
        facts.db.commit()

    def _in_quiet_hours(self) -> bool:
        try:
            start = dt.time.fromisoformat(config.get("proactive", "quiet_start", default="22:00"))
            end = dt.time.fromisoformat(config.get("proactive", "quiet_end", default="08:00"))
        except ValueError:
            return False
        now = dt.datetime.now().time()
        return (now >= start or now <= end) if start > end else (start <= now <= end)

    def _asleep(self) -> bool:
        try:
            return self._orch.sm.state.value == "sleeping"
        except Exception:
            return False

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(1800)   # check every 30 min
            try:
                if not config.get("facts", "night_school", default=True):
                    continue
                if not (self._asleep() and self._in_quiet_hours()):
                    continue
                if time.time() - self._last_run_ts() < AUDIT_GAP_H * 3600:
                    continue
                await self.run()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("night school run failed")

    # ---------------------------------------------------------------- the run
    async def run(self, force: bool = False, fetch=None, compare=None) -> dict:
        """One full night-school pass. Aborts between items if he wakes
        (unless force, which the debug endpoint uses). fetch/compare are
        injectable so the audit logic is testable offline."""
        self._fetch, self._compare = fetch, compare
        report = {"started": time.time(), "audited": 0, "confirmed": 0,
                  "changed": 0, "unclear": 0, "curiosity": 0, "learned": 0,
                  "aborted": False}

        def awake() -> bool:
            return not force and not self._asleep()

        # -- job 1: the fact audit ------------------------------------------
        for f in facts.due_for_audit(FACT_BUDGET):
            if awake():
                report["aborted"] = True
                break
            verdict = await self._audit_one(f)
            report["audited"] += 1
            report[verdict] += 1
            await bus.emit("fact_audit", question=f["question"], verdict=verdict)
            await asyncio.sleep(2)      # gentle on the machine and the web

        # -- job 2: overnight curiosity -------------------------------------
        if not report["aborted"]:
            report["curiosity"] = await self._curiosity(awake)

        # -- job 3: paraphrase distillation ----------------------------------
        if not report["aborted"]:
            report["learned"] = await self._distill(awake)

        self._mark_run()
        report["finished"] = time.time()
        self.last_report = report
        await bus.emit("night_school", **report)
        log.info("night school: %s", report)
        return report

    async def _audit_one(self, f: dict) -> str:
        """Re-fetch the fact's original source and let temp-0 judge it."""
        fetch = self._fetch or self._fetch_default
        compare = self._compare or self._compare_default
        extract = ""
        for s in f["sources"]:
            try:
                extract = await fetch(s["url"])
            except Exception:
                extract = ""
            if extract:
                break
        if not extract:
            return "unclear" if facts.strike(f["id"]) < 2 else "changed"
        v = await compare(f["question"], f["answer"], extract)
        if v == "SAME":
            facts.mark_verified(f["id"])
            return "confirmed"
        if v == "CHANGED":
            facts.demote(f["id"], "audit: source changed")
            return "changed"
        return "unclear" if facts.strike(f["id"]) < 2 else "changed"

    @staticmethod
    async def _fetch_default(url: str) -> str:
        from tools.web_tools import fetch_page
        page = await fetch_page(url, max_chars=2500)
        return page.get("content") or ""

    @staticmethod
    async def _compare_default(q: str, a: str, extract: str) -> str:
        out = ""
        try:
            from llm.provider import local_llm
            async for ch in local_llm.stream(
                    [{"role": "user", "content": _COMPARE_PROMPT.format(
                        q=q, a=a, extract=extract[:2200])}],
                    max_tokens=600, sampling={"temperature": 0.0}):
                out += ch.text
                if ch.done:
                    break
        except Exception:
            log.exception("audit compare failed")
            return "UNCLEAR"
        words = re.findall(r"\b(SAME|CHANGED|UNCLEAR)\b", out.upper())
        return words[-1] if words else "UNCLEAR"

    async def _curiosity(self, awake) -> int:
        """Research recent LLM-memory answers so verified facts replace them.
        Candidates: general-knowledge questions from HIS OWN transcript that have
        no fact-store entry. The normal intake gates what actually sticks."""
        from memory.store import memory
        from tools.web_tools import research
        rows = memory.db.execute(
            "SELECT content FROM transcript WHERE role='user' "
            "ORDER BY id DESC LIMIT 120").fetchall()
        seen: set[str] = set()
        done = 0
        for (text,) in rows:
            if done >= CURIOSITY_BUDGET or awake():
                break
            t = text.strip().lower()
            # questions only, no commands, no realm-2, nothing already known
            if not re.match(r"^(?:what|who|when|where|how|why|which)\b", t):
                continue
            if len(t) < 12 or t in seen:
                continue
            seen.add(t)
            from brain.facts import REALM2
            if REALM2.search(t):
                continue
            d = await brain.decide(text)
            if d is not None:
                continue                      # a command, not a question
            if await facts.lookup(text):
                continue                      # already known
            try:
                facts.reset_evidence()
                res = await research(t, num_sources=3)
                srcs = [{"url": s["url"], "title": s.get("title", "")}
                        for s in (res.get("sources") or []) if s.get("extract")]
                extracts = "\n---\n".join(
                    s.get("extract", "")[:900] for s in (res.get("sources") or [])
                    if s.get("extract"))[:2600]
                if not (srcs and extracts):
                    continue
                # synthesize a SPOKEN answer from the sources — a stored fact is a
                # sentence he can say, never raw page prose
                answer = await self._synthesize(t, extracts)
                if answer and await facts.consider(t, answer, srcs, "research"):
                    done += 1
            except Exception:
                log.exception("curiosity research failed for %r", t[:60])
            finally:
                facts.reset_evidence()
            await asyncio.sleep(5)
        return done

    async def _synthesize(self, q: str, extracts: str) -> str:
        out = ""
        try:
            from llm.provider import local_llm
            async for ch in local_llm.stream(
                    [{"role": "user", "content": _SYNTH_PROMPT.format(q=q, extracts=extracts)}],
                    max_tokens=600, sampling={"temperature": 0.0}):
                out += ch.text
                if ch.done:
                    break
        except Exception:
            log.exception("curiosity synthesis failed")
            return ""
        ans = out.strip().splitlines()[-1].strip() if out.strip() else ""
        if not ans or "UNKNOWN" in ans.upper() or len(ans) > 300:
            return ""
        return ans

    async def _distill(self, awake) -> int:
        """Widen ROUTING: paraphrase recent tool_then_llm utterances and teach
        the router the ones whose slot extractors actually execute."""
        from memory.store import memory
        rows = memory.db.execute(
            "SELECT DISTINCT skill FROM turn_stats WHERE path='tool_then_llm' "
            "AND skill IS NOT NULL AND ts > ? LIMIT 6",
            (time.time() - 3 * 86400,)).fetchall()
        utter_rows = memory.db.execute(
            "SELECT content FROM transcript WHERE role='user' ORDER BY id DESC LIMIT 60").fetchall()
        learned = 0
        for (skill_name,) in rows:
            if learned >= DISTILL_BUDGET or awake():
                break
            # find a recent utterance that routes to this skill
            sample = None
            for (u,) in utter_rows:
                d = await brain.decide(u)
                if d and d[0].name == skill_name:
                    sample = u
                    break
            if not sample:
                continue
            out = ""
            try:
                from llm.provider import local_llm
                async for ch in local_llm.stream(
                        [{"role": "user", "content": _PARA_PROMPT.format(n=3, u=sample)}],
                        max_tokens=400, sampling={"temperature": 0.7}):
                    out += ch.text
                    if ch.done:
                        break
            except Exception:
                continue
            for line in out.splitlines():
                p = line.strip().strip('-"• ').lower()
                if not (6 < len(p) < 90) or learned >= DISTILL_BUDGET:
                    continue
                if await brain.decide(p) is not None:
                    continue     # the router already gets this phrasing
                # brain.learn applies the full safety bar itself: the skill's
                # slots must execute the phrasing, the general-question guard,
                # dedupe, and the 14-word cap. Seeds always override learned rows.
                try:
                    if await brain.learn(p, skill_name, source="night_school"):
                        learned += 1
                        await bus.emit("brain_learned", text=p, skill=skill_name,
                                       source="night_school")
                except Exception:
                    log.exception("distill learn failed")
            await asyncio.sleep(2)
        return learned


night_school = NightSchool()
