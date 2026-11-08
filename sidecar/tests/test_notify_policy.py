"""What JARVIS sends him unprompted, and what may make his phone SOUND.

2026-11-07, his words: *"I am mainly ignoring his notifications, which is not
what I built him for."* The evidence is in the fixtures at the bottom of this
file, copied from his own database: every news decision (`news_judged`) and
every delivery (`deliveries`) from 10-27 to 11-06 - 98 messages, 96 of them to
his phone and all of them with a sound. Ten URGENT news pings, none urgent or
local. Friday's close six times over a weekend. "Take my supplements at 7 PM;
wear my retainers at 9 PM" recited every morning. Memory warnings about his
other apps. Reminders rephrased into "lace up your retainers" and "put on
your.".

This gate replays all of it against the new policy and holds each rule:
  * news: local + current + dangerous, or it never pings (deterministic gate,
    then a model check that can only veto);
  * Telegram: silent by default; URGENT and his own reminders sound; a daily
    budget on the sounding ones (reminders excluded);
  * briefs: market only on NYSE trading days and only when it changed; no
    brief with nothing new; no recital of recurring reminders; the scanner
    desk's line if that desk exists;
  * memory: once a day at most, silently, only when JARVIS himself is slowed;
  * reminders: his words verbatim, a Done button, one nudge (it sounds), a record.

Offline and hermetic: no network, no model, no live Telegram. The gate database
is shared with every other gate and outlives the build, so what this file adds
it takes back (delivery rows are never written; task and reminder rows are
deleted at the end); the brief memory and state files are scratch copies.
Run: python tests/test_notify_policy.py
"""
import asyncio
import datetime as dt
import logging
import os
import pathlib
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "notify.db"))

fails = []
SCRATCH = pathlib.Path(tempfile.mkdtemp())


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


LOCAL_DESKS = ("WHDH", "MassLive", "WCVB Boston", "WCVB", "Boston.com", "MetroWest Daily News")


def story_of(row):
    when, stage, tier, why, source, head, text = row
    st = {"headline": head, "summary": text, "source": source}
    if source in LOCAL_DESKS:
        st["_local_feed"] = True
    return st


# ============================================================================ news
def replay_news() -> None:
    import significance as S
    S.local_only = lambda: True
    S.emergencies_only = lambda: True
    print("\n-- every news decision of 10-27..11-06, replayed --")
    check("all 28 news_judged rows are here", len(NEWS_JUDGED) == 28, len(NEWS_JUDGED))
    old_urgent = [r for r in NEWS_JUDGED if r[2] == "urgent"]
    check("...of which 16 were judged URGENT then", len(old_urgent) == 16, len(old_urgent))
    for row in NEWS_JUDGED:
        st = story_of(row)
        new, why = S.classify_news(st)
        ok, gate_why, _facts = S.urgent_gate(st)
        check(f"[{row[0]} was {row[2]}] never sounds now: {row[5][:46]!r}",
              new != S.URGENT and not ok, f"{new}: {why}")
        check(f"...and it is never an ALERT: {row[5][:40]!r}", new != S.ALERT, new)
        check(f"...and the gate says why: {row[5][:40]!r}", bool(gate_why), gate_why)
        # a deterministic gate rejects the junk ON ITS OWN - no model involved
    # The reasons, for the ones he named.
    why_of = {r[5]: S.urgent_gate(story_of(r))[1] for r in NEWS_JUDGED}
    expect = {
        "Western Mass. wildfire reaches 70% containment Tuesday": "no town",
        "Healey activates national guard to battle western MA wildfire": "no town",
        "RMV employee's bribe income exceeded her government salary, AGO says": "bribe",
        "Dear Annie: Is there a polite way to tell a co-worker she's become the office outbreak source?": "dear annie",
        "Taco Bell recovers after cyclospora outbreak: ‘Continuing to play offense’": "recovers",
        "Millions of dollars from the worst US gas leak are now available to help residents electrify homes": "dollars",
        "Political analysts predict ‘blue wave,' trouble for Trump": "analysts",
        "Suspicious election mail case: Man accused of sending powder, threats": "election",
        "Why you should’t panic about governments urging emergency kits (but what should be in yours)": "why you should",
        "Gavin Newsom just made one of his biggest moves yet on California’s death penalty": ("sentenc", "somewhere else"),
        "Shooting after Alabama football game leaves 1 dead, 8 injured": "national",
        "Dozens injured, at least 6 dead in Halloween weekend gun violence at nightlife districts across US": "national",
        "Closing statements expected today in Matthew Farwell trial": ("closing statements", "trial"),
        "3 defense witnesses testify in Farwell trial, closing arguments expected later in the week": "testif",
        "New Veterans Home in Holyoke opens after deadly 2020 COVID-19 outbreak": "veterans",
        "Country music’s most devastating year: These legendary singers have died in 2026": "devastating year",
        "Trooper charged in connection with trainee death appears in court": ("charged", "court"),
        "2 Mass. residents killed in NH crash": "somewhere else",
    }
    for head, words in expect.items():
        words = (words,) if isinstance(words, str) else words
        got = why_of.get(head, "(missing)")
        check(f"refused for the right reason ({words[0]}): {head[:40]!r}",
              any(w in got.lower() for w in words), got)
    # The Gloucester rows: never his state, however the wire carried them.
    for row in NEWS_JUDGED:
        if "Gloucester" in row[5]:
            st = story_of(row)
            check(f"Gloucester, England is not his state: {row[5][:40]!r}",
                  not S.in_his_state(st) and not S.is_local(st)[0] and not S.places(st),
                  (S.in_his_state(st), S.is_local(st), S.places(st)))
    # The wildfire and the trial were real Massachusetts news: the brief, not the phone.
    wild = story_of(NEWS_JUDGED[1])
    check("a real Massachusetts wildfire eighty miles off waits for the brief",
          S.classify_news(wild)[0] == S.NOTABLE, S.classify_news(wild))


# ====================================================================== briefs
class Recorder:
    def __init__(self):
        self.sent = []

    async def deliver(self, text, tier="notable", *, key="", subject="", written="", **kw):
        self.sent.append({"text": text, "written": written, "key": key, "tier": tier, "subject": subject})
        return {"delivered": "recorded"}


TITLES = ("Weather", "Today", "Yesterday", "Markets", "Yours", "Worth a look", "The story",
          "Experts", "Ahead", "News", "Also")


def parse_brief(text: str) -> dict:
    """'Weather: ... Today: ... Markets: ...' -> {title: body}."""
    import re
    pat = re.compile(r"(?:^|(?<=\. ))(" + "|".join(re.escape(t) for t in TITLES) + r"): ")
    out, marks = {}, list(pat.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[m.group(1)] = text[m.end():end].strip().rstrip(".")
    return out


def replay_briefs() -> list[dict]:
    """Every brief slot of 10-27..11-06 through the REAL _maybe_brief, with the
    content each one actually had (from the ledger) as its raw material. What
    each would carry now, and whether it would be sent at all."""
    import briefing as br
    import tasks.scheduler as TS
    rec = Recorder()
    real = (br.delivery, br._now, TS.scheduler.list_pending)
    br.delivery = rec
    mem_path, state_path = SCRATCH / "brief_memory.json", SCRATCH / "briefing_state.json"
    real_paths = (br.Briefing._memory_path, br.Briefing._state_path)
    br.Briefing._memory_path = staticmethod(lambda: mem_path)
    br.Briefing._state_path = staticmethod(lambda: state_path)
    # his two daily reminders, as the scheduler lists them
    TS.scheduler.list_pending = lambda: [
        {"id": 278, "due": "2099-01-01 19:00", "text": "take my supplements", "recurrence": "daily"},
        {"id": 170, "due": "2099-01-01 21:00", "text": "wear my retainers", "recurrence": "daily"}]
    results = []
    try:
        b = br.Briefing()
        b._held = []
        for rid, when, tier, outcome, subject, text in DELIVERIES:
            if not subject.startswith("brief:"):
                continue
            at = dt.datetime.strptime(when, "%Y-%m-%d %H:%M")
            slot = subject.split(" ")[-1]
            parts = parse_brief(text)

            async def fake_sections(final=False, first=False, market=True, parts=parts, at=at, slot=slot,
                                    cut=len(text) >= 295):
                secs = []
                if "Weather" in parts:          # the morning's, or a held weather statement
                    secs.append(("Weather", [(parts["Weather"], parts["Weather"])]))
                if first:
                    today = b._today_lines()
                    if today:
                        secs.append(("Today", today))
                    if "Yesterday" in parts:
                        secs.append(("Yesterday", [(parts["Yesterday"], parts["Yesterday"])]))
                if market:
                    for t in ("Markets", "Yours", "Worth a look"):
                        if parts.get(t):
                            secs.append((t, [(parts[t], parts[t])]))
                    if "Markets" not in parts and cut:
                        # the ledger kept 300 characters and the weather
                        # statement filled them (10-29 16:15); the market was there
                        line = f"(the ledger cut this market section off) {at:%Y-%m-%d %H:%M}"
                        secs.append(("Markets", [(line, line)]))
                if final:       # the night brief always carried the day's news
                    line = f"the news of {at:%A %d %B}"
                    secs.append(("News", [(line, line)]))
                return secs
            b._sections = fake_sections
            b._done_slots = set()
            br._now = lambda at=at: at
            before = len(rec.sent)
            asyncio.run(b._maybe_brief())
            sent = rec.sent[-1] if len(rec.sent) > before else None
            results.append({"id": rid, "when": when, "slot": slot, "old": text, "sent": sent})
    finally:
        br.delivery, br._now, TS.scheduler.list_pending = real
        br.Briefing._memory_path, br.Briefing._state_path = real_paths
    return results


def test_briefs() -> list[dict]:
    import briefing as br
    import market_calendar as mc
    print("\n-- the briefs of 10-27..11-06, replayed --")
    res = replay_briefs()
    check("44 brief slots in the ledger", len(res) == 44, len(res))
    sent = [r for r in res if r["sent"]]
    dropped = [r for r in res if not r["sent"]]
    weekend_mid = [r for r in res if dt.datetime.strptime(r["when"], "%Y-%m-%d %H:%M").weekday() >= 5
                   and r["slot"] in ("12:30", "16:15")]
    check("the weekend's midday and afternoon briefs are not sent (nothing new)",
          all(not r["sent"] for r in weekend_mid) and len(weekend_mid) == 4, [r["when"] for r in weekend_mid])
    check("...and every other brief still is", len(dropped) == 4, [r["when"] for r in dropped])
    check("the 10-29 close is told at 16:15, so the 20:00 brief does not repeat it",
          not any("Markets:" in r["sent"]["text"] for r in sent if r["when"] == "2026-10-29 20:00"))
    fri_close = [r for r in sent if "down 1.77%" in (r["sent"]["text"] or "")]
    old_close = [r for r in res if "down 1.77%" in r["old"]]
    check("Friday's close (S&P down 1.77%) is told ONCE, not on Saturday and Sunday",
          len(fri_close) == 1 and fri_close[0]["when"].startswith("2026-10-30 16:"),
          [r["when"] for r in fri_close])
    check(f"...where it went out {len(old_close)} times before", len(old_close) >= 6, len(old_close))
    for r in sent:
        d = dt.datetime.strptime(r["when"], "%Y-%m-%d %H:%M")
        has_market = "Markets:" in r["sent"]["text"]
        if not mc.is_trading_day(d):
            check(f"no market on a weekend: {r['when']}", not has_market, r["sent"]["text"][:80])
        if r["slot"] == "20:00":
            check(f"the 20:00 brief does not repeat 16:15's market: {r['when']}", not has_market,
                  r["sent"]["text"][:80])
        check(f"no recital of his recurring reminders: {r['when']}",
              "supplements" not in r["sent"]["text"] and "retainers" not in r["sent"]["text"],
              r["sent"]["text"][:120])
    weekday_1615 = [r for r in sent if r["slot"] == "16:15"
                    and mc.is_trading_day(dt.datetime.strptime(r["when"], "%Y-%m-%d %H:%M"))]
    check("a trading day's close is still told, at 16:15",
          weekday_1615 and all("Markets:" in r["sent"]["text"] for r in weekday_1615),
          [r["when"] for r in weekday_1615])
    import notify_policy as NP
    check("a brief arrives silently", not NP.audible("brief", NP.kind_of("", "brief:2026-11-06 20:00")))
    check("...and keeps its 'brief:<stamp>' key in the ledger (self_report and 'what did I miss' read it)",
          all(r["sent"]["key"].startswith("brief:") and not r["sent"]["subject"] for r in sent),
          [(r["sent"]["key"], r["sent"]["subject"]) for r in sent][:2])

    # --- holidays and the calendar -----------------------------------------------
    b = br.Briefing()
    mem = SCRATCH / "brief_memory_cal.json"
    b._memory_path = lambda: mem
    check("no market on Thanksgiving 2026", not b._market_due(dt.datetime(2026, 11, 26, 12, 30)))
    check("...nor on the observed Fourth, Friday 2026-07-03", not b._market_due(dt.datetime(2026, 7, 3, 16, 15)))
    check("...nor on Good Friday 2027", not b._market_due(dt.datetime(2027, 3, 26, 12, 30)))
    check("...but on an ordinary Tuesday, yes", b._market_due(dt.datetime(2026, 11, 10, 12, 30)))
    b._remember_brief(market_key="closed:2026-11-10")
    check("a close already told is not told again that evening",
          not b._market_due(dt.datetime(2026, 11, 10, 20, 0)))
    check("...or the next morning before the open", not b._market_due(dt.datetime(2026, 11, 11, 7, 30)))
    check("...but the next session is news", b._market_due(dt.datetime(2026, 11, 11, 12, 30)))
    b._remember_brief(market_key="open:2026-11-11",
                      market_fp=b._market_fingerprint([("Markets", [("x", "the S&P 500 +0.51%")])]))
    same = [("Markets", [("x", "the S&P 500 +0.52%")]), ("News", [("n", "n")])]
    moved = [("Markets", [("x", "the S&P 500 +1.40%")]), ("News", [("n", "n")])]
    check("mid-session, a cent of drift is not a change",
          [t for t, _l in b._drop_unchanged_market(same, dt.datetime(2026, 11, 11, 13, 0))] == ["News"])
    check("...a real move is", "Markets" in [t for t, _l in b._drop_unchanged_market(moved, dt.datetime(2026, 11, 11, 13, 0))])

    # --- one-off reminders stay in the morning; recurring ones do not --------------
    import tasks.scheduler as TS
    real_pending, real_now = TS.scheduler.list_pending, br._now
    br._now = lambda: dt.datetime(2026, 11, 9, 7, 30)
    TS.scheduler.list_pending = lambda: [
        {"id": 1, "due": "2026-11-09 16:00", "text": "dentist", "recurrence": "none"},
        {"id": 2, "due": "2026-11-09 19:00", "text": "take my supplements", "recurrence": "daily"},
        {"id": 3, "due": "2026-11-09 21:00", "text": "wear my retainers", "recurrence": "daily"}]
    try:
        import tools.phone as _ph
        real_today = _ph.today_lines
        _ph.today_lines = lambda: []
        today = b._today_lines()
        _ph.today_lines = real_today
    finally:
        TS.scheduler.list_pending, br._now = real_pending, real_now
    check("the morning lists the dentist, not the supplements or the retainers",
          today == [("dentist at 4 PM", "4 PM - dentist")], today)

    # --- the scanner desk: optional, and never able to break a brief ---------------
    print("\n-- the scanner desk's line, if the desk exists --")
    b2 = br.Briefing()
    saved = sys.modules.pop("scanner_desk", None)
    try:
        sys.modules["scanner_desk"] = None          # "import scanner_desk" raises ImportError
        check("no scanner desk yet: no section, no error", asyncio.run(b2._scanner_lines(0.0)) == [])
        fake = types.ModuleType("scanner_desk")
        seen = {}

        def brief_line(since_ts):
            seen["since"] = since_ts
            return "Overnight on the scanner: a brush fire in Sudbury, out by 2 a.m."
        fake.brief_line = brief_line
        sys.modules["scanner_desk"] = fake
        got = asyncio.run(b2._scanner_lines(1234.0))
        check("a desk with a line puts it in the morning brief",
              got == [("Overnight on the scanner: a brush fire in Sudbury, out by 2 a.m.",) * 2] and seen.get("since") == 1234.0,
              (got, seen))

        async def async_line(since_ts):
            return "Quiet night on the scanner."
        fake.brief_line = async_line
        check("...an async one too", asyncio.run(b2._scanner_lines(0.0)) == [("Quiet night on the scanner.",) * 2])

        def boom(since_ts):
            raise RuntimeError("radio unplugged")
        fake.brief_line = boom
        check("a desk that raises costs nothing", asyncio.run(b2._scanner_lines(0.0)) == [])
        fake.brief_line = lambda since_ts: {"not": "a line"}
        check("...nor one that returns nonsense", asyncio.run(b2._scanner_lines(0.0)) == [])
        fake.brief_line = lambda since_ts: ""
        check("...nor one with nothing to say", asyncio.run(b2._scanner_lines(0.0)) == [])
        del fake.brief_line
        check("...nor one without brief_line at all", asyncio.run(b2._scanner_lines(0.0)) == [])
    finally:
        sys.modules.pop("scanner_desk", None)
        if saved is not None:
            sys.modules["scanner_desk"] = saved
    src = (pathlib.Path(br.__file__)).read_text(encoding="utf-8")
    check("the morning brief asks for it", 'out.append(("Scanner", heard))' in src and "if first:" in src)

    # --- held news rides in the very next brief --------------------------------------
    b3 = br.Briefing()
    b3._held = [{"kind": "news", "text": "Brush fire near Route 20 in Sudbury", "why": "for the brief: ..."}]
    out = asyncio.run(b3._news_sections([], final=False))
    check("what was held from his phone rides in the next brief, whatever the slot",
          out and out[0][0] == "Also" and "Sudbury" in out[0][1][0][1], out)
    return res


# ===================================================================== telegram
class FakeConfig:
    def __init__(self, chat=111):
        self.data = {"remote": {"telegram_chat_id": chat}}

    def get(self, *keys, default=None):
        cur = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def set(self, *keys, value):
        cur = self.data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value


def bridge():
    import remote_telegram as rt
    rt.config = FakeConfig()
    b = rt.TelegramBridge()
    b.token = "test:token"
    calls = []

    async def fake_api(method, http_timeout=30, _token=None, **params):
        calls.append((method, params))
        return {"message_id": 7}
    b._api = fake_api
    return b, calls


def test_telegram() -> None:
    import remote_telegram as rt
    print("\n-- Telegram: silent unless it has earned a sound --")
    spawned = []
    real_spawn = rt.spawn
    rt.spawn = lambda coro, name=None: (spawned.append(name), coro.close())
    try:
        b, calls = bridge()
        asyncio.run(b.send_proactive("Markets: the S&P 500 up 0.3%.", tier="brief", silent=True))
        check("a silent message carries disable_notification",
              calls and calls[-1][1].get("disable_notification") is True, calls[-1:])
        calls.clear()
        asyncio.run(b.send_proactive("Time for your retainers, sir.", tier="alert", silent=False,
                                     buttons=[("Done", "done:42")]))
        p = calls[-1][1] if calls else {}
        check("a reminder sounds (no disable_notification)", "disable_notification" not in p, p)
        check("...and carries a Done button for this occurrence",
              p.get("reply_markup") == {"inline_keyboard": [[{"text": "Done", "callback_data": "done:42"}]]}, p)
        calls.clear()
        asyncio.run(b.send_proactive("Gas leak on Main Street in Natick.", tier="urgent", silent=False))
        check("an urgent sounds and is chased", "disable_notification" not in calls[-1][1]
              and spawned == ["tg-urgent-chase"], (calls[-1:], spawned))
        calls.clear()
        spawned.clear()
        asyncio.run(b.send_proactive("Gas leak on Main Street in Natick.", tier="urgent", silent=True))
        check("an urgent past the day's sound budget arrives silently and is not chased",
              calls[-1][1].get("disable_notification") is True and spawned == [], (calls[-1:], spawned))
    finally:
        rt.spawn = real_spawn
    src = pathlib.Path(rt.__file__).read_text(encoding="utf-8")
    check("photos honour it too", 'data["disable_notification"] = "true"' in src)

    # --- the chase spends the sound budget and stops when it is gone ---------------
    import delivery as D
    real_sleep, real_left, real_has = asyncio.sleep, D.delivery.sound_budget_left, D.delivery.has_budget
    real_quiet = D._in_quiet_hours

    async def no_sleep(_s):
        return None
    try:
        rt.asyncio.sleep = no_sleep
        D._in_quiet_hours = lambda: False           # by day: three chases
        D.delivery.has_budget = lambda tier="alert": True
        for left, want in ((0, 0), (5, 3)):
            b, calls = bridge()
            b._urgent["tok"] = {"text": "Gas leak.", "at": time.time(), "sent": 1}
            D.delivery.sound_budget_left = lambda left=left: left
            n0 = len(D.delivery._sounded)
            asyncio.run(b._chase("tok"))
            chased = [c for c in calls if "Still unanswered" in str(c[1].get("text"))]
            check(f"with {left} sounds left today the chase sends {want}", len(chased) == want, len(chased))
            if want:
                check("...and each chase is counted as a sound", len(D.delivery._sounded) - n0 == want,
                      len(D.delivery._sounded) - n0)
            del D.delivery._sounded[n0:]
    finally:
        rt.asyncio.sleep = real_sleep
        D._in_quiet_hours = real_quiet
        D.delivery.sound_budget_left, D.delivery.has_budget = real_left, real_has


# ===================================================================== delivery
class FakeTG:
    def __init__(self):
        self.sent = []

    async def send_proactive(self, text, tier="notable", subject="", silent=False, buttons=None):
        self.sent.append({"text": text, "tier": tier, "subject": subject, "silent": silent, "buttons": buttons})
        return True

    async def _upload(self, *a, **k):
        return False


def fresh_delivery():
    import delivery as D
    d = D.Delivery()
    d.orchestrator = None
    d._ledger_loaded = True
    d._sounded_loaded = True
    return d


def test_budget() -> None:
    import delivery as D
    import notify_policy as NP
    print("\n-- the daily budget of sounding pings --")
    tg = FakeTG()
    real = (D.is_present, D.telegram_available, D._in_quiet_hours, D._remember_proactive,
            sys.modules.get("remote_telegram"), NP.daily_sound_cap)
    D.is_present = lambda: False
    D.telegram_available = lambda: True
    D._in_quiet_hours = lambda: False
    D._remember_proactive = lambda text: None
    mod = types.ModuleType("remote_telegram")
    mod.telegram = tg
    sys.modules["remote_telegram"] = mod
    NP.daily_sound_cap = lambda: 3
    logs = []

    class Grab(logging.Handler):
        def emit(self, rec):
            logs.append(rec.getMessage())
    h = Grab()
    logging.getLogger("jarvis.delivery").addHandler(h)
    try:
        d = fresh_delivery()

        async def day():
            for i in range(5):
                await d.deliver(f"Gas leak number {i} on Main Street in Natick.", D.URGENT, key=f"news:leak {i}")
            # exactly as main wires them: the KEY says what it is, and the
            # ledger keeps "task:278" / "memory:<day>" / "nudge:9" as before
            await d.deliver("Time for your supplements, sir.", D.ALERT, key="task:278",
                            buttons=[("Done", "done:9")])
            await d.deliver("Tesla is down 5.57% at 376.29 dollars.", D.ALERT, key="move:TSLA:2026-11-04")
            await d.deliver("Markets: the S&P 500 up 0.3%.", D.BRIEF, key="brief:2026-11-04 16:15")
            await d.deliver("Memory is at 96 percent.", D.ALERT, key="memory:2026-11-04")
            await d.deliver("Still to do, sir: take your supplements.", D.ALERT, key="nudge:9")
        asyncio.run(day())
        urgent = [m for m in tg.sent if m["tier"] == D.URGENT]
        check("the first three urgents of the day sound", [m["silent"] for m in urgent[:3]] == [False] * 3,
              [m["silent"] for m in urgent])
        check("...the rest arrive silently, not dropped", len(urgent) == 5 and all(m["silent"] for m in urgent[3:]),
              [m["silent"] for m in urgent])
        check("...and the log says so", any("sounding budget spent" in m for m in logs), logs[-3:])
        rem = [m for m in tg.sent if m["text"].startswith("Time for your supplements")]
        check("his reminder still sounds past the budget - reminders are never rationed",
              rem and rem[0]["silent"] is False and rem[0]["buttons"] == [("Done", "done:9")], rem)
        quiet = {m["text"][:12]: m["silent"] for m in tg.sent
                 if m["tier"] != D.URGENT and not m["text"].startswith(("Time for", "Still to do"))}
        check("a price move, a brief and a memory note are all silent",
              len(quiet) == 3 and all(quiet.values()), quiet)
        nud = [m for m in tg.sent if m["text"].startswith("Still to do")]
        # his words, 2026-11-07: "I don't want that, why would it be silent?"
        check("the nudge sounds like the reminder it follows, past the budget too",
              len(nud) == 1 and nud[0]["silent"] is False, nud)
        check("kinds: reminder and watch sound at any tier; market never",
              NP.audible("alert", "reminder") and NP.audible("alert", "watch")
              and not NP.audible("urgent", "market") and NP.audible("urgent", "news")
              and not NP.audible("alert", "news"))
        check("a price move or a memory note never interrupts him at the PC",
              not NP.interrupts("alert", "market") and not NP.interrupts("alert", "memory")
              and NP.interrupts("urgent", "news") and NP.interrupts("alert", "reminder"))
        # a restart does not buy a new day's budget: today's sounding rows are read back
        from memory.store import memory
        real_recent = memory.recent_deliveries
        today = D.Delivery._today_start()
        memory.recent_deliveries = lambda since, limit=200: [
            {"ts": today + 60, "outcome": "telegram", "subject": "news:a", "sound": True},
            {"ts": today + 70, "outcome": "telegram", "subject": "news:b", "sound": True},
            {"ts": today + 80, "outcome": "telegram", "subject": "task:278", "sound": True},
            {"ts": today + 90, "outcome": "telegram", "subject": "brief:x", "sound": False}]
        try:
            d2 = D.Delivery()
            check("after a restart, today's two sounding pings are still counted (the reminder is not)",
                  d2.sound_budget_left() == 1, d2.sound_budget_left())
        finally:
            memory.recent_deliveries = real_recent
    finally:
        logging.getLogger("jarvis.delivery").removeHandler(h)
        (D.is_present, D.telegram_available, D._in_quiet_hours, D._remember_proactive, old_mod,
         NP.daily_sound_cap) = real
        if old_mod is not None:
            sys.modules["remote_telegram"] = old_mod
        else:
            sys.modules.pop("remote_telegram", None)


# ===================================================================== reminders
def test_reminders() -> None:
    import reminder_voice as RV
    import tasks.scheduler as TS
    from tasks.scheduler import scheduler as S
    print("\n-- reminders: his words, a Done, one nudge, a record --")
    db = S.db
    made_tasks, made_occ = [], []
    try:
        tid = S.add("take my supplements", time.time() - 5, "daily")
        made_tasks.append(tid)
        said = []

        async def announce(text, **kw):
            said.append((text, kw))
        S.announce = announce
        asyncio.run(S._fire(tid, time.time() - 5, "take my supplements", "daily"))
        check("the reminder goes off with its own occurrence",
              said and said[0][1].get("key") == f"task:{tid}" and said[0][1].get("occurrence"), said)
        occ = said[0][1].get("occurrence")
        made_occ.append(occ)
        check("...and is said in his words, plainly", RV.plain(said[0][0]) == "Time for your supplements, sir.",
              RV.plain(said[0][0]))
        main_src = (pathlib.Path(TS.__file__).parent.parent / "main.py").read_text(encoding="utf-8")
        check("main wires it as a reminder with a Done button, and the nudge",
              "plain(text), tier=ALERT, key=key, buttons=buttons" in main_src
              and 'buttons = [("Done", f"done:{occurrence}")]' in main_src
              and "scheduler.nudge = _nudge_reminder" in main_src and "plain(text)" in main_src
              and "phrase(" not in main_src)

        # --- the one nudge -------------------------------------------------------------
        nudged = []

        async def nudge(text, **kw):
            nudged.append((text, kw))
        S.nudge = nudge
        real_due = S.due_nudges
        S.due_nudges = lambda now=None: [o for o in real_due(now) if o["id"] in made_occ]
        asyncio.run(S._nudge_tick(time.time()))
        check("no nudge inside half an hour", nudged == [], nudged)
        db.execute("UPDATE reminder_log SET fired_ts=? WHERE id=?", (time.time() - 31 * 60, occ))
        db.commit()
        asyncio.run(S._nudge_tick(time.time()))
        asyncio.run(S._nudge_tick(time.time()))
        asyncio.run(S._nudge_tick(time.time() + 3600))
        check("after half an hour with no Done: exactly one nudge, never more",
              len(nudged) == 1 and nudged[0][1].get("key") == f"nudge:{occ}", nudged)
        check("...in his words", RV.nudge(nudged[0][0]) == "Still to do, sir: take your supplements." if nudged else False)
        # done in time: no nudge
        occ2 = S.log_occurrence(tid, "take my supplements", time.time())
        made_occ.append(occ2)
        db.execute("UPDATE reminder_log SET fired_ts=? WHERE id=?", (time.time() - 40 * 60, occ2))
        db.commit()
        check("Done marks it", S.mark_done(occ2, via="telegram"))
        check("...once", not S.mark_done(occ2, via="telegram"))
        nudged.clear()
        asyncio.run(S._nudge_tick(time.time()))
        check("a reminder marked done is never nudged", nudged == [], nudged)
        # long ago: no nudge after a restart
        occ3 = S.log_occurrence(tid, "take my supplements", time.time())
        made_occ.append(occ3)
        db.execute("UPDATE reminder_log SET fired_ts=? WHERE id=?", (time.time() - 5 * 3600, occ3))
        db.commit()
        asyncio.run(S._nudge_tick(time.time()))
        check("last night's reminder is not nudged this morning", nudged == [], nudged)

        # --- the Done button, and a typed "done" ---------------------------------------
        b, calls = bridge()
        occ4 = S.log_occurrence(tid, "take my supplements", time.time())
        made_occ.append(occ4)
        press = {"update_id": 5, "callback_query": {"id": "cb1", "from": {"id": 111}, "data": f"done:{occ4}",
                                                     "message": {"message_id": 77, "chat": {"id": 111}}}}
        asyncio.run(b._handle_update(press))
        row = db.execute("SELECT done_ts, done_via FROM reminder_log WHERE id=?", (occ4,)).fetchone()
        check("pressing Done records this occurrence as done", row and row[0] and row[1] == "telegram", row)
        check("...says so", any(m == "answerCallbackQuery" and p.get("text") == "Marked done, sir." for m, p in calls), calls)
        check("...and takes the button off the message",
              any(m == "editMessageReplyMarkup" and p.get("message_id") == 77 for m, p in calls), calls)
        calls.clear()
        asyncio.run(b._handle_update(press))
        check("a second press changes nothing", any(p.get("text") == "Already noted, sir." for _m, p in calls), calls)
        occ5 = S.log_occurrence(tid, "take my supplements", time.time())
        made_occ.append(occ5)
        calls.clear()
        turns = []

        async def no_turn(text):
            turns.append(text)
        b._remote_turn = no_turn
        asyncio.run(b._handle_update({"update_id": 6, "message": {"chat": {"id": 111}, "from": {"id": 111},
                                                                   "text": "done"}}))
        row = db.execute("SELECT done_ts FROM reminder_log WHERE id=?", (occ5,)).fetchone()
        check("'done' typed under a reminder is the button pressed", row and row[0] and not turns, (row, turns))
        check("...and the receipt is silent",
              any(p.get("disable_notification") is True and p.get("text") == "Noted, sir." for _m, p in calls), calls)

        # --- "have I taken my supplements this week" ---------------------------------
        from brain import skills as K
        tonic = S.add("take my gate tonic", time.time() + 3600, "daily")
        made_tasks.append(tonic)
        base = time.time() - 6.5 * 86400
        week = []
        for i in range(7):
            o = S.log_occurrence(tonic, "take my gate tonic", base + i * 86400)
            db.execute("UPDATE reminder_log SET fired_ts=? WHERE id=?", (base + i * 86400, o))
            if i not in (1, 4):
                db.execute("UPDATE reminder_log SET done_ts=? WHERE id=?", (base + i * 86400 + 60, o))
            week.append(o)
        db.commit()
        made_occ.extend(week)
        slots = K.slots_reminder_record("have i taken my gate tonic this week")
        check("the question is recognised as one about his reminder", slots is not None, slots)
        said = K.say_reminder_record(slots or {}, {})
        days = [dt.datetime.fromtimestamp(base + i * 86400).strftime("%A") for i in (1, 4)]
        check("...and answered from the record: 5 of 7, and which days not",
              "5 of 7" in said and all(d in said for d in days), said)
        check("a question about something he has no reminder for is not this skill",
              K.slots_reminder_record("have i taken the bins out this week") is None)
        check("an order to set one is not this skill",
              K.slots_reminder_record("remind me every day at 7 pm to take my supplements") is None)
        # saying it at the PC
        occ6 = S.log_occurrence(tid, "take my supplements", time.time())
        made_occ.append(occ6)
        sl = K.slots_reminder_done("i took my supplements")
        check("'I took my supplements' with one open is recognised", sl is not None, sl)
        check("...and recorded", K.say_reminder_done(sl or {}, {}) == "Noted, sir."
              and db.execute("SELECT done_via FROM reminder_log WHERE id=?", (occ6,)).fetchone()[0] == "voice")
        for o in made_occ:                      # the earlier ones are still open: close them
            S.mark_done(o, via="gate")
        check("with nothing open it is not this skill", K.slots_reminder_done("i took my supplements") is None,
              K.slots_reminder_done("i took my supplements"))
        check("a question is never a Done", K.slots_reminder_done("did i take my supplements?") is None)
    finally:
        S.announce = None
        S.nudge = None
        S.__dict__.pop("due_nudges", None)
        for o in made_occ:
            if o:
                db.execute("DELETE FROM reminder_log WHERE id=?", (o,))
        for t in made_tasks:
            db.execute("DELETE FROM reminder_log WHERE task_id=?", (t,))
            db.execute("DELETE FROM tasks WHERE id=?", (t,))
        db.commit()


# ======================================================================= memory
def NP_kind(kw: dict) -> str:
    import notify_policy as NP
    return NP.kind_of(kw.get("subject", ""), kw.get("key", ""))


def test_memory_alert() -> None:
    import delivery as D
    import proactive as PR
    print("\n-- the memory alert: only when it is JARVIS's problem --")
    sent = []
    real_deliver = D.delivery.deliver

    async def rec(text, tier="notable", **kw):
        sent.append((text, tier, kw))
        return {"delivered": "telegram"}
    D.delivery.deliver = rec
    try:
        p = PR.Proactive()
        affected = {"v": (False, "")}

        async def jarvis_affected():
            return affected["v"]
        p._jarvis_affected = jarvis_affected
        told = {"today": False}
        p._said_memory_today = lambda: told["today"]
        vm = types.SimpleNamespace
        t0 = 1_800_000_000.0
        check("94% is not an alert at all", asyncio.run(p._memory_check(vm(percent=94), t0)) == "" and not sent)
        check("95% starts the clock", asyncio.run(p._memory_check(vm(percent=95.5), t0)) == "waiting")
        check("...nine minutes is still waiting",
              asyncio.run(p._memory_check(vm(percent=96), t0 + 540)) == "waiting" and not sent)
        check("ten minutes, JARVIS answering normally: logged, nothing sent",
              asyncio.run(p._memory_check(vm(percent=96), t0 + 610)) == "logged" and not sent, sent)
        affected["v"] = (True, "slow")
        check("...and it does not look again for ten minutes (no health call every minute)",
              asyncio.run(p._memory_check(vm(percent=96), t0 + 615)) == "logged" and not sent, sent)
        affected["v"] = (False, "")
        check("a dip below 95% resets the clock",
              asyncio.run(p._memory_check(vm(percent=90), t0 + 620)) == ""
              and asyncio.run(p._memory_check(vm(percent=97), t0 + 630)) == "waiting")
        affected["v"] = (True, "the model server took 4.2 seconds to answer a health check")
        out = asyncio.run(p._memory_check(vm(percent=97), t0 + 1300))
        check("ten minutes AND JARVIS slowed: told, once",
              out == "sent" and len(sent) == 1 and "slowing me down" in sent[0][0], (out, sent))
        check("...as a memory note: silent, never an interruption",
              sent and NP_kind(sent[0][2]) == "memory", sent[0][2] if sent else sent)
        told["today"] = True
        check("...and never twice in a day",
              asyncio.run(p._memory_check(vm(percent=99), t0 + 2000)) == "already today" and len(sent) == 1)
        import notify_policy as NP
        check("memory notes never sound", not NP.audible("alert", "memory") and not NP.audible("urgent", "memory"))
        # a rule HE set is his own request, like a watch; the disk and a break are not
        heard = []

        async def announce(text, **kw):
            heard.append(kw)
        p2 = PR.Proactive()
        p2.announce = announce
        p2.in_quiet_hours = lambda now=None: False
        asyncio.run(p2._fire("rule:cpu>90", "Heads up: CPU is above 90 percent.", cooldown_h=1))
        asyncio.run(p2._fire("disk_low", "Drive C is down to 40 gigabytes.", cooldown_h=12))
        check("a rule he set may sound (watch); the disk warning is silent",
              [NP.kind_of(k.get("subject", ""), k.get("key", "")) for k in heard] == ["watch", "other"]
              and NP.audible("alert", "watch") and not NP.audible("alert", "other"), heard)
    finally:
        D.delivery.deliver = real_deliver


# ================================================================== model check
def test_news_check() -> None:
    import news_check as NC
    print("\n-- the model's second opinion can veto, never promote --")
    good = ('Thinking... the leak is ongoing. {"happening_now": true, "town": "Natick", "state": "MA", '
            '"country": "US", "hazard_to_public": true, "category": "hazmat", "one_line": "Gas leak on Main St"}')
    check("a clear yes confirms", NC.judge(NC.parse(good))[0] == NC.CONFIRMED, NC.judge(NC.parse(good)))
    for label, ans in (
            ("not happening now", {"happening_now": False, "hazard_to_public": True, "category": "fire"}),
            ("no danger to the public", {"happening_now": True, "hazard_to_public": False, "category": "fire"}),
            ("another state", {"happening_now": True, "hazard_to_public": True, "state": "NH", "category": "road"}),
            ("another country", {"happening_now": True, "hazard_to_public": True, "country": "England",
                                 "category": "lockdown"}),
            ("a town past his radius", {"happening_now": True, "hazard_to_public": True, "town": "Holyoke",
                                        "state": "Massachusetts", "category": "fire"}),
            ("no hazard at all", {"happening_now": True, "hazard_to_public": True, "category": "none"})):
        check(f"vetoed: {label}", NC.judge(ans)[0] == NC.VETOED, NC.judge(ans))
    wordy = {"happening_now": True, "hazard_to_public": True, "town": "Framingham, MA",
             "state": "Massachusetts (MA)", "country": "United States (US)", "category": "police"}
    check("a yes in other words is still a yes", NC.judge(wordy)[0] == NC.CONFIRMED, NC.judge(wordy))
    for label, raw in (("no JSON", "I think it is fine."), ("empty", ""),
                       ("half an answer", '{"happening_now": "maybe", "hazard_to_public": true, "category": "fire"}')):
        check(f"unreadable is unconfirmed, not confirmed: {label}", NC.judge(NC.parse(raw))[0] == NC.UNCONFIRMED,
              NC.judge(NC.parse(raw)))

    async def slow(headline, text):
        await asyncio.sleep(5)
        return good
    real_ask, real_budget = NC._ask, NC.BUDGET_S
    NC._ask, NC.BUDGET_S = slow, 0.1
    try:
        r = asyncio.run(NC.confirm({"headline": "Gas leak on Main Street in Natick"}))
        check("a model that takes too long confirms nothing", r["verdict"] == NC.UNCONFIRMED, r)
    finally:
        NC._ask, NC.BUDGET_S = real_ask, real_budget
    src = pathlib.Path(NC.__file__).read_text(encoding="utf-8")
    check("it waits for a quiet moment, at temperature 0, as a side call",
          "await wait_for_quiet(" in src and '"temperature": 0.0' in src)
    import briefing as br
    bsrc = pathlib.Path(br.__file__).read_text(encoding="utf-8")
    check("an unconfirmed urgent goes out SILENTLY (ALERT), a vetoed one waits for the brief",
          "URGENT if verdict == news_check.CONFIRMED else ALERT" in bsrc and "vetoed:" in bsrc)


# ====================================================================== calendar
def test_calendar_and_weather() -> None:
    import market_calendar as mc
    import nws
    print("\n-- the NYSE calendar, and which weather zone to name --")
    for d in sorted(mc.NYSE_HOLIDAYS):
        check(f"{d} is a weekday holiday", d.weekday() < 5 and not mc.is_trading_day(d))
    check("ten holidays a year, 2026 and 2027",
          sum(d.year == 2026 for d in mc.NYSE_HOLIDAYS) == 10 and sum(d.year == 2027 for d in mc.NYSE_HOLIDAYS) == 10)
    check("Saturday is not a trading day", not mc.is_trading_day(dt.date(2026, 10, 31)))
    check("the day after Thanksgiving is (a half day)", mc.is_trading_day(dt.date(2026, 11, 27)))
    check("Monday morning still shows Friday's close",
          mc.session_state(dt.datetime(2026, 11, 2, 7, 30)) == ("closed", "2026-10-30"))
    check("...the session is open at noon", mc.session_state(dt.datetime(2026, 11, 2, 12, 30)) == ("open", "2026-11-02"))
    check("the holiday table covers the next two months (extend it before it runs out)",
          mc.holiday_table_covers(dt.date.today() + dt.timedelta(days=60)))
    alert = {"properties": {"event": "Special Weather Statement", "areaDesc": "Hartford; Tolland; Southeast Middlesex",
                            "geocode": {"UGC": ["CTZ002", "CTZ003", "MAZ014"]}, "instruction": ""}}
    check("a statement for two states names his zone, not Hartford",
          "for Southeast Middlesex" in nws.spoken(alert), nws.spoken(alert))


# ================================================================= the whole week
def replay_deliveries(brief_results: list[dict]) -> dict:
    """Every message of 10-27..11-06 under the new policy:
    sounding / silent / brief-only / dropped (and 'unchanged' for the one a
    test muted). Returns {category: {before..., after...}}."""
    import notify_policy as NP
    import significance as S
    by_head = {}
    for r in NEWS_JUDGED:
        by_head.setdefault(r[5].lower()[:80], r)
        if r[1] == "read":
            by_head[r[5].lower()[:80]] = r
    briefs = {r["id"]: r for r in brief_results}
    table, rows = {}, []
    for rid, when, tier, outcome, subject, text in DELIVERIES:
        kind = NP.kind_of(subject, subject)
        before = ("muted" if outcome == "nothing" else "spoken" if outcome == "spoken" else "sounding")
        if outcome == "nothing":
            after, cat = "unchanged", "other"
        elif kind == "brief":
            cat = "brief"
            after = "silent" if briefs.get(rid, {}).get("sent") else "dropped"
        elif kind == "reminder":
            cat, after = "reminder", "sounding"
        elif kind == "news":
            cat = "news " + tier
            row = by_head.get(subject[5:85])
            st = story_of(row) if row else {"headline": subject[5:], "summary": text}
            new, _why = S.classify_news(st)
            after = ("sounding" if new == S.URGENT else "brief-only" if new == S.NOTABLE else "dropped")
        elif kind == "market":
            cat, after = "price/index", ("silent" if not NP.audible("alert", kind) else "sounding")
        elif text.startswith("Memory is at"):
            # 94%: below the line. 95%+: logged unless JARVIS himself was slowed
            # (his model server answered normally all week: the probe never failed)
            cat, after = "memory", "dropped"
        else:
            cat, after = "other", ("sounding" if NP.audible(tier, kind) else "silent")
        rows.append((rid, when, cat, before, after))
        t = table.setdefault(cat, {"n": 0, "before": {}, "after": {}})
        t["n"] += 1
        t["before"][before] = t["before"].get(before, 0) + 1
        t["after"][after] = t["after"].get(after, 0) + 1
    return {"table": table, "rows": rows}


def test_week(brief_results) -> None:
    print("\n-- the whole ten days, before and after --")
    r = replay_deliveries(brief_results)
    table = r["table"]
    total = sum(t["n"] for t in table.values())
    check("all 98 messages of 10-27..11-06 are replayed", total == len(DELIVERIES) == 98, total)
    before_sound = sum(t["before"].get("sounding", 0) for t in table.values())
    after_sound = sum(t["after"].get("sounding", 0) for t in table.values())
    rem_sound = table.get("reminder", {}).get("after", {}).get("sounding", 0)
    check("before: 96 reached his phone with a sound", before_sound == 96, before_sound)
    check("after: only his 22 reminders sound - nothing else did", after_sound == rem_sound == 22,
          (after_sound, rem_sound))
    check("no news item sounds", all(t["after"].get("sounding", 0) == 0
                                     for c, t in table.items() if c.startswith("news")))
    check("price and index moves are kept, silently",
          table.get("price/index", {}).get("after") == {"silent": table["price/index"]["n"]}, table.get("price/index"))
    print(f"\n  {'category':<14}{'n':>4}   {'before':<34}{'after'}")
    for cat in sorted(table):
        t = table[cat]
        b = ", ".join(f"{k} {v}" for k, v in sorted(t["before"].items()))
        a = ", ".join(f"{k} {v}" for k, v in sorted(t["after"].items()))
        print(f"  {cat:<14}{t['n']:>4}   {b:<34}{a}")


def main() -> int:
    from memory.store import memory
    real_log = memory.log_delivery
    memory.log_delivery = lambda row: None          # nothing this gate delivers is written to the shared ledger
    try:
        replay_news()
        res = test_briefs()
        test_telegram()
        test_budget()
        test_reminders()
        test_memory_alert()
        test_news_check()
        test_calendar_and_weather()
        test_week(res)
    finally:
        memory.log_delivery = real_log
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


# Every row of news_judged in his database (2026-10-27..11-06):
# (when, stage, old tier, old why, source, headline, judged text)
NEWS_JUDGED = [
    ('2026-10-27 05:20', 'headline', 'urgent', 'something dangerous close to home',
     'WHDH', 'Healey activates national guard to battle western MA wildfire',
     'Governor Maura Healey activated the National Guard to battle a wildfire that has spread into the western part of the state. That fire, which started...'),
    ('2026-10-28 11:12', 'headline', 'urgent', 'something dangerous close to home',
     'MassLive', 'Western Mass. wildfire reaches 70% containment Tuesday',
     'By Tuesday, the fire encompassed about 200 acres and the National Guard had been activated to assist local firefighters.'),
    ('2026-10-29 07:56', 'headline', 'urgent', 'something dangerous close to home',
     'MassLive', "RMV employee's bribe income exceeded her government salary, AGO says",
     'When the pandemic caused delays to those services, the woman started taking payments to complete customers’ transactions at a speed that was not available to ordinary citizens.'),
    ('2026-10-29 11:56', 'headline', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'WHDH', 'New Veterans Home in Holyoke opens after deadly 2020 COVID-19 outbreak',
     'Governor Maura Healey was in Holyoke Thursday morning with other local leaders for the opening of a new veteran&#8217;s home. The Veterans Home at Holyoke...'),
    ('2026-10-29 11:56', 'read', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'WHDH', 'New Veterans Home in Holyoke opens after deadly 2020 COVID-19 outbreak',
     'Governor Maura Healey and local leaders opened a new Veterans Home in Holyoke, which provides 234 long‑term care beds, a memory care unit, and a potential adult day health program.'),
    ('2026-10-31 08:35', 'headline', 'alert', 'a death nearby, and it is still unfolding',
     'MassLive', 'Country music’s most devastating year: These legendary singers have died in 2026',
     'It has been a heartbreaking year for country music.'),
    ('2026-10-31 11:03', 'headline', 'urgent', 'something dangerous close to home',
     'MassLive', "Dear Annie: Is there a polite way to tell a co-worker she's become the office outbreak source?",
     'We think she either likes the attention or she’s just lazy.'),
    ('2026-11-01 06:15', 'headline', 'urgent', 'something dangerous close to home',
     'WCVB Boston', 'Firefighters evacuate building after discovering structural issues',
     'Firefighters evacuate a residential building after discovering structural issues while fighting small fire'),
    ('2026-11-01 06:15', 'read', 'urgent', 'something dangerous close to home',
     'WCVB Boston', 'Firefighters evacuate building after discovering structural issues',
     'Firefighters evacuated a six‑unit residential building at 211 Boylston Street after finding the rear walls were unstable.'),
    ('2026-11-01 10:13', 'headline', 'urgent', 'the whole country needs to know this',
     'CBS', 'Shooting after Alabama football game leaves 1 dead, 8 injured',
     'The shooting outside Legion Field in Birmingham, Alabama, was among several violent incidents nationwide on Halloween weekend.'),
    ('2026-11-01 12:49', 'headline', 'urgent', 'many people have died',
     'WCVB Boston', 'Dozens injured, at least 6 dead in Halloween weekend gun violence at nightlife districts across US',
     'Dozens of people were injured and at least six people died in mass shootings as gun violence broke out at nightlife districts in half a dozen cities across the country.'),
    ('2026-11-01 12:49', 'read', 'urgent', 'many people have died',
     'WCVB Boston', 'Dozens injured, at least 6 dead in Halloween weekend gun violence at nightlife districts across US',
     'Mass shootings occurred in nightlife districts across several U.S. cities over Halloween weekend, killing at least six people and injuring dozens.'),
    ('2026-11-02 16:49', 'headline', 'alert', 'a death nearby, and it has closed something',
     'WCVB Boston', '3 defense witnesses testify in Farwell trial, closing arguments expected later in the week',
     'Testimony resumes Monday in the trial former Stoughton, Massachusetts, police officer, charged in the death of Sandra Birchmore.'),
    ('2026-11-02 17:11', 'headline', 'alert', 'a death nearby, and it has closed something',
     'WCVB Boston', '2 Mass. residents killed in NH crash',
     'Part of Route 104 in Meredith was shut down Monday after a fatal crash.'),
    ('2026-11-03 12:08', 'headline', 'urgent', 'something dangerous close to home',
     'WCVB Boston', 'Millions of dollars from the worst US gas leak are now available to help residents electrify homes',
     'The worst methane leak in U.S. history sickened thousands. Now, settlement money is helping affected homeowners electrify their houses.'),
    ('2026-11-03 12:08', 'read', 'urgent', 'something dangerous close to home',
     'WCVB Boston', 'Millions of dollars from the worst US gas leak are now available to help residents electrify homes',
     'Millions of dollars from the Aliso Canyon gas‑leak settlement are now available to residents to replace gas appliances with electric ones, with $20 million already released and a total of $40 million earmarked for heat pumps, electric stoves, water heaters and other equipment. The program targets single‑family homes in the affected area, with additional funding for qualifying low‑income households'),
    ('2026-11-03 16:11', 'headline', 'urgent', 'something dangerous close to home',
     'WCVB Boston', 'Suspicious election mail case: Man accused of sending powder, threats',
     'Hundreds of suspicious letters, including some sent to Massachusetts, are believed to be from the same man. The threatening parcels sparked concern and evacuations in the run-up to this election and in 2024.'),
    ('2026-11-03 18:09', 'headline', 'urgent', 'something dangerous close to home',
     'WCVB Boston', "Political analysts predict ‘blue wave,' trouble for Trump",
     'Mary Anne Marsh predicts a ‘tsunami’ for Democrats, while Rob Gray sees a ‘red undertow’ driven by Donald Trump. Both analysts say the results could have major implications for Trump’s political standing.'),
    ('2026-11-04 08:00', 'headline', 'alert', 'a death nearby, and it has closed something',
     'WHDH', 'Trooper charged in connection with trainee death appears in court',
     'One of the four suspended Massachusetts State Police troopers charged in connection with a training tragedy was back in Court on Tuesday. Lieutenant Jennifer Penton...'),
    ('2026-11-04 15:44', 'headline', 'alert', 'a death nearby, and it is still unfolding',
     'MassLive', 'Officials share cause of fire involved in Boston firefighter Bobby Kilduff’s death',
     'Robert “BK” Kilduff Jr. died May 23 while responding to a home fire in Dorchester. The Rescue Company 2 firefighter fell from a third-story window and was critically injured.'),
    ('2026-11-04 18:06', 'headline', 'urgent', 'something dangerous close to home',
     'MassLive', 'Taco Bell recovers after cyclospora outbreak: ‘Continuing to play offense’',
     "Taco Bell is making a slow recovery following this summer's cyclosporiasis outbreak."),
    ('2026-11-05 15:46', 'headline', 'urgent', 'something dangerous close to home',
     'MassLive', 'Why you should’t panic about governments urging emergency kits (but what should be in yours)',
     'The guidance while in most cases was nothing new, prompted concerns of an impending natural disaster, terrorist attack or war.'),
    ('2026-11-05 16:35', 'headline', 'urgent', 'the whole country needs to know this',
     'MassLive', 'Gavin Newsom just made one of his biggest moves yet on California’s death penalty',
     'California Gov. Gavin Newsom plans to commute 548 death sentences to life without parole, a sweeping move that could reshape death row nationwide.'),
    ('2026-11-06 05:28', 'headline', 'alert', 'a death nearby, and it has closed something',
     'WCVB Boston', 'Closing statements expected today in Matthew Farwell trial',
     'Closing statements are expected Friday in the trial former Stoughton, Massachusetts, police officer, charged in the death of Sandra Birchmore.'),
    ('2026-11-06 06:51', 'headline', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'BBC', 'Two Gloucester schools in lockdown after boy hurt in incident',
     'Armed police are on the scene in Tuffley - police say pupils are safe within school grounds.'),
    ('2026-11-06 06:51', 'read', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'BBC', 'Two Gloucester schools in lockdown after boy hurt in incident',
     "A boy was injured in an altercation outside St Peter's High School, and the nearby Tuffley school and Willow Primary Academy were put into lockdown while armed police searched for those involved."),
    ('2026-11-06 11:02', 'headline', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'BBC', 'Armed police called after boy hurt near Gloucester school',
     'Two schools went into lockdown following the incident and one woman has been arrested.'),
    ('2026-11-06 11:13', 'headline', 'alert', 'a hazard loose in his state, beyond his twenty miles',
     'BBC', 'Manhunt in Gloucester after boy injured in sword incident',
     'Two schools went into lockdown following the incident and one woman has been arrested.'),
]

# Every row of deliveries in the same window: (id, when, tier, outcome, subject, text)
# (briefs keep the 300 characters the ledger keeps; the rest their first 160)
DELIVERIES = [
    (491, '2026-10-27 07:30', 'brief', 'telegram', 'brief:2026-10-27 07:30',
     'Weather: 44 and clear skies in Framingham, high of 56, low 38. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Yesterday: Yesterday we spoke 132 '),
    (492, '2026-10-27 11:47', 'alert', 'nothing', 'render-done:8dcb84e7',
     "The flat plate is ready, sir — 40 by 30 by 6 millimetres. I chose centred, since you didn't say where."),
    (493, '2026-10-27 12:30', 'brief', 'telegram', 'brief:2026-10-27 12:30',
     'Markets: the S&P 500 up 0.64%; the Nasdaq 100 up 1.39%; the Dow up 0.05%. Yours: Tesla up 3.60%; AMC Entertainment up 2.77%; SpaceX up 2.74%; Nvidia up 1.31%; Apple up 0.61%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is up 3.6% today — and you own it; Amazon: 67 of 70 ana'),
    (494, '2026-10-27 16:15', 'brief', 'telegram', 'brief:2026-10-27 16:15',
     'Markets: the S&P 500 up 0.56%; the Nasdaq 100 up 1.27%; the Dow up 0.06%. Yours: Tesla up 4.04%; AMC Entertainment up 3.46%; SpaceX up 1.14%; Apple up 0.93%; Nvidia up 0.90%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is up 4.0% today — and you own it; Amazon: 67 of 70 ana'),
    (495, '2026-10-27 19:00', 'alert', 'telegram', 'task:278',
     'Sir, it’s the moment to ingest your daily supplements.'),
    (496, '2026-10-27 20:00', 'brief', 'telegram', 'brief:2026-10-27 20:00',
     'Markets: the S&P 500 up 0.56%; the Nasdaq 100 up 1.27%; the Dow up 0.06%. Yours: Tesla up 4.04%; AMC Entertainment up 3.46%; SpaceX up 1.14%; Apple up 0.93%; Nvidia up 0.90%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is up 4.0% today — and you own it; Amazon: 67 of 70 ana'),
    (497, '2026-10-27 21:00', 'alert', 'telegram', 'task:170',
     'Sir, consider fastening your retainers at this moment.'),
    (498, '2026-10-28 07:30', 'brief', 'telegram', 'brief:2026-10-28 07:30',
     'Weather: 31 and clear skies in Framingham, high of 51, low 31. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Yesterday: Yesterday we spoke 2 times, and about 6 seconds when I had to think. I ignored 3 false wakes. Markets: the S&P 500 up 0.56%; the Nasdaq 100 up 1.27%; the Dow up 0.'),
    (499, '2026-10-28 11:12', 'urgent', 'telegram', 'news:western mass. wildfire reaches 70% containment tuesday',
     'Western Mass. wildfire reaches 70% containment Tuesday — MassLive.'),
    (500, '2026-10-28 12:30', 'brief', 'telegram', 'brief:2026-10-28 12:30',
     'Markets: the S&P 500 up 0.20%; the Nasdaq 100 up 0.13%; the Dow up 0.22%. Yours: SpaceX down 2.54%; Tesla down 1.56%; Apple down 0.44%; Nvidia down 0.39%; AMC Entertainment down 0.33%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is down 1.6% today — and you own it; Apple: 3'),
    (501, '2026-10-28 16:15', 'brief', 'telegram', 'brief:2026-10-28 16:15',
     'Markets: the S&P 500 up 0.34%; the Nasdaq 100 up 0.60%; the Dow up 0.27%. Yours: SpaceX down 2.25%; Nvidia down 1.17%; Apple down 0.94%; Tesla down 0.80%; AMC Entertainment up 0.00%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is down 0.8% today — and you own it; Amazon: 67'),
    (502, '2026-10-28 19:00', 'alert', 'telegram', 'task:278',
     'Sir, you should pop your supplements at this point.'),
    (503, '2026-10-28 20:00', 'brief', 'telegram', 'brief:2026-10-28 20:00',
     'Markets: the S&P 500 up 0.34%; the Nasdaq 100 up 0.60%; the Dow up 0.27%. Yours: SpaceX down 2.25%; Nvidia down 1.17%; Apple down 0.94%; Tesla down 0.80%; AMC Entertainment up 0.00%. Worth a look: Tesla: analysts are split — 27 buy, 22 hold, 5 sell. It is down 0.8% today — and you own it; Amazon: 67'),
    (504, '2026-10-28 21:00', 'alert', 'telegram', 'task:170',
     "Sir, it's a good moment to put on your retainers."),
    (505, '2026-10-29 07:30', 'brief', 'telegram', 'brief:2026-10-29 07:30',
     'Weather: 44 and clear skies in Framingham, high of 66, low 41. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 up 0.34%; the Nasdaq 100 up 0.60%; the Dow up 0.27%. Yours: SpaceX down 2.25%; Nvidia down 1.17%; Apple down 0.94%; Tesla down 0.80%; AMC Entertainment u'),
    (506, '2026-10-29 07:56', 'urgent', 'telegram', "news:rmv employee's bribe income exceeded her government salary, ago says",
     "RMV employee's bribe income exceeded her government salary, AGO says — MassLive."),
    (507, '2026-10-29 11:56', 'alert', 'telegram', 'news:new veterans home in holyoke opens after deadly 2020 covid-19 outbreak',
     'Governor Maura Healey and local leaders opened a new Veterans Home in Holyoke, which provides 234 long‑term care beds, a memory care unit, and a potential adult'),
    (508, '2026-10-29 12:30', 'brief', 'telegram', 'brief:2026-10-29 12:30',
     'Markets: the S&P 500 down 0.36%; the Nasdaq 100 down 1.35%; the Dow down 0.41%. Yours: AMC Entertainment up 2.68%; Apple up 1.67%; Nvidia down 0.81%; SpaceX down 0.72%; Tesla up 0.35%. Worth a look: Amazon: 67 of 70 analysts say buy, and it is down 3.1% today; Advanced Micro Devices: 50 of 60 analys'),
    (509, '2026-10-29 15:33', 'alert', 'telegram', '',
     'Memory is at 94 percent - the model server 15 GB, claude 4 GB, python 4 GB. Things may slow down.'),
    (510, '2026-10-29 16:15', 'brief', 'telegram', 'brief:2026-10-29 16:15',
     'Weather: 67 and clear skies in Framingham, high of 68, low 41; The Weather Service has a Special Weather Statement for Hartford until 6:15 PM tomorrow. The combination of dry fuels, gusty northwest winds up to 25 to 35 mph, and minimum relative humidity values near 40 percent will contribute to an e'),
    (511, '2026-10-29 18:08', 'alert', 'spoken', '',
     'Memory is at 94 percent - the model server 15 GB, claude 5 GB, JARVIS 3 GB. Things may slow down.'),
    (512, '2026-10-29 19:00', 'alert', 'telegram', 'task:278',
     'Sir, it’s time to take your supplements.'),
    (513, '2026-10-29 20:00', 'brief', 'telegram', 'brief:2026-10-29 20:00',
     'Markets: the S&P 500 down 0.38%; the Nasdaq 100 down 1.42%; the Dow down 0.37%. Yours: AMC Entertainment up 3.01%; Apple up 1.48%; SpaceX down 0.92%; Nvidia down 0.80%; Tesla down 0.23%. Worth a look: Amazon: 67 of 70 analysts say buy, and it is down 4.2% today; Alphabet: 61 of 67 analysts say buy, '),
    (514, '2026-10-29 21:00', 'alert', 'telegram', 'task:170',
     'Sir, it’s a suitable moment to slide on your retainers.'),
    (515, '2026-10-30 07:30', 'brief', 'telegram', 'brief:2026-10-30 07:30',
     'Weather: 55 and partly cloudy in Framingham, high of 68, low 45. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Yesterday: Yesterday we spoke 2 times, my first word came in under half a second on reflexes, and about 4 seconds when I had to think. I ignored 2 false wakes. Markets: the'),
    (516, '2026-10-30 10:29', 'alert', 'telegram', 'index:QQQ:2026-10-30',
     'the Nasdaq 100 is down 3.09% today.'),
    (517, '2026-10-30 12:30', 'brief', 'telegram', 'brief:2026-10-30 12:30',
     'Markets: the S&P 500 down 1.53%; the Nasdaq 100 down 3.17%; the Dow down 0.70%. Yours: Nvidia down 4.08%; SpaceX down 4.07%; Tesla down 0.77%; AMC Entertainment down 0.65%; Apple down 0.36%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.9% today; Alphabet: 61 of 67 analysts say buy. '),
    (518, '2026-10-30 16:15', 'brief', 'telegram', 'brief:2026-10-30 16:15',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.39%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Alphabet: 61 of 67 analysts say buy. '),
    (519, '2026-10-30 19:00', 'alert', 'telegram', 'task:278',
     'Sir, now is the proper time to take your supplements.'),
    (520, '2026-10-30 20:01', 'brief', 'telegram', 'brief:2026-10-30 20:00',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Intel: analysts are split — 20 buy, 3'),
    (521, '2026-10-30 21:00', 'alert', 'telegram', 'task:170',
     'Sir, now is an opportune moment to place your retainers.'),
    (522, '2026-10-31 07:30', 'brief', 'telegram', 'brief:2026-10-31 07:30',
     'Weather: 39 and clear skies in Framingham, high of 54, low 37. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertain'),
    (523, '2026-10-31 07:50', 'alert', 'telegram', '',
     'Memory is at 94 percent - the model server 15 GB, hades 4 GB, claude 4 GB. Things may slow down.'),
    (524, '2026-10-31 08:35', 'alert', 'telegram', 'news:country music’s most devastating year: these legendary singers have died in 2026',
     'Country music’s most devastating year: These legendary singers have died in 2026 — MassLive.'),
    (525, '2026-10-31 11:03', 'urgent', 'telegram', "news:dear annie: is there a polite way to tell a co-worker she's become the office ou",
     "Dear Annie: Is there a polite way to tell a co-worker she's become the office outbreak source? — MassLive."),
    (526, '2026-10-31 12:30', 'brief', 'telegram', 'brief:2026-10-31 12:30',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Broadcom: 52 of 56 analysts say buy, '),
    (527, '2026-10-31 16:15', 'brief', 'telegram', 'brief:2026-10-31 16:15',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Broadcom: 52 of 56 analysts say buy, '),
    (528, '2026-10-31 19:00', 'alert', 'telegram', 'task:278',
     'Sir, now would be an opportune moment for your supplements.'),
    (529, '2026-10-31 20:01', 'brief', 'telegram', 'brief:2026-10-31 20:00',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Broadcom: 52 of 56 analysts say buy, '),
    (530, '2026-10-31 21:00', 'alert', 'telegram', 'task:170',
     'Sir, now would be an ideal moment to slip your retainers on.'),
    (531, '2026-11-01 06:15', 'urgent', 'telegram', 'news:firefighters evacuate building after discovering structural issues',
     'Firefighters evacuated a six‑unit residential building at 211 Boylston Street after finding the rear walls were unstable — WCVB Boston.'),
    (532, '2026-11-01 07:30', 'brief', 'telegram', 'brief:2026-11-01 07:30',
     'Weather: 33 and clear skies in Framingham, high of 58, low 28. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertain'),
    (533, '2026-11-01 12:30', 'brief', 'telegram', 'brief:2026-11-01 12:30',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Alphabet: 61 of 67 analysts say buy. '),
    (534, '2026-11-01 12:49', 'urgent', 'telegram', 'news:dozens injured, at least 6 dead in halloween weekend gun violence at nightlife d',
     'Mass shootings occurred in nightlife districts across several U.S. cities over Halloween weekend, killing at least six people and injuring dozens — WCVB Boston.'),
    (535, '2026-11-01 16:16', 'brief', 'telegram', 'brief:2026-11-01 16:15',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.4% today; Alphabet: 61 of 67 analysts say buy. '),
    (536, '2026-11-01 19:00', 'alert', 'telegram', 'task:278',
     'Sir, this is the moment to take your daily supplements.'),
    (537, '2026-11-01 20:00', 'brief', 'telegram', 'brief:2026-11-01 20:00',
     'Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertainment down 0.65%. Worth a look: Nvidia: 66 of 69 analysts say buy, and it is down 4.2% today — and you own it; Amazon: 67 of 70'),
    (538, '2026-11-01 21:00', 'alert', 'telegram', 'task:170',
     'Sir, this is the time to secure your retainers.'),
    (539, '2026-11-02 07:30', 'brief', 'telegram', 'brief:2026-11-02 07:30',
     'Weather: 42 and clear skies in Framingham, high of 69, low 38. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 down 1.77%; the Nasdaq 100 down 3.47%; the Dow down 1.07%. Yours: SpaceX down 6.37%; Nvidia down 4.22%; Apple down 1.23%; Tesla down 0.87%; AMC Entertain'),
    (540, '2026-11-02 12:30', 'brief', 'telegram', 'brief:2026-11-02 12:30',
     'Markets: the S&P 500 up 0.45%; the Nasdaq 100 up 0.29%; the Dow up 1.02%. Yours: AMC Entertainment up 4.58%; SpaceX up 4.10%; Tesla up 2.52%; Apple down 1.96%; Nvidia up 0.98%. Worth a look: Amazon: 67 of 70 analysts say buy, but it is up 4.4% today — that is chasing it, not buying a dip; CrowdStrik'),
    (541, '2026-11-02 13:56', 'alert', 'telegram', '',
     'Memory is at 95 percent - the model server 15 GB, claude 5 GB, java 4 GB. Things may slow down.'),
    (542, '2026-11-02 15:57', 'alert', 'telegram', '',
     'Memory is at 96 percent - the model server 15 GB, claude 4 GB, java 4 GB. Things may slow down.'),
    (543, '2026-11-02 16:15', 'brief', 'telegram', 'brief:2026-11-02 16:15',
     'Markets: the S&P 500 down 0.48%; the Nasdaq 100 down 1.37%; the Dow up 0.43%. Yours: AMC Entertainment up 4.25%; Apple down 1.54%; Nvidia down 1.26%; SpaceX up 1.22%; Tesla up 1.00%. Worth a look: Amazon: 67 of 70 analysts say buy. It is up 2.2% today; CrowdStrike: 45 of 61 analysts say buy, and it '),
    (544, '2026-11-02 19:00', 'alert', 'telegram', 'task:278',
     "Sir, it's time to take your supplements."),
    (545, '2026-11-02 20:00', 'brief', 'telegram', 'brief:2026-11-02 20:00',
     'Markets: the S&P 500 down 0.48%; the Nasdaq 100 down 1.36%; the Dow up 0.43%. Yours: AMC Entertainment up 4.25%; Apple down 1.83%; Nvidia down 1.26%; SpaceX up 1.22%; Tesla up 1.00%. Worth a look: Amazon: 67 of 70 analysts say buy. It is up 2.2% today; CrowdStrike: 45 of 61 analysts say buy, and it '),
    (546, '2026-11-02 21:00', 'alert', 'telegram', 'task:170',
     'Sir, now would be a fine time to lace up your retainers.'),
    (547, '2026-11-03 07:30', 'brief', 'telegram', 'brief:2026-11-03 07:30',
     'Weather: 50 and clear skies in Framingham, high of 74, low 49. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 down 0.48%; the Nasdaq 100 down 1.36%; the Dow up 0.43%. Yours: AMC Entertainment up 4.25%; Apple down 1.83%; Nvidia down 1.26%; SpaceX up 1.22%; Tesla u'),
    (548, '2026-11-03 12:08', 'urgent', 'telegram', 'news:millions of dollars from the worst us gas leak are now available to help residen',
     'Millions of dollars from the Aliso Canyon gas‑leak settlement are now available to residents to replace gas appliances with electric ones, with $20 million alre'),
    (549, '2026-11-03 12:31', 'brief', 'telegram', 'brief:2026-11-03 12:30',
     'Markets: the S&P 500 up 0.47%; the Nasdaq 100 up 0.92%; the Dow down 0.45%. Yours: SpaceX up 4.01%; Nvidia up 3.35%; AMC Entertainment up 2.51%; Apple down 2.05%; Tesla down 1.61%. Worth a look: Apple: 30 of 49 analysts say buy, and it is down 2.1% today — and you own it; Amazon: 67 of 70 analysts s'),
    (550, '2026-11-03 16:15', 'brief', 'telegram', 'brief:2026-11-03 16:15',
     'Markets: the S&P 500 up 0.30%; the Nasdaq 100 up 0.62%; the Dow down 0.44%. Yours: SpaceX up 3.62%; AMC Entertainment up 2.82%; Nvidia up 2.27%; Apple down 1.90%; Tesla down 1.32%. Worth a look: Apple: 30 of 49 analysts say buy. It is down 1.9% today — and you own it; Amazon: 67 of 70 analysts say b'),
    (551, '2026-11-03 18:09', 'urgent', 'telegram', "news:political analysts predict ‘blue wave,' trouble for trump",
     "Political analysts predict ‘blue wave,' trouble for Trump — WCVB Boston."),
    (552, '2026-11-03 19:00', 'alert', 'telegram', 'task:278',
     'Sir, please attend to your supplement intake now.'),
    (553, '2026-11-03 20:00', 'brief', 'telegram', 'brief:2026-11-03 20:00',
     'Markets: the S&P 500 up 0.30%; the Nasdaq 100 up 0.62%; the Dow down 0.44%. Yours: SpaceX up 3.62%; AMC Entertainment up 2.82%; Nvidia up 2.29%; Apple down 1.90%; Tesla down 1.32%. Worth a look: Apple: 30 of 49 analysts say buy. It is down 1.9% today — and you own it; Amazon: 67 of 70 analysts say b'),
    (554, '2026-11-03 21:00', 'alert', 'telegram', 'task:170',
     "Sir, it's time to fit your retainers."),
    (555, '2026-11-04 07:30', 'brief', 'telegram', 'brief:2026-11-04 07:30',
     'Weather: 57 and clear skies in Framingham, high of 75, low 53. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 up 0.30%; the Nasdaq 100 up 0.62%; the Dow down 0.44%. Yours: SpaceX up 3.62%; AMC Entertainment up 2.82%; Nvidia up 2.29%; Apple down 1.90%; Tesla down '),
    (556, '2026-11-04 09:35', 'alert', 'telegram', 'move:TSLA:2026-11-04',
     'Tesla is down 5.57% at 376.29 dollars.'),
    (557, '2026-11-04 09:35', 'alert', 'telegram', 'move:SPCX:2026-11-04',
     'SpaceX is down 5.20% at 133.96 dollars.'),
    (558, '2026-11-04 12:30', 'brief', 'telegram', 'brief:2026-11-04 12:30',
     'Markets: the S&P 500 up 1.87%; the Nasdaq 100 up 1.62%; the Dow up 2.92%. Yours: AMC Entertainment up 7.62%; Apple up 6.35%; Tesla down 2.69%; SpaceX down 2.46%; Nvidia down 1.50%. Worth a look: Apple: 30 of 49 analysts say buy, but it is up 6.3% today — that is chasing it, not buying a dip — and yo'),
    (559, '2026-11-04 14:17', 'alert', 'telegram', 'index:DIA:2026-11-04',
     'the Dow is up 3.06% today.'),
    (560, '2026-11-04 15:44', 'alert', 'telegram', 'news:officials share cause of fire involved in boston firefighter bobby kilduff’s dea',
     'Officials share cause of fire involved in Boston firefighter Bobby Kilduff’s death — MassLive.'),
    (561, '2026-11-04 16:15', 'brief', 'telegram', 'brief:2026-11-04 16:15',
     'Markets: the S&P 500 up 1.65%; the Nasdaq 100 up 0.97%; the Dow up 3.10%. Yours: AMC Entertainment up 7.32%; Apple up 5.31%; SpaceX down 3.88%; Tesla down 3.34%; Nvidia down 3.12%. Worth a look: Apple: 30 of 49 analysts say buy, but it is up 5.3% today — that is chasing it, not buying a dip — and yo'),
    (562, '2026-11-04 18:06', 'urgent', 'telegram', 'news:taco bell recovers after cyclospora outbreak: ‘continuing to play offense’',
     'Taco Bell recovers after cyclospora outbreak: ‘Continuing to play offense’ — MassLive.'),
    (563, '2026-11-04 19:00', 'alert', 'telegram', 'task:278',
     'Sir, it’s time to grab your supplements.'),
    (564, '2026-11-04 19:59', 'alert', 'telegram', '',
     'Memory is at 97 percent - the model server 15 GB, claude 3 GB, JARVIS 3 GB, up 26 points on last week. Things may slow down.'),
    (565, '2026-11-04 20:00', 'brief', 'telegram', 'brief:2026-11-04 20:00',
     'Markets: the S&P 500 up 1.65%; the Nasdaq 100 up 0.97%; the Dow up 3.10%. Yours: AMC Entertainment up 7.32%; Apple up 5.31%; SpaceX down 3.81%; Tesla down 3.34%; Nvidia down 3.17%. Worth a look: Amazon: 67 of 70 analysts say buy, but it is up 6.1% today — that is chasing it, not buying a dip; Corewe'),
    (566, '2026-11-04 21:00', 'alert', 'telegram', 'task:170',
     'Sir, this is the moment to don your retainers.'),
    (567, '2026-11-05 07:30', 'brief', 'telegram', 'brief:2026-11-05 07:30',
     'Weather: 62 and clear skies in Framingham, high of 75, low 57. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Markets: the S&P 500 up 1.65%; the Nasdaq 100 up 0.97%; the Dow up 3.10%. Yours: AMC Entertainment up 7.32%; Apple up 5.31%; SpaceX down 3.81%; Tesla down 3.34%; Nvidia down '),
    (568, '2026-11-05 09:46', 'alert', 'telegram', 'index:QQQ:2026-11-05',
     'the Nasdaq 100 is down 3.00% today.'),
    (569, '2026-11-05 10:44', 'alert', 'telegram', '',
     'Memory is at 95 percent - the model server 15 GB, claude 6 GB, hades 4 GB, up 23 points on last week. Things may slow down.'),
    (570, '2026-11-05 10:48', 'alert', 'telegram', 'index:SPY:2026-11-05',
     'the S&P 500 is down 3.20% today.'),
    (571, '2026-11-05 12:30', 'brief', 'telegram', 'brief:2026-11-05 12:30',
     'Markets: the S&P 500 down 3.56%; the Nasdaq 100 down 5.39%; the Dow down 2.76%. Yours: Tesla down 11.35%; SpaceX down 9.12%; AMC Entertainment down 8.52%; Nvidia down 7.81%; Apple down 1.39%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 1.6% today; Broadcom: 54 of 57 analysts say buy,'),
    (572, '2026-11-05 13:05', 'alert', 'telegram', 'index:DIA:2026-11-05',
     'the Dow is down 3.34% today.'),
    (573, '2026-11-05 13:43', 'alert', 'telegram', '',
     'Memory is at 95 percent - the model server 15 GB, claude 6 GB, java 3 GB, up 24 points on last week. Things may slow down.'),
    (574, '2026-11-05 15:46', 'urgent', 'telegram', 'news:why you should’t panic about governments urging emergency kits (but what should',
     'Why you should’t panic about governments urging emergency kits (but what should be in yours) — MassLive.'),
    (575, '2026-11-05 16:15', 'brief', 'telegram', 'brief:2026-11-05 16:15',
     'Markets: the S&P 500 down 4.41%; the Nasdaq 100 down 6.49%; the Dow down 3.53%. Yours: AMC Entertainment down 12.50%; Tesla down 10.77%; Nvidia down 9.26%; SpaceX down 9.05%; Apple down 1.53%. Worth a look: Nvidia: 66 of 69 analysts say buy, and it is down 9.3% today — and you own it; Amazon: 67 of '),
    (576, '2026-11-05 16:35', 'urgent', 'telegram', 'news:gavin newsom just made one of his biggest moves yet on california’s death penalt',
     'Gavin Newsom just made one of his biggest moves yet on California’s death penalty — MassLive.'),
    (577, '2026-11-05 19:00', 'alert', 'telegram', 'task:278',
     'Sir, this is the moment to pop your supplements.'),
    (578, '2026-11-05 20:00', 'brief', 'telegram', 'brief:2026-11-05 20:00',
     'Markets: the S&P 500 down 4.41%; the Nasdaq 100 down 6.49%; the Dow down 3.53%. Yours: AMC Entertainment down 12.50%; Tesla down 10.77%; Nvidia down 9.26%; SpaceX down 9.05%; Apple down 1.53%. Worth a look: Nvidia: 66 of 69 analysts say buy, and it is down 9.3% today — and you own it; Amazon: 67 of '),
    (579, '2026-11-05 21:00', 'alert', 'telegram', 'task:170',
     "Sir, now's a good chance to strap on your retainers."),
    (580, '2026-11-06 06:51', 'alert', 'telegram', 'news:two gloucester schools in lockdown after boy hurt in incident',
     "A boy was injured in an altercation outside St Peter's High School, and the nearby Tuffley school and Willow Primary Academy were put into lockdown while armed "),
    (581, '2026-11-06 07:30', 'brief', 'telegram', 'brief:2026-11-06 07:30',
     'Weather: 65 and rain in Framingham, high of 69, low 63, 97 percent chance of rain. Today: take my supplements at 7 PM; wear my retainers at 9 PM. Yesterday: Yesterday we spoke 1 times, my first word came in under half a second on reflexes. I ignored 17 false wakes. Markets: the S&P 500 down 4.41%; t'),
    (582, '2026-11-06 09:35', 'alert', 'telegram', 'move:NVDA:2026-11-06',
     'Nvidia is up 6.27% at 192.99 dollars.'),
    (583, '2026-11-06 09:35', 'alert', 'telegram', 'move:SPCX:2026-11-06',
     'SpaceX is up 5.72% at 130.69 dollars.'),
    (584, '2026-11-06 12:30', 'brief', 'telegram', 'brief:2026-11-06 12:30',
     'Markets: the S&P 500 up 0.90%; the Nasdaq 100 up 1.95%; the Dow up 0.14%. Yours: SpaceX up 7.33%; Tesla up 4.61%; Nvidia up 3.72%; AMC Entertainment up 2.60%; Apple up 0.03%. Worth a look: Amazon: 67 of 70 analysts say buy. It is up 0.9% today; Cisco Systems: 23 of 35 analysts say buy. It is down 1.'),
    (585, '2026-11-06 16:15', 'brief', 'telegram', 'brief:2026-11-06 16:15',
     'Markets: the S&P 500 up 0.31%; the Nasdaq 100 up 1.40%; the Dow down 0.40%. Yours: SpaceX up 8.62%; Tesla up 4.23%; Nvidia up 2.78%; AMC Entertainment up 1.95%; Apple down 0.52%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 0.3% today; Coinbase: 31 of 47 analysts say buy. It is down 1'),
    (586, '2026-11-06 19:00', 'alert', 'telegram', 'task:278',
     'Sir, now would be a suitable moment for your supplements.'),
    (587, '2026-11-06 20:00', 'brief', 'telegram', 'brief:2026-11-06 20:00',
     'Markets: the S&P 500 up 0.31%; the Nasdaq 100 up 1.40%; the Dow down 0.40%. Yours: SpaceX up 8.62%; Tesla up 4.23%; Nvidia up 2.78%; AMC Entertainment up 1.95%; Apple down 0.52%. Worth a look: Amazon: 67 of 70 analysts say buy. It is down 0.3% today; Nike: analysts are split — 14 buy, 26 hold, 9 sel'),
    (588, '2026-11-06 21:00', 'alert', 'telegram', 'task:170',
     'Sir, now is the appropriate time to put on your.'),
]


if __name__ == "__main__":
    sys.exit(main())
