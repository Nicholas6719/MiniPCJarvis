"""The Weather Service alerts, tiered like the news - offline.

Run: python tests/test_nws.py
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def alert(event, severity="Severe", urgency="Immediate", hours=2, area="Southeast Middlesex; Suffolk",
          instruction="Take shelter now in a basement or an interior room on the lowest floor.",
          status="Actual", aid="urn:oid:2.49.0.1.840.0.abc"):
    exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).isoformat()
    return {"id": aid, "properties": {"id": aid, "event": event, "severity": severity, "urgency": urgency,
                                      "expires": exp, "areaDesc": area, "instruction": instruction,
                                      "description": "A severe thunderstorm was located near Framingham.",
                                      "status": status}}


def main() -> int:
    import nws

    print("\n-- what wakes him --")
    for ev in ("Tornado Warning", "Flash Flood Warning", "Severe Thunderstorm Warning", "Blizzard Warning",
               "Extreme Heat Warning", "Hurricane Warning"):
        check(f"{ev} is urgent", nws.classify(alert(ev))[0] == nws.URGENT, nws.classify(alert(ev)))
    check("an Extreme + Immediate warning of any kind is urgent",
          nws.classify(alert("Civil Danger Warning", "Extreme", "Immediate"))[0] == nws.URGENT)
    print("\n-- what is worth a message --")
    check("a winter weather warning is an alert", nws.classify(alert("Winter Storm Warning"))[0] == nws.ALERT)
    check("a tornado WATCH is an alert, not urgent", nws.classify(alert("Tornado Watch", "Moderate", "Expected"))[0] == nws.ALERT)
    print("\n-- what waits for the brief --")
    for ev, sev in (("Rip Current Statement", "Moderate"), ("Heat Advisory", "Minor"),
                    ("Wind Advisory", "Minor"), ("Winter Storm Watch", "Moderate")):
        check(f"{ev} waits", nws.classify(alert(ev, sev, "Expected"))[0] == nws.NOTABLE, nws.classify(alert(ev, sev)))
    print("\n-- what is nothing --")
    check("a test message is nothing", nws.classify(alert("Test Message"))[0] == nws.NONE)
    check("an exercise is nothing", nws.classify(alert("Tornado Warning", status="Exercise"))[0] == nws.NONE)
    check("an expired warning is nothing", nws.classify(alert("Tornado Warning", hours=-1))[0] == nws.NONE)

    print("\n-- what he hears --")
    line = nws.spoken(alert("Tornado Warning"))
    check("it names the service, the event and the ground",
          line.startswith("The Weather Service has a Tornado Warning for Southeast Middlesex until "), line)
    check("...and gives the one instruction that matters", "Take shelter now" in line, line)
    check("...never the whole bulletin", len(line) < 260, len(line))
    plain = nws.spoken(alert("Wind Advisory", "Minor", "Expected", instruction=""))
    check("with no instruction it says what was said",
          "A severe thunderstorm was located near Framingham" in plain, plain)
    check("the key is the alert's own id", nws.key_of(alert("X")) == "nws:urn:oid:2.49.0.1.840.0.abc")

    print("\n-- the sweep --")
    import asyncio

    async def feats(lat, lon):
        return [alert("Tornado Warning", aid="a"), alert("Rip Current Statement", "Moderate", "Expected", aid="b"),
                alert("Test Message", aid="c")]
    real_active, real_home = nws.active, None
    nws.active = feats
    import tools.weather as W
    real_home = W._home_location

    async def home():
        return (42.28, -71.42, "Framingham")
    W._home_location = home
    try:
        got = asyncio.run(nws.scan(seen={"nws:b"}))
        check("the warning is reported, the seen statement and the test are not",
              [k for _t, _tier, k in got] == ["nws:a"], got)
        check("...at the urgent tier", got and got[0][1] == nws.URGENT, got)
    finally:
        nws.active = real_active
        W._home_location = real_home

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
