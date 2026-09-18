"""Faster, smarter, better - round one (2026-09-18).

What his real numbers said and what changed:
  * turn_stats stored only the whole turn, speech included, so a long weather
    sentence "took 4.5 s" with its first word at 160 ms. Now the time to the
    first word and the model's token counts are filed with every turn.
  * "Has OpenAI stock been released?" took 38 s of quoting Apple and asking
    Finnhub for a ticker called OPENAI. A private-company table answers at once.
  * "How far apart are they" (Lima, Santiago) came back 1,200 km; it is 2,450.
    distance_between is arithmetic over the geocoder.
  * The forecast is cached ten minutes; the observation's own age is spoken.
  * The morning brief carries one sentence on how he did yesterday.
  * A lasting fact said in passing is offered for memory through the MEDIUM
    gate - never to a muted room, never to the phone, never for questions.
Run: python tests/test_faster.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "faster.db"))
ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from brain import skills as K
    from brain import overheard
    from brain.skills import SKILL_BY_NAME
    from tools import market_tools as M
    from tools import location as L
    from tools import weather as W
    from memory.store import memory
    import self_report
    from tools import memory_tools as MT
    for mod in (L, MT):
        try:
            mod.register_all()
        except Exception:
            pass

    print("\n-- companies with no ticker --")
    check("openai is private", M.private_company("openai") == "OpenAI")
    check("'the OpenAI stock' too", M.private_company("the OpenAI stock") == "OpenAI")
    check("spacex's shares", M.private_company("SpaceX's") == "SpaceX")
    check("apple is not", M.private_company("apple") is None)
    check("reddit / figma / klarna are listed now: never called private",
          all(M.private_company(x) is None for x in ("reddit", "figma", "klarna")))
    res = asyncio.run(M.get_stock_quote("OpenAI"))
    check("the quote tool says so without a network call",
          res.get("private") and "isn't publicly traded" in res.get("error", ""), res)
    s = K.slots_private_stock("has openai stock been released")
    check("'has openai stock been released' -> OpenAI", s == {"name": "OpenAI"}, s)
    check("'is spacex publicly traded'", K.slots_private_stock("is spacex publicly traded") == {"name": "SpaceX"})
    check("'what's the ticker for anthropic'", K.slots_private_stock("what's the ticker for anthropic") == {"name": "Anthropic"})
    check("a listed company falls through", K.slots_private_stock("is apple publicly traded") is None)
    check("no stock context falls through", K.slots_private_stock("tell me about openai") is None)
    check("'mars' the planet is not the candy company",
          K.slots_private_stock("how far is mars") is None)
    line = K.say_private_stock({"name": "OpenAI"}, {})
    check("the line is honest and short", "isn't publicly traded" in line and len(line) < 140, line)
    check("the quote skill speaks the same line for 'what's openai trading at'",
          K.say_quote({"symbol": "openai"}, res) == M.not_listed_line("OpenAI"))

    print("\n-- distances are arithmetic --")
    d = K.slots_distance_between("how far is boston from new york")
    check("how far is A from B", d == {"place_a": "boston", "place_b": "new york"}, d)
    d = K.slots_distance_between("what's the distance between lima and santiago")
    check("distance between A and B", d == {"place_a": "lima", "place_b": "santiago"}, d)
    d = K.slots_distance_between("how far apart are lima and santiago")
    check("how far apart are A and B", d == {"place_a": "lima", "place_b": "santiago"}, d)
    d = K.slots_distance_between("how many miles from miami to atlanta")
    check("how many miles from A to B", d == {"place_a": "miami", "place_b": "atlanta"}, d)
    check("'how far apart are they' is the model's (it has the tool and the context)",
          K.slots_distance_between("how far apart are they") is None)
    check("'how far is the moon' is not two places", K.slots_distance_between("how far is the moon") is None)
    check("'how far is boston from here' is distance_to's", K.slots_distance_between("how far is boston from here") is None)
    check("digits mean a unit conversion", K.slots_distance_between("how far is 5 km in miles") is None)
    miles = L.haversine_miles(-12.046, -77.043, -33.447, -70.673)      # Lima -> Santiago
    check("Lima to Santiago is about 1,520 miles, not 750", 1480 < miles < 1560, miles)
    said = K.say_distance_between({}, {"from": "Lima, Peru", "to": "Santiago, Chile", "miles": 1522, "km": 2449})
    check("spoken with both units and the caveat", "1,522 miles" in said and "2,449 kilometres" in said and "crow" in said, said)
    check("the tool is registered for the model", "distance_between" in __import__("tools.registry", fromlist=["registry"]).registry._tools)
    prompts = (ROOT / "llm" / "prompts.py").read_text(encoding="utf-8")
    check("the prompt says distances are the tool's, never estimated", "call distance_between" in prompts)
    sk = SKILL_BY_NAME["distance_between"]
    check("the skill runs the tool", sk.tool == "distance_between")

    print("\n-- the forecast is cached ten minutes --")
    calls = []

    async def fake_fetch(lat, lon, unit):
        calls.append((lat, lon))
        return {"current": {"temperature_2m": 70, "apparent_temperature": 69, "relative_humidity_2m": 50,
                            "weather_code": 1, "wind_speed_10m": 5, "precipitation": 0, "time": "2026-09-18T09:00"},
                "daily": {"temperature_2m_max": [75, 77, 70], "temperature_2m_min": [55, 56, 50],
                          "weather_code": [1, 2, 3], "precipitation_probability_max": [0, 10, 40],
                          "sunrise": ["", "", ""], "sunset": ["", "", ""]}}

    async def fake_geocode(place):
        return (42.28, -71.42, "Framingham, MA")

    W._fetch_forecast, W._geocode = fake_fetch, fake_geocode
    W._forecast_cache.clear()
    a = asyncio.run(W.get_weather("framingham"))
    b = asyncio.run(W.get_weather("framingham", when="tomorrow"))
    check("two questions, one fetch", len(calls) == 1, calls)
    check("today and tomorrow both shaped from it", a.get("today", {}).get("high") == 75 and b.get("tomorrow", {}).get("high") == 77, (a, b))
    W._forecast_cache[(42.28, -71.42, "fahrenheit")] = (time.time() - W.FORECAST_TTL - 1, calls and fake_fetch)
    W._forecast_cache.clear()
    asyncio.run(W.get_weather("framingham"))
    check("an expired entry fetches again", len(calls) == 2, calls)

    print("\n-- the honest numbers --")
    memory.log_turn_stat("reflex", "time", 900, first_ms=150)
    memory.log_turn_stat("llm_general", None, 5200, first_ms=4100, prompt_tokens=2100, gen_tokens=180, reasoning_chars=400)
    memory.log_turn_stat("llm_tools", None, 9000, first_ms=6000, prompt_tokens=9000, gen_tokens=300, reasoning_chars=900)
    cols = {r[1] for r in memory.db.execute("PRAGMA table_info(turn_stats)")}
    check("turn_stats has the new columns", {"first_ms", "prompt_tokens", "gen_tokens", "reasoning_chars"} <= cols, cols)
    rep = memory.turn_stats_report(24)
    check("the report separates reflex from model", rep["reflex_first_ms"] == 150 and rep["model_first_ms"] in (4100, 6000), rep)
    check("...and knows the slowest turn", rep["slowest"]["ms"] == 9000, rep["slowest"])
    check("...and what the model spent", rep["model_gen_tokens"] in (180, 300), rep)
    lines = self_report.lines(24)
    check("the morning brief gets one spoken line", len(lines) == 1 and lines[0][0].startswith("Yesterday we spoke"), lines)
    check("...that gives the first-word times, not the whole turn",
          "reflexes" in lines[0][0] and "think" in lines[0][0] and "first word" in lines[0][1], lines)
    brief = (ROOT / "briefing.py").read_text(encoding="utf-8")
    check("the morning brief carries it", 'out.append(("Yesterday", me))' in brief)
    prov = (ROOT / "llm" / "provider.py").read_text(encoding="utf-8")
    check("the provider files llama-server's own timings", "LocalLLM.last_call = {" in prov and "predicted_per_second" in prov)
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    check("the model path files first_ms and tokens", 'reasoning_chars=_lc.get("reasoning_chars")' in orch)
    check("the reflex path files first_ms", 'label, latency,\n                             first_ms=breakdown.get("first_token_ms")' in orch)

    print("\n-- a fact said in passing is offered, not lost --")
    check("the fact before the question", overheard.extract("my chemistry final is on the 25th, what should i study") == "My chemistry final is on the 25th")
    check("his car", overheard.extract("my car is a 2019 civic") == "My car is a 2019 civic")
    check("his sister's name", overheard.extract("my sister's name is emma") == "My sister's name is emma")
    check("where he lives", overheard.extract("i live in framingham") == "I live in framingham")
    check("an allergy", overheard.extract("i'm allergic to peanuts") == "I'm allergic to peanuts")
    check("a question is not a fact", overheard.extract("what is my sister's name") is None)
    check("'my computer is slow' is not a fact about him", overheard.extract("my computer is slow today") is None)
    check("'my monitor is off' is a state", overheard.extract("my monitor is off") is None)
    check("'my head hurts' is a complaint", overheard.extract("my head hurts") is None)
    check("'remember that my car is a civic' is the remember skill's", overheard.extract("remember that my car is a civic") is None)
    check("'my code is broken' is not", overheard.extract("my code is broken") is None)
    from tools.registry import registry
    t = registry._tools.get("_note_overheard")
    check("the internal tool exists and asks first", t is not None and t.requires_confirmation)
    check("...and the model never sees it", all(s["function"]["name"] != "_note_overheard" for s in registry.schemas()))
    import orchestrator as O
    ph = O.CONFIRM_PHRASE["_note_overheard"]({"content": "My chemistry final is on the 25th"})
    check("the question is in his terms", ph == "Shall I remember that your chemistry final is on the 25th?", ph)
    src = orch[orch.index("async def _offer_overheard"):][:2200]
    check("never to a muted room or the phone", "speaker.silent_until > time.time()" in src and "self.remote_turn" in src)
    check("never twice in ten minutes", "_last_offer_at < 600" in src)
    check("never for something already stored", "memory.search(fact, top_k=1, min_score=0.88)" in src)
    check("waits for the answer to finish", "State.IDLE" in src)
    check("only after a completed model reply", "if full_reply and not self._speak_cancel.is_set():\n            spawn(self._offer_overheard(text)" in orch)

    print("\n-- the conversation is re-read into both prompt shapes while he is idle --")
    src = orch[orch.index("async def _rewarm_history"):][:2600]
    check("both shapes, one token each", "for tl, slot in ((tools, 0), (None, 1)):" in src and "max_tokens=1" in src)
    check("only while idle, two seconds after the turn", "await asyncio.sleep(2.0)" in src and "State.IDLE, State.SLEEPING" in src)
    check("the plain history, no turn note", '{"role": "user", "content": "hi"}' in src)
    check("scheduled after a model reply", "if full_reply:\n            self._schedule_history_rewarm()" in orch)
    check("...and after a reflex that added to the history", 'label not in ("sleep", "correction", "teach"):\n            self._schedule_history_rewarm()' in orch)
    check("cancelled wherever a turn begins", orch.count("self._cancel_history_rewarm()\n") >= 4 and "self._cancel_history_rewarm()\n        self.metrics.begin()" in orch)
    check("held by a reference, never a bare task", 'spawn(self._rewarm_history(), name="history-rewarm")' in orch)
    check("switchable in config", 'config.get("llm", "rewarm_history", default=True)' in orch)

    print()
    if fails:
        print(f"FAILED: {len(fails)}: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
