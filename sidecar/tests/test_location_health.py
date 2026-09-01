"""Phase 2: his phone's readings, and the poller that must survive them.

Both features ride the SAME inbound Telegram path, which is why they were built
together — that path also carries reminders, alerts and every remote turn, so the
tests that matter most here are the ones about it not breaking.

Gated:

  * a live location arrives as `edited_message`, and `getUpdates` must ASK for
    that kind or Telegram never sends it. Two independent ways this feature could
    have looked broken with nothing in any log;
  * a live share edits its message every few seconds — acknowledging each one
    would be a message storm of exactly the kind that already cost him a night's
    sleep. First fix acknowledged, the rest silent;
  * health JSON is UNTRUSTED external input: size-capped, allow-listed,
    range-checked, unknown keys ignored, and no payload can raise into the poller;
  * the sniffer must never swallow something he actually said;
  * every answer carries its age, and a stale reading says so.

Offline: no network, no Telegram. Run: python tests/test_location_health.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["JARVIS_DB"] = os.path.join(tempfile.mkdtemp(), "p2.db")

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    import volatile
    from tools import health as H
    from tools import location as L

    # ---------------------------------------------------------------- location
    check("with no fix, it says so instead of guessing",
          (await L.where_am_i()).get("error") is not None)

    check("a fix is stored", L.ingest({"latitude": 42.2793, "longitude": -71.4162}))
    got = await L.where_am_i()
    check("...and comes back", round(got["lat"], 3) == 42.279, got)
    check("...carrying its age", got["as_of"] == "just now", got)
    check("...and is not stale", got["stale"] is False)

    check("nonsense coordinates are refused, not stored",
          not L.ingest({"latitude": 999, "longitude": 0}))
    check("a fix with no coordinates is refused",
          not L.ingest({"live_period": 3600}))
    check("...and the good fix is still the one held",
          round((await L.where_am_i())["lat"], 3) == 42.279)

    check("accuracy rides along when the phone sends it",
          L.ingest({"latitude": 42.3, "longitude": -71.4, "horizontal_accuracy": 12.4})
          and (await L.where_am_i()).get("accuracy_m") == 12)

    # haversine against a known pair: Framingham -> Boston is ~20 miles
    miles = L.haversine_miles(42.2793, -71.4162, 42.3601, -71.0589)
    check("haversine is right for a known pair", 17 < miles < 22, f"{miles:.1f} mi")
    check("...and zero distance is zero",
          L.haversine_miles(42.0, -71.0, 42.0, -71.0) == 0.0)

    volatile._conn().execute("UPDATE volatile_facts SET ts=? WHERE key='location'",
                             (time.time() - 6 * 3600,))
    volatile._conn().commit()
    old = await L.where_am_i()
    check("a six-hour-old fix is LABELLED stale, not silently served as current",
          old["stale"] is True, old)
    check("...and still says how old it is", "hours ago" in old["as_of"], old)

    check("distance to nowhere is refused", (await L.distance_to("")).get("error"))

    # ------------------------------------------------------------------ health
    check("with nothing stored it says so", (await H.get_health()).get("error"))

    res = H.ingest_payload(json.dumps({
        "type": "health", "heart_rate": 58, "steps": 8412, "sleep_hours": 7.2}))
    check("a good payload stores every metric", res["stored"] == 3, res)
    hr = await H.get_health("heart rate")
    check("...and reads back by spoken name", hr["metrics"][0]["value"] == 58.0, hr)
    check("...with its age", hr["metrics"][0]["as_of"] == "just now", hr)

    res = H.ingest_payload(json.dumps({"hr": 61, "spo2": 98, "step_count": 9000}))
    check("aliases are understood", res["stored"] == 3, res)

    res = H.ingest_payload(json.dumps({"heart_rate": 4000, "steps": 10}))
    check("a physiologically impossible reading is IGNORED, not reported",
          res["stored"] == 1 and "heart_rate" in res["ignored"], res)
    check("...and the last believable value stands",
          (await H.get_health("heart_rate"))["metrics"][0]["value"] == 61.0)

    res = H.ingest_payload(json.dumps({"favourite_colour": "blue", "steps": 5}))
    check("unknown keys are ignored rather than stored",
          res["stored"] == 1 and "favourite_colour" in res["ignored"], res)

    for bad, why in [("not json at all", "not JSON"),
                     ("[1,2,3]", "a list, not an object"),
                     ('{"heart_rate": "sixty"}', "an unparseable value"),
                     ('{"heart_rate": null}', "a null"),
                     ("{" + '"steps":1,' * 20000 + "}", "far too large")]:
        out = H.ingest_payload(bad)
        check(f"malformed input survives: {why}",
              isinstance(out, dict) and out.get("stored", 0) == 0, out)

    check("a payload never raises",
          H.ingest_payload(None).get("stored") == 0)

    # -------------------------------------------------- the sniffer's restraint
    for said in ["what's my heart rate", "how many steps have I done",
                 "{not really json}", "steps", "", "{}"]:
        check(f"NOT treated as telemetry: {said!r}", not H.looks_like_payload(said))
    for payload in ['{"heart_rate": 58}', '{"type":"health","x":1}',
                    '{"steps": 100, "sleep_hours": 8}']:
        check(f"IS treated as telemetry: {payload[:28]!r}", H.looks_like_payload(payload))

    # ------------------------------------------------- the poller's own wiring
    import inspect

    import remote_telegram as RT
    src = inspect.getsource(RT)
    check("getUpdates asks for edited_message — without it Telegram sends nothing",
          '"edited_message"' in src or "'edited_message'" in src)
    check("the handler reads edited_message as well as message",
          'u.get("edited_message")' in src)
    check("a live-share edit does NOT send a reply each time",
          "if ok and not edited" in src,
          "acknowledging every edit is the message-storm failure again")
    check("the sniffer cannot raise into the poller",
          "def _health_payload" in src and "return False" in src)
    check("phone data is handled AFTER the allowed-chat check",
          src.index("silence for strangers") < src.index('msg.get("location")'))

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
