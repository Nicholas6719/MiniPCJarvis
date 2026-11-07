"""The scanner desk: his police scanner, heard and judged (scanner_desk.py).

Why: in eleven days he spoke to JARVIS five times and ignored 94 unprompted
Telegram messages, almost none local or actionable. He asked for "info within
a 10-15-20 mile radius of me" from his own scanner. This gate holds the desk
to that:

  * the rules read real Massachusetts dispatch language - incident type,
    address, units, his roads - and a negation is never read as the thing it
    denies ("no entrapment", "nothing showing");
  * transmissions merge into one incident by town and place, and an
    escalation (a second alarm, entrapment confirmed) updates that incident;
  * a fire channel far busier than its own history is something happening,
    with no words at all;
  * URGENT only for active danger in Framingham or within five miles, ALERT
    for that danger elsewhere in range and for crashes on his roads,
    everything else stored; quiet hours and a daily cap hold the rest;
  * the console is started at most once per few minutes and an absent
    console is survived quietly;
  * his voice wins the recogniser: a radio clip never makes one of his turns
    wait, and the desk never loads the model itself.

Offline and hermetic: a fake console (stdlib http.server on an ephemeral
loopback port) serving health, feed, annotate, the doctor's report and WAVs;
a fake transcriber; delivery, the summariser and the clip sender stubbed.
Gates share one database that outlives builds: this one deletes exactly what
it added (hit ids from BASE, incidents on its own channels) at the start and
the end. Run: python tests/test_scanner_desk.py
"""
import asyncio
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "scanner.db"))

import numpy as np  # noqa: E402

fails: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


BASE = 9_000_000_000            # every hit id this gate plants is >= BASE
TEST_CHANNELS = ("Framnghm FD", "Newton PD", "Natick PD", "Sudbury FD", "Waltham PD", "Ashland FD",
                 "Wayland FD", "Hudson FD", "Waltham FD", "Marlboro PD", "Wellesley PD", "Natick FD",
                 "Southboro FD", "Sherborn FD", "Framingham EMS",
                 "Hopkinton FD", "Holliston PD", "Weston FD", "Dover FD", "Maynard FD")
TMP = Path(tempfile.mkdtemp(prefix="scanner-gate-"))


# ------------------------------------------------------------------ dispatch, as it is said
# (transcript, channel town, kinds that must be read, danger, address must contain, roads)
DISPATCH = [
    ("Engine 3, Ladder 1 respond to 45 Union Avenue for a reported structure fire, smoke showing.",
     "Framingham", {"smoke_showing"}, True, "45 Union Ave", []),
    ("Command to Framingham, we have a working fire, two and a half story wood frame, fire showing from the second floor.",
     "Framingham", {"working_fire"}, True, "", []),
    ("Framingham Command, strike a second alarm for 45 Union Avenue.", "Framingham",
     {"multiple_alarm"}, True, "45 Union Ave", []),
    ("Persons trapped, structure fire, 9 Maple Street, heavy smoke from the second floor.", "Hudson",
     {"persons_trapped"}, True, "9 Maple St", []),
    ("Engine 2 respond to 14 Elm Street, Natick, for a fire alarm activation, general alarm.", "Natick",
     {"alarm"}, False, "14 Elm St", []),
    ("Engine 4, car fire, vehicle fully involved, Route 126 near the mall.", "Framingham",
     {"vehicle_fire"}, False, "Route 126", ["Route 126"]),
    ("Tower 1 respond to 300 Boston Post Road for an odor of gas inside the building.", "Sudbury",
     {"gas_leak"}, False, "300 Boston Post Rd", ["Route 20"]),
    ("Report of a carbon monoxide alarm, 22 Pleasant Street, residents are out of the house.", "Marlborough",
     {"co_alarm"}, False, "22 Pleasant St", []),
    ("Brush fire behind the middle school off Hemenway Road, Engine 5 and Forestry 2 respond.", "Framingham",
     {"brush_fire"}, False, "Hemenway Rd", []),
    ("Wires down and arcing on Salem End Road near the reservoir.", "Framingham", {"wires_down"}, False,
     "Salem End Rd", []),
    ("Ice rescue, report of a person through the ice on Lake Cochituate, Rescue 1 respond.", "Natick",
     {"water_rescue"}, False, "", []),
    ("Hazmat team requested, tanker leak on the Mass Pike westbound near exit 117.", "Framingham",
     {"hazmat"}, True, "the Mass Pike", ["the Mass Pike"]),
    ("Car 3 and Rescue 1, MVA with entrapment, Route 9 eastbound at Speen Street.", "Natick",
     {"entrapment"}, True, "Route 9 eastbound at Speen St", ["Route 9"]),
    ("Two car crash on the Mass Pike westbound near exit 117, all lanes blocked.", "Framingham",
     {"mva", "road_closure"}, True, "the Mass Pike westbound", ["the Mass Pike"]),
    ("Respond to 12 Worcester Road for a car versus pole, no one is trapped, airbags deployed.", "Framingham",
     {"mva"}, False, "12 Worcester Rd", ["Route 9"]),
    ("Units respond to Concord Street and Waverly Street for a pedestrian struck.", "Framingham",
     {"pedestrian_struck"}, False, "Concord St and Waverly St", ["Route 126", "Route 135"]),
    ("Rollover on 495 northbound at exit 25, one patient walking around.", "Hudson", {"rollover"}, False,
     "Route 495 northbound", ["Route 495"]),
    ("Route nine westbound at Kingsbury Square, crash, the road is closed.", "Wellesley",
     {"mva", "road_closure"}, True, "Route 9 westbound", ["Route 9"]),
    ("Report of shots fired in the area of Pleasant Street and Main Street.", "Marlborough",
     {"shots_fired"}, True, "Pleasant St and Main St", []),
    ("Caller reports a man with a gun outside the Shell station on Route 20.", "Sudbury",
     {"armed_person"}, True, "Route 20", ["Route 20"]),
    ("Cruiser 12 in pursuit, northbound Route 27, gray Honda failed to stop.", "Natick",
     {"pursuit"}, True, "Route 27", ["Route 27"]),
    ("Missing five year old boy last seen at Cushing Park, wearing a red jacket.", "Framingham",
     {"missing_child"}, True, "", []),
    ("Missing elderly male with dementia walked away from 40 Lincoln Street.", "Framingham",
     {"missing_person"}, False, "40 Lincoln St", []),
    ("Report of a stabbing at the bar on Moody Street, ambulance requested.", "Waltham",
     {"stabbing"}, False, "Moody St", []),
    ("B and E in progress at 8 Hill Street, caller hears someone in the basement.", "Framingham",
     {"break_in"}, False, "8 Hill St", []),
    ("Disturbance in the parking lot of 1 Worcester Road, two males fighting.", "Natick",
     {"disturbance"}, False, "1 Worcester Rd", []),
    ("Medic 2 respond to 77 Waverly Street for a cardiac arrest, CPR in progress.", "Framingham",
     {"cardiac_arrest"}, False, "77 Waverly St", ["Route 135"]),
    ("Possible overdose in the bathroom of the train station, Narcan administered.", "Framingham",
     {"overdose"}, False, "", []),
    ("Suspicious package reported at the post office on Main Street.", "Maynard", {"bomb_threat"},
     False, "Main St", []),
    ("Mutual aid to Natick, Engine 2 respond to 88 Eliot Street for a working fire.", "Framingham",
     {"working_fire"}, True, "88 Eliot St", []),
    ("Armed robbery just occurred at the CVS, suspect fled on foot toward Route 135.", "Ashland",
     {"armed_person", "pursuit"}, True, "Route 135", ["Route 135"]),
    ("Pedestrian struck by a car on Route 30 at Edgell Road, serious injuries.", "Framingham",
     {"pedestrian_struck"}, False, "Route 30", ["Route 30"]),
]
# what must NOT be read as danger
DENIALS = [
    ("No entrapment, minor injuries, units can clear.", "Natick"),
    ("Engine 1 on scene, nothing showing, investigating.", "Framingham"),
    ("No smoke or fire showing, alarm was burnt food.", "Framingham"),
    ("Negative for fire extension, fire is knocked down.", "Framingham"),
    ("Elevator entrapment, 1 Speen Street, building maintenance on the way.", "Framingham"),
    ("Engine 6, the vehicle is fully involved in the lot, no exposures.", "Framingham"),
]


# ------------------------------------------------------------------ audio
def tone(seconds: float, peak_dbfs: float = -35.0) -> np.ndarray:
    t = np.arange(int(seconds * 16000)) / 16000
    x = 0.6 * np.sin(2 * np.pi * 700 * t) + 0.4 * np.sin(2 * np.pi * 1300 * t)
    x = x / np.max(np.abs(x)) * (10 ** (peak_dbfs / 20))
    return x.astype(np.float32)


def wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


# ------------------------------------------------------------------ the fake console
class Console:
    def __init__(self):
        self.health = {"ok": True, "version": "gate", "connected": True, "port": "COM3", "scanning": True,
                       "last_hit_ts": None, "audio": {"enabled": True, "device": "Line In", "recording": True,
                                                      "recent_peak_dbfs": -35.2, "weak_input": True}}
        self.hits: list[dict] = []
        self.annotations: list[dict] = []
        self.doctor = None
        self.clips: dict[int, bytes] = {}
        self.refused = 0
        self.feed_calls = 0
        state = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _guard(self) -> bool:
                if self.headers.get("X-Scanner-Client") != "1":
                    state.refused += 1
                    self._json(403, {"error": "no client header"})
                    return False
                return True

            def do_GET(self):
                if not self._guard():
                    return
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/api/health":
                    return self._json(200, state.health)
                if u.path == "/api/hits/feed":
                    state.feed_calls += 1
                    after, limit = int(q.get("after_id", 0)), int(q.get("limit", 100))
                    got = sorted((h for h in state.hits if h["id"] > after), key=lambda h: h["id"])[:limit]
                    return self._json(200, {"hits": got, "last_id": max([h["id"] for h in state.hits] or [0])})
                if u.path == "/api/doctor/report":
                    return self._json(404, {"error": "none"}) if state.doctor is None else self._json(200, state.doctor)
                if u.path == "/api/clip":
                    data = state.clips.get(int(q.get("id", 0)))
                    if data is None:
                        return self._json(404, {"error": "no recording"})
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json(404, {"error": "no route"})

            def do_POST(self):
                if not self._guard():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if urlparse(self.path).path == "/api/hits/annotate":
                    state.annotations.append(body)
                    return self._json(200, {"ok": True})
                self._json(404, {"error": "no route"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


def closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ------------------------------------------------------------------ cleanup
def scrub(store) -> None:
    db = store.conn()
    marks = ",".join("?" * len(TEST_CHANNELS))
    db.execute("DELETE FROM scanner_hits WHERE id >= ?", (BASE,))
    db.execute(f"DELETE FROM scanner_incidents WHERE channel IN ({marks})", TEST_CHANNELS)
    db.execute("DELETE FROM scanner_sent WHERE incident_id NOT IN (SELECT id FROM scanner_incidents)")
    db.commit()


async def run() -> None:
    import geo
    import scanner_desk as SD
    import volatile
    from config import config
    from delivery import ALERT, URGENT
    from scanner import rules as R
    from scanner import store as S
    from scanner.client import ConsoleClient

    scrub(S)
    cursor_before = volatile.get(SD.CURSOR_KEY)
    cfg_before = dict(config.data.get("scanner") or {})
    config.data.setdefault("scanner", {})
    config.data["scanner"].update({"recordings_dir": str(TMP / "recordings"), "console_exe": str(TMP / "none.exe")})
    console = Console()
    try:
        await _run(SD, R, S, ConsoleClient, console, geo, config, ALERT, URGENT, volatile)
    finally:
        console.stop()
        scrub(S)
        if cursor_before:
            volatile.put(SD.CURSOR_KEY, cursor_before["value"], source="scanner")
        else:
            volatile.forget(SD.CURSOR_KEY)
        config.data["scanner"] = cfg_before
        left = S._rows("SELECT COUNT(*) FROM scanner_hits WHERE id >= ?", (BASE,))[0][0]
        check("the gate took back every row it planted", left == 0, left)
        import shutil
        shutil.rmtree(TMP, ignore_errors=True)       # its WAVs and the dummy exe
        check("...and its temporary files", not TMP.exists())


async def _run(SD, R, S, ConsoleClient, console, geo, config, ALERT, URGENT, volatile) -> None:
    print("\n-- his channel labels, as he typed them --")
    for label, town, agency in (("Framnghm FD", "Framingham", "FD"), ("Action PD", "Acton", "PD"),
                                ("Newton FD 1", "Newton", "FD"), ("Marlboro PD", "Marlborough", "PD"),
                                ("Waltham Fire", "Waltham", "FD"), ("Wellesley EMS", "Wellesley", "EMS")):
        lab = R.parse_label(label)
        check(f"{label!r} -> {town} {agency}", lab.town and lab.town.name == town and lab.agency == agency,
              f"{lab.town and lab.town.name} {lab.agency}")
    check("a label with no town keeps its words", R.parse_label("MSP Troop H").spoken == "MSP Troop H")

    print(f"\n-- {len(DISPATCH)} dispatch transmissions, read by the rules --")
    for text, town, kinds, danger, addr, roads in DISPATCH:
        r = R.read(text, town)
        ok = kinds <= set(r.types) and r.danger == danger and (addr in r.address if addr else True) \
            and set(roads) <= set(r.roads)
        check(f"{text[:58]!r}", ok, f"types={r.types} danger={r.danger} addr={r.address!r} roads={r.roads}")
    r = R.read(DISPATCH[0][0], "Framingham")
    check("units are named", r.units == ["Engine 3", "Ladder 1"], r.units)
    r = R.read("Mutual aid to Natick, Engine 2 respond to 88 Eliot Street for a working fire.", "Framingham")
    check("mutual aid moves the incident to the town it is in", r.towns == ["Natick"], r.towns)
    r = R.read("Respond to 12 Worcester Road for a car versus pole.", "Framingham")
    check("a town's name inside a street's is a street", r.towns == [], r.towns)

    print("\n-- what is denied is not read as danger --")
    for text, town in DENIALS:
        r = R.read(text, town)
        check(f"{text[:58]!r}", not r.danger, f"types={r.types}")
    check("a stand-down is read as one", R.read("Fire is knocked down, units can clear.", "Framingham").resolved)
    check("...but not when it reports a fire", not R.read("Working fire, fire knocked down on the first floor, "
                                                          "extending to the attic.", "Framingham").resolved)

    print("\n-- how much a transcript is trusted --")
    check("squelch hiss read as 'thank you' is not trusted", R.asr_confidence("Thank you.", 3) < R.TRUST)
    check("a word repeated over and over is not trusted",
          R.asr_confidence("the the the the the the the", 4) < R.TRUST)
    check("a real dispatch is trusted", R.asr_confidence(DISPATCH[0][0], 6, {"class": "analog_clear"}) >= 0.6)
    check("digital voice is never sent to the recogniser", not R.transcribable({"class": "digital"}))
    check("...nor squelch noise", not R.transcribable({"class": "noise_squelch"}))
    check("...but an unknown quality is tried", R.transcribable(None))

    print("\n-- the audio is made loud enough to read --")
    x = tone(3.0, -35.0)
    y, peak = SD.normalise_audio(x)
    check("a -35 dBFS clip is brought up to a usable level", y is not None and 0.6 < float(np.max(np.abs(y))) < 0.8,
          f"peak {peak:.1f} -> {float(np.max(np.abs(y))) if y is not None else None}")
    check("...and silence is left as silence", SD.normalise_audio(np.zeros(16000, dtype=np.float32))[0] is None)
    check("a WAV round-trips", len(SD.decode_wav(wav_bytes(x))) == len(x))
    parts = SD.split_audio(tone(25.0))
    check("a long clip is cut into pieces of at most ~10 s", len(parts) >= 3 and max(len(p) for p in parts) <= 10 * 16000
          and sum(len(p) for p in parts) == 25 * 16000, [len(p) / 16000 for p in parts])

    # ------------------------------------------------------------------ deciding
    print("\n-- who gets told what (delivery tiers) --")
    desk = SD.ScannerDesk()
    quiet = [False]
    desk.quiet_hours = lambda: quiet[0]
    natick, waltham = geo.town("Natick").miles, geo.town("Waltham").miles
    near_natick = URGENT if natick <= 5.0 else ALERT

    # two transmissions that name the call: confirmed (his answer, 2026-11-07:
    # nothing sounds or interrupts on one transmission)
    CLEAR = [{"ts": time.time(), "text": "Engine 1 respond for the call.", "conf": 0.9},
             {"ts": time.time(), "text": "Engine 1 on scene, confirming.", "conf": 0.85}]
    ONCE = CLEAR[:1]

    def inc(town, types, roads=(), burst=0, status="active", channel="X", said=None):
        t = geo.town(town) if town else None
        top = max(types, key=lambda k: R.KINDS[k].severity) if types else ""
        return {"town": town, "miles": round(t.miles, 1) if t else None, "types": list(types),
                "label": R.describe(list(types)) or "unusual activity", "roads": list(roads), "burst": burst,
                "status": status, "channel": channel, "family": R.KINDS[top].family if top else "fire",
                "said": CLEAR if said is None else said}
    shaky1 = [{"ts": time.time(), "text": "(noisy)", "conf": 0.4}]
    shaky2 = shaky1 + [{"ts": time.time(), "text": "(noisy again)", "conf": 0.45}]
    table = [
        ("a working fire in Framingham", inc("Framingham", ["working_fire"]), URGENT),
        ("a working fire in Natick", inc("Natick", ["working_fire"]), near_natick),
        ("a working fire in Waltham", inc("Waltham", ["working_fire"]), ALERT if waltham > 5 else URGENT),
        ("a working fire in Leominster, out of range", inc("Leominster", ["working_fire"]), None),
        ("shots fired in Framingham", inc("Framingham", ["shots_fired"]), URGENT),
        ("a missing child in Framingham", inc("Framingham", ["missing_child"]), URGENT),
        ("an entrapment crash in Hudson", inc("Hudson", ["entrapment"]), ALERT),
        ("a crash closing the road in Natick", inc("Natick", ["mva", "road_closure"]), near_natick),
        ("a plain crash on Route 9 in Wellesley", inc("Wellesley", ["mva"], ["Route 9"]), ALERT),
        # THE QUIET FEED: real calls in his radius that do not interrupt
        ("a plain crash on Elm Street in Natick", inc("Natick", ["mva"]), SD.FYI),
        ("a crash in Sudbury, ambulance sent", inc("Sudbury", ["mva"]), SD.FYI),
        ("a gas leak in Framingham", inc("Framingham", ["gas_leak"]), SD.FYI),
        ("a stabbing in Waltham", inc("Waltham", ["stabbing"]), SD.FYI),
        ("a reported structure fire in Wayland", inc("Wayland", ["structure_fire"]), SD.FYI),
        ("a fire alarm in Framingham, his own town", inc("Framingham", ["alarm"]), SD.FYI),
        ("a fire alarm in Natick: routine elsewhere", inc("Natick", ["alarm"]), None),
        # his police and medical calls (his answer, 2026-11-07)
        ("a disturbance in Natick", inc("Natick", ["disturbance"]), SD.FYI),
        ("a theft report in Wellesley", inc("Wellesley", ["larceny"]), SD.FYI),
        ("suspicious activity in Hudson", inc("Hudson", ["suspicious"]), SD.FYI),
        ("a traffic stop in Wayland", inc("Wayland", ["traffic_stop"]), SD.FYI),
        ("a well-being check in Natick", inc("Natick", ["welfare_check"]), SD.FYI),
        ("a chest pain call in Sudbury", inc("Sudbury", ["chest_pain"]), SD.FYI),
        ("a fall in Framingham", inc("Framingham", ["fall"]), SD.FYI),
        ("a medical call in Leominster, out of range", inc("Leominster", ["medical"]), None),
        ("a crash heard once, unclearly", inc("Sudbury", ["mva"], said=shaky1), None),
        ("...heard twice, it is believed", inc("Sudbury", ["mva"], said=shaky2), SD.FYI),
        ("...or once, unclearly, with a second transmission agreeing",
         inc("Wayland", ["structure_fire"], said=[dict(shaky1[0], agreed=True)]), SD.FYI),
        # "Anyone, A1. Why do we have a crash be on anyone A1? I need a crash" (0.70,
        # Natick FD, 2026-11-07) reached his phone as "A crash" and made no sense
        ("a clear line with only the word in it waits for a second (Natick, 3:50 PM)",
         inc("Natick", ["mva"], said=[{"ts": time.time(), "conf": 0.7,
                                       "text": "Anyone, A1. Why do we have a crash be on anyone A1? I need a crash. I need my"}]),
         None),
        ("...and goes when a second transmission says crash too",
         inc("Natick", ["mva"], said=[{"ts": time.time(), "conf": 0.7,
                                       "text": "Anyone, A1. Why do we have a crash be on anyone A1? I need a crash."},
                                      {"ts": time.time(), "conf": 0.76,
                                       "text": "And A1 apparatus cuts for any about after a crash. 38 air street."}]),
         SD.FYI),
        ("a burst on Ashland FD", inc("Ashland", [], burst=1, channel="Ashland FD"),
         ALERT if geo.town("Ashland").miles <= 5 else SD.FYI),
        ("a burst on Hudson FD, beyond five miles", inc("Hudson", [], burst=1, channel="Hudson FD"), SD.FYI),
        # ONE TRANSMISSION NEVER SOUNDS OR INTERRUPTS (his answer, 2026-11-07)
        ("a working fire in Framingham heard ONCE -> a silent unconfirmed line",
         inc("Framingham", ["working_fire"], said=ONCE), SD.FYI),
        ("shots fired in Framingham heard ONCE -> a silent unconfirmed line",
         inc("Framingham", ["shots_fired"], said=ONCE), SD.FYI),
        ("an entrapment crash in Hudson heard ONCE -> a silent unconfirmed line",
         inc("Hudson", ["entrapment"], said=ONCE), SD.FYI),
        ("a crash on Route 9 heard ONCE -> a silent unconfirmed line",
         inc("Wellesley", ["mva"], ["Route 9"], said=ONCE), SD.FYI),
        ("...and a one-line 'working fire' with nothing concrete in it is not sent at all",
         inc("Natick", ["working_fire"], said=[{"ts": time.time(), "conf": 0.8,
                                                "text": "I need a working fire. Why a working fire?"}]), None),
        ("a working fire that is over", inc("Framingham", ["working_fire"], status="over"), None),
        ("a crash that is over", inc("Natick", ["mva"], status="over"), None),
        ("a channel with no town", inc("", ["working_fire"]), None),
    ]
    for name, i, want in table:
        got, why = desk.decide(i)
        check(f"{name} -> {want or 'stored'}", got == want, f"got {got}: {why}")
    got, why = desk.decide(inc("Natick", ["mva"]))
    check("...and says why it is told", "a crash" in why and "Natick" in why, why)
    got, why = desk.decide(inc("Natick", ["alarm"]))
    check("...and why it is not", "routine" in why, why)
    quiet[0] = True
    # HIS NIGHT (his answer, 2026-11-07): "send silently plus in morning summary"
    got, _ = desk.decide(inc("Framingham", ["working_fire"]))
    check("night: danger in his own town still wakes him (URGENT)", got == URGENT)
    got, why = desk.decide(inc("Natick", ["working_fire"]))
    check("night: danger one town over still goes - silently, as an ALERT" if near_natick == URGENT
          else "night: danger one town over goes as an ALERT", got == ALERT and (
              "silently" in why if near_natick == URGENT else True), why)
    got, why = desk.decide(inc("Wellesley", ["mva"], ["Route 9"]))
    check("night: an ALERT goes", got == ALERT, why)
    got, why = desk.decide(inc("Natick", ["mva"]))
    check("night: the quiet feed goes (never spoken - notify_policy.NIGHT_SILENT)", got == SD.FYI, why)
    config.data.setdefault("scanner", {})["night"] = "hold"
    got, why = desk.decide(inc("Natick", ["mva"]))
    check("...and scanner.night = 'hold' brings back holding everything till morning",
          got is None and "quiet" in why, why)
    config.data["scanner"].pop("night", None)
    quiet[0] = False
    config.data.setdefault("scanner", {})["fyi"] = False
    got, why = desk.decide(inc("Natick", ["mva"]))
    check("the quiet feed can be switched off (scanner.fyi)", got is None and "stored" in why, why)
    got, why = desk.decide(inc("Hudson", [], burst=1, channel="Hudson FD"))
    check("...bursts beyond five miles included", got is None, why)
    config.data["scanner"].pop("fyi", None)

    print("\n-- the quiet feed is silent and never cuts in --")
    import notify_policy as NP
    k = NP.kind_of("scanner-fyi: a crash - Sudbury", "scanner-fyi:7")
    check("its kind is scanner_fyi", k == "scanner_fyi", k)
    check("...which never sounds, even sent as an ALERT", NP.audible(ALERT, k) is False)
    check("...and never interrupts him at the PC", NP.interrupts(ALERT, k) is False)
    k2 = NP.kind_of("scanner: a working fire - Natick", "scanner:8")
    check("a real scanner ALERT still interrupts, silently on the phone; an URGENT sounds",
          k2 == "scanner" and NP.interrupts(ALERT, k2) and not NP.audible(ALERT, k2) and NP.audible(URGENT, k2), k2)

    print("\n-- what he hears and reads --")
    i = inc("Framingham", ["smoke_showing", "structure_fire"])
    i.update(address="45 Union Ave", channel="Framnghm FD", said=[{"ts": time.time(), "text": DISPATCH[0][0],
                                                                   "channel": "Framnghm FD"}], summary="")
    spoken, written = desk.compose(i, URGENT)
    check("URGENT speaks the kind, the street in full and the town",
          spoken == "Scanner, sir: a structure fire with smoke showing at 45 Union Avenue here in Framingham.", spoken)
    wl_ = written.splitlines()
    check("...and the phone gets a header, the place and the words he can judge",
          written.startswith("SCANNER - URGENT - Framingham (home)") and "45 Union Ave" in written
          and "Heard on Framnghm FD:" in wl_
          and any(re.match(r"\d{1,2}:\d\d [AP]M  \"", x) and "smoke showing" in x for x in wl_)
          and wl_[-1].startswith("First heard "), written)
    i3 = inc("Sudbury", ["mva"])
    i3.update(channel="Sudbury FD", address="", summary="", units=["Car 3", "Ambulance 1"],
              details=["2 vehicles", "minor injuries", "units on scene"], first_ts=time.time() - 300,
              last_ts=time.time(), hits=4,
              said=[{"ts": time.time() - 300, "text": "Two car MVA, car three ambulance one respond.", "conf": 0.8},
                    {"ts": time.time() - 60, "text": "Car 3 on scene, minor injuries.", "conf": 0.8, "context": True}])
    spoken, written = desk.compose(i3, SD.FYI)
    check("a quiet call is said with what was heard: the facts and who was sent",
          spoken == "From the scanner: a crash in Sudbury, 5 miles away. 2 vehicles, minor injuries. "
                    "Sudbury Fire sent Car 3 and Ambulance 1.", spoken)
    check("...and written with where, the units, what is known and every line heard",
          written.startswith("SCANNER - Sudbury (5 mi)\nA crash\n") and "Units: Car 3, Ambulance 1" in written
          and "Known: 2 vehicles; minor injuries; units on scene" in written and "Heard on Sudbury FD:" in written
          and written.count('  "') == 2 and "4 transmissions - latest" in written, written)
    i2 = inc("Natick", ["entrapment"], ["Route 9"])
    i2.update(address="Route 9 eastbound at Speen St", channel="Natick FD", said=list(CLEAR), summary="")
    spoken, _ = desk.compose(i2, ALERT)
    check("ALERT says how far, and a road is 'on'",
          spoken.startswith("From the scanner: a crash with entrapment on Route 9 eastbound at "
                            "Speen Street in Natick, ") and "mile" in spoken, spoken)
    i2["summary"] = "A crash with people trapped on Route 9 eastbound at Speen Street in Natick"
    spoken, _ = desk.compose(i2, ALERT)
    check("the model's line is used, with the distance and no doubled town",
          spoken.count("Natick") == 1 and "mile" in spoken, spoken)
    spoken, written = desk.compose(i, URGENT, update="it has gone to a second alarm")
    check("an escalation is said as an update", spoken.startswith("Update from the scanner on the fire at 45 Union Avenue")
          and spoken.endswith("it has gone to a second alarm.") and "UPDATE" in written, spoken)
    got = SD.parse_summary('thinking... {"summary": "A house fire on Union Avenue.", "type": "house fire", '
                           '"address": "45 Union Ave", "active": true}')
    check("the model's JSON is read past its reasoning", got and got["summary"] == "A house fire on Union Avenue."
          and got["active"] is True, got)
    check("...and nonsense is not", SD.parse_summary("no json here") is None)

    # ------------------------------------------------------------------ the pipeline
    print("\n-- the console's feed, end to end (fake console, fake recogniser) --")
    now = time.time()
    texts: dict[float, str] = {}
    calls = {"stt": [], "deliver": [], "summ": [], "clip": []}

    def plant(n, name, text, secs, quality="analog_clear", ago=5.0, how="file", transcript=None, asr=None):
        hid = BASE + n
        x = tone(secs)
        data = wav_bytes(x)
        texts[round(len(x) / 16000, 2)] = text
        audio_path = ""
        if how == "file":
            p = TMP / f"{hid}.wav"
            p.write_bytes(data)
            audio_path = str(p)
        elif how == "http":
            console.clips[hid] = data
            audio_path = f"2026-11-07/{hid}.wav"          # relative and not on disk: fetched over HTTP
        end = now - ago
        console.hits.append({"id": hid, "start": end - secs, "end": end, "duration": secs, "channel": n,
                             "name": name, "freq": 461.0, "mod": "FM", "peak_rssi": 600, "mean_rssi": 500,
                             "audio_path": audio_path, "quality": {"class": quality, "confidence": 0.8},
                             "transcript": transcript, "asr_conf": asr, "tags": None, "incident": None})
        return hid

    async def fake_stt(chunk):
        key = round(len(chunk) / 16000, 2)
        calls["stt"].append(key)
        return texts.get(key, "")

    async def fake_deliver(text, tier, key="", subject="", written=""):
        calls["deliver"].append({"text": text, "tier": tier, "key": key, "written": written})
        return {"delivered": "telegram", "tier": tier, "why": "he is not at the machine",
                "message_id": 7000 + len(calls["deliver"])}

    calls["edit"] = []

    async def fake_edit(message_id, text):
        calls["edit"].append((message_id, text))
        return True

    async def fake_summary(inc_, urgent):
        calls["summ"].append(urgent)
        return None                       # the rules' own words

    async def fake_clip(path, caption):
        calls["clip"].append((path, Path(path).exists(), caption))
        return True

    desk = SD.ScannerDesk()
    desk.client = ConsoleClient(console.url)
    desk.transcriber, desk.deliverer, desk.summariser, desk.clip_sender = fake_stt, fake_deliver, fake_summary, fake_clip
    desk.editor = fake_edit
    desk.REFRESH_GAP_S = 0.0                     # the gate does not wait twenty seconds between edits
    desk.voice_busy = lambda: False
    desk.quiet_hours = lambda: False
    desk.after_id = BASE

    h1 = plant(1, "Framnghm FD", DISPATCH[0][0], 6.01)
    h2 = plant(2, "Newton PD", "this must never be transcribed", 4.02, quality="digital")
    h3 = plant(3, "Natick PD", "nor this, it is half an hour old", 3.03, ago=1800)
    h4 = plant(4, "Sudbury FD", "Tower 1 respond to 300 Boston Post Road for an odor of gas inside the building.",
               5.04, how="http")
    h5 = plant(5, "Waltham PD", "", 2.05, transcript="Caller reports a disturbance at the Moody Street bridge, "
                                                     "two males fighting.", asr=0.8)
    delay = await desk.tick()
    check("the console answers and the desk follows its feed", desk.up is True and console.feed_calls == 1,
          f"up={desk.up} feed={console.feed_calls}")
    check("every request carried the client header", console.refused == 0, console.refused)
    check("the poll keeps its one-second rhythm", 0.5 <= delay <= 1.5, delay)
    check("the cursor moved past everything and was saved",
          desk.after_id == h5 and (volatile.get(SD.CURSOR_KEY) or {}).get("value", {}).get("after_id") == h5,
          (desk.after_id, volatile.get(SD.CURSOR_KEY)))
    check("every transmission is logged, the old one included", all(S.hit(h) for h in (h1, h2, h3, h4, h5)))
    queued = [r["id"] for r in desk._queue]
    check("only fresh analog clips with audio wait for the recogniser", queued == [h1, h4], queued)
    while desk._queue:
        await desk._transcribe_one(desk._queue.popleft())
    await desk.settle()
    check("the digital channel and the half-hour-old clip never reached it",
          4.02 not in calls["stt"] and 3.03 not in calls["stt"], calls["stt"])
    check("a clip on disk and a clip over HTTP were both heard", 6.01 in calls["stt"] and 5.04 in calls["stt"],
          calls["stt"])
    ann = {a["id"]: a for a in console.annotations}
    check("the words went back to the console with tags and the incident",
          h1 in ann and ann[h1]["transcript"] == DISPATCH[0][0] and "smoke_showing" in ann[h1]["tags"]
          and str(ann[h1]["incident"] or "").startswith("JARVIS #") and isinstance(ann[h1]["asr_conf"], float),
          ann.get(h1))
    check("...a transcript the console already had was not re-posted", h5 not in ann and h2 not in ann, list(ann))
    fire = S.incident(S.hit(h1)["incident_id"])
    check("the fire became an incident with its street, units and distance",
          fire and fire["town"] == "Framingham" and fire["address"] == "45 Union Ave"
          and fire["units"] == ["Engine 3", "Ladder 1"] and fire["miles"] is not None, fire)
    gas = S.incident(S.hit(h4)["incident_id"] or 0)
    loud = lambda: [c for c in calls["deliver"] if c["key"].startswith("scanner:")]
    quiet_feed = lambda: [c for c in calls["deliver"] if c["key"].startswith("scanner-fyi:")]
    check("the gas leak went to the quiet feed, not as an alert",
          gas and gas["tier"] == SD.FYI and "gas leak" in gas["decided"], gas)
    g = [c for c in quiet_feed() if c["key"] == f"scanner-fyi:{gas['id'] if gas else 0}"]
    check("...once, through delivery's ALERT route under its own key",
          len(g) == 1 and g[0]["tier"] == ALERT, quiet_feed())
    check("...said in plain words with the street and the distance - and as unconfirmed, being one transmission",
          g and g[0]["text"].startswith("From the scanner, unconfirmed: a gas leak at 300 Boston Post Road in Sudbury")
          and "mile" in g[0]["text"] and "Unconfirmed: a gas leak" in g[0]["written"], g[:1])
    check("...and written for the phone: town and distance, no tier shouting, the words he can judge",
          g and g[0]["written"].startswith("SCANNER - Sudbury (") and "URGENT" not in g[0]["written"]
          and "odor of gas" in g[0]["written"], g[:1])
    fire_line = [c for c in quiet_feed() if c["key"] == f"scanner-fyi:{fire['id']}"]
    check("the fire, heard ONCE, went out as a silent 'Unconfirmed' line - nothing sounded",
          not loud() and len(fire_line) == 1 and fire_line[0]["text"].startswith("From the scanner, unconfirmed:")
          and "Unconfirmed: a structure fire with smoke showing - 45 Union Ave" in fire_line[0]["written"]
          and S.incident(fire["id"])["tier"] == SD.FYI, (calls["deliver"], S.incident(fire["id"])["tier"]))
    check("...and the model was not asked (nothing loud yet; it invents details from weak audio)",
          calls["summ"] == [], calls["summ"])
    clip_of = lambda words: [c for c in calls["clip"] if words in c[2]]
    check("he was away, so the transmission itself went to his phone",
          len(clip_of("smoke showing")) == 1 and clip_of("smoke showing")[0][1], calls["clip"])
    check("...and the quiet calls' recordings went too (the gas leak, the Waltham disturbance)",
          len(clip_of("gas leak")) == 1 and len(clip_of("a disturbance")) == 1, calls["clip"])
    check("...and the clip files were cleaned up", calls["clip"] and not any(Path(c[0]).exists() for c in calls["clip"]))
    want_mid = 7000 + calls["deliver"].index(g[0]) + 1 if g else -1
    check("the message ids came back and were kept, so the call's message can grow",
          gas and S.incident(gas["id"])["msg_id"] == want_mid and S.incident(fire["id"])["msg_id"] > 7000,
          (S.incident(gas["id"])["msg_id"], want_mid, S.incident(fire["id"])["msg_id"]))

    print("\n-- the same fire, escalating, then standing down --")
    h6 = plant(6, "Framnghm FD", "Framingham Command, strike a second alarm for 45 Union Avenue.", 4.06, ago=2)
    await desk.tick()
    while desk._queue:
        await desk._transcribe_one(desk._queue.popleft())
    await desk.settle()
    fire2 = S.incident(fire["id"])
    check("the second alarm merged into the same incident", S.hit(h6)["incident_id"] == fire["id"]
          and fire2["hits"] == 2 and "multiple_alarm" in fire2["types"], fire2)
    check("...recorded as an escalation", fire2["escalations"] == ["it has gone to a second alarm"],
          fire2["escalations"])
    check("a SECOND transmission confirms it: now the URGENT goes, keyed to the incident",
          len(loud()) == 1 and loud()[0]["tier"] == URGENT and loud()[0]["key"] == f"scanner:{fire['id']}"
          and loud()[0]["text"].startswith("Scanner, sir: a multiple-alarm fire at 45 Union Avenue")
          and "Unconfirmed" not in loud()[0]["written"], loud())
    check("...the model asked without waiting for a quiet moment (URGENT)", calls["summ"] == [True], calls["summ"])
    first_mid = fire_line[0] and 7000 + calls["deliver"].index(fire_line[0]) + 1
    closed = [e for e in calls["edit"] if e[0] == first_mid]
    check("...and the earlier 'Unconfirmed' line is closed: it says the alert follows",
          closed and "Confirmed - the alert follows." in closed[-1][1] and "Unconfirmed" not in closed[-1][1],
          closed[-1:])
    check("...and the recording of the confirming transmission went with the alert",
          len(clip_of("multiple-alarm")) == 1, calls["clip"])
    h7 = plant(7, "Framnghm FD", "Fire is knocked down, units can clear.", 3.07, ago=1)
    await desk.tick()
    while desk._queue:
        await desk._transcribe_one(desk._queue.popleft())
    await desk.settle()
    check("the stand-down closed it", S.incident(fire["id"])["status"] == "over"
          and S.hit(h7)["incident_id"] == fire["id"])
    gas_lines = lambda: [c for c in quiet_feed() if c["key"] == f"scanner-fyi:{gas['id']}"]
    check("...and nothing more was sent", len(loud()) == 1 and len(gas_lines()) == 1, calls["deliver"])
    check("a different street in the same town is a different incident",
          S.incident(S.hit(h4)["incident_id"])["id"] != fire["id"])

    print("\n-- the follow-ups are where the details are --")
    edits_before = len(calls["edit"])
    h8 = plant(8, "Sudbury FD", "Tower 1 on scene, everyone is out, two patients, transporting one to Emerson.", 3.08,
               ago=1)
    await desk.tick()
    while desk._queue:
        await desk._transcribe_one(desk._queue.popleft())
    await desk.settle()
    gas2 = S.incident(gas["id"])
    check("a follow-up with no call in it joins the open call on its channel",
          S.hit(h8)["incident_id"] == gas["id"] and len(gas2["said"]) == 2 and gas2["said"][-1].get("context"), gas2)
    check("...and its facts are kept", {"everyone out", "2 patients", "transported to Emerson"} <= set(gas2["details"]),
          gas2["details"])
    new_edits = calls["edit"][edits_before:]
    check("...and his message for that call is edited (silently) - not a second message",
          new_edits and new_edits[-1][0] == gas2["msg_id"] and "transported to Emerson" in new_edits[-1][1]
          and "Tower 1 on scene" in new_edits[-1][1] and len(gas_lines()) == 1, new_edits[-1:])
    ann8 = next((a for a in console.annotations if a["id"] == h8), {})
    check("...and the console shows which call it belongs to", "context" in (ann8.get("tags") or [])
          and str(ann8.get("incident") or "").startswith(f"JARVIS #{gas['id']}"), ann8)
    await desk._edit(S.incident(gas["id"]))
    check("an edit that would change nothing is not sent", len(calls["edit"]) == len(new_edits) + edits_before,
          len(calls["edit"]))

    S.update_incident(gas["id"], {"summary": "Responders are on scene with no injuries."})
    check("a model summary never reaches the phone text: only what was heard does",
          "no injuries" not in desk.written(S.incident(gas["id"]), SD.FYI), "")
    S.update_incident(gas["id"], {"summary": ""})
    wpd =S.incident(S.hit(h5)["incident_id"] or 0) if S.hit(h5)["incident_id"] else None
    row = {"id": BASE + 30, "ts": now - 2, "end_ts": now - 1, "duration": 2, "name": "Waltham PD",
           "town": "Waltham", "agency": "PD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    got = await desk.on_text(row, "What's your location? Copy that.", 0.9, annotate=False)
    check("on a police channel, chatter with no fact in it joins nothing", got is None, got)
    row = dict(row, id=BASE + 31)
    S.log_hit(row)
    got = await desk.on_text(row, "Two patients, both refusing, units can stay.", 0.9, annotate=False)
    check("...a stated fact joins the one open call there" if wpd else "...(no open Waltham PD call to join)",
          (got == wpd["id"]) if wpd else got is None, (got, wpd and wpd["id"]))
    check("the send was recorded", S.sent_count(SD.FYI, now - 60) >= 1)

    print("\n-- mutual aid, the model's veto, delivery's repeat window --")
    row = {"id": BASE + 20, "ts": now - 3, "end_ts": now - 1, "duration": 5, "name": "Framnghm FD",
           "town": "Framingham", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    iid = await desk.on_text(row, DISPATCH[29][0], 0.9, annotate=False)
    await desk.settle()
    aid = S.incident(iid)
    check("a Framingham engine sent to Natick makes a Natick incident", aid and aid["town"] == "Natick", aid)
    check("...told quietly as unconfirmed (one transmission), not yet at the Natick tier",
          calls["deliver"][-1]["key"] == f"scanner-fyi:{iid}" and "unconfirmed" in calls["deliver"][-1]["text"],
          calls["deliver"][-1])
    row2 = dict(row, id=BASE + 23, ts=now - 2, end_ts=now - 1)
    S.log_hit(row2)
    await desk.on_text(row2, "Engine 2 on scene at 88 Eliot Street, working fire, fire showing.", 0.9, annotate=False)
    await desk.settle()
    check(f"...and at the Natick tier ({near_natick}) once a second transmission confirms it",
          calls["deliver"][-1]["tier"] == near_natick and calls["deliver"][-1]["key"] == f"scanner:{iid}",
          calls["deliver"][-1])

    async def veto(inc_, urgent):
        return {"summary": "A drill at the training tower, nothing real.", "active": False}
    desk.summariser = veto
    row = {"id": BASE + 21, "ts": now - 3, "end_ts": now - 1, "duration": 5, "name": "Waltham FD",
           "town": "Waltham", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    iid = await desk.on_text(row, "Engine 1 respond to 10 Main Street, working fire, fire showing.", 0.9, annotate=False)
    await desk.settle()
    row3 = dict(row, id=BASE + 24, ts=now - 2, end_ts=now - 1)
    S.log_hit(row3)
    await desk.on_text(row3, "Ladder 1 to 10 Main Street, working fire.", 0.9, annotate=False)
    await desk.settle()
    check("a confirmed ALERT the model reads as over is held (no loud message)",
          not [c for c in calls["deliver"] if c["key"] == f"scanner:{iid}"]
          and "over" in S.incident(iid)["decided"], S.incident(iid)["decided"])
    desk.summariser = fake_summary

    held = {"n": 0}

    async def said_recently(text, tier, key="", subject="", written=""):
        held["n"] += 1
        return {"delivered": "nothing", "why": "said recently", "tier": tier}
    desk.deliverer = said_recently
    row = {"id": BASE + 22, "ts": now - 3, "end_ts": now - 1, "duration": 5, "name": "Marlboro PD",
           "town": "Marlborough", "agency": "PD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    iid = await desk.on_text(row, "Report of shots fired in the area of Pleasant Street.", 0.9, annotate=False)
    await desk.settle()
    row4 = dict(row, id=BASE + 25, ts=now - 2, end_ts=now - 1)
    S.log_hit(row4)
    await desk.on_text(row4, "Car 4 responding, shots fired on Pleasant Street.", 0.9, annotate=False)
    await desk.settle()
    shots = S.incident(iid)
    check("a confirmed send held as 'said recently' is not counted as told, and waits",
          held["n"] >= 2 and shots["tier"] == "" and shots["pending"] in (URGENT, ALERT), (held, shots))
    desk.deliverer = fake_deliver
    real_now = desk.now
    desk.now = lambda: real_now() + 2800         # past the 45-minute ALERT repeat window
    await desk._retry_pending()
    await desk.settle()
    desk.now = real_now
    shots = S.incident(iid)
    check("...and goes out once the repeat window has passed", shots["tier"] in (URGENT, ALERT)
          and shots["pending"] == "" and calls["deliver"][-1]["key"] == f"scanner:{iid}", shots)

    print("\n-- a fire channel busier than it ever is, with no words --")
    import datetime as dt
    this_hour = dt.datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)
    for d in range(1, 6):                 # five days of history inside this same clock hour (DST-safe)
        hour_start = (this_hour - dt.timedelta(days=d)).timestamp()
        for k, (name, per_day) in enumerate((("Ashland FD", 1), ("Wayland FD", 30), ("Hudson FD", 1))):
            for j in range(per_day):
                ts = hour_start + 30 + j * 115
                S.log_hit({"id": BASE + 1000 + d * 100 + k * 40 + j, "ts": ts, "end_ts": ts + 3, "duration": 3,
                           "name": name, "town": name.split()[0], "agency": "FD"})
    sent_before = len(calls["deliver"])
    n0 = 5000
    for name in ("Ashland FD", "Wayland FD", "Hudson FD"):
        burst = []
        for j in range(9):
            hid = BASE + n0
            n0 += 1
            ts = now - 540 + j * 60
            burst.append({"id": hid, "start": ts, "end": ts + 4, "duration": 4, "channel": 9, "name": name,
                          "freq": 33.9, "audio_path": "", "quality": {"class": "digital", "confidence": 0.9}})
        desk.after_id = BASE + n0 - 10
        await desk.ingest(burst)
    await desk.settle()
    bursts = {i["channel"]: i for i in S.incidents_since(now - 900) if i["burst"] and not i["types"]}
    check("Ashland FD at many times its normal traffic is a burst", "Ashland FD" in bursts,
          sorted(bursts))
    check("...described with its numbers against its own history",
          "transmissions in 10 minutes" in (bursts.get("Ashland FD") or {}).get("summary", "")
          and "normal for this hour" in (bursts.get("Ashland FD") or {}).get("summary", ""),
          (bursts.get("Ashland FD") or {}).get("summary"))
    check("Wayland FD at its usual busy rate is not", "Wayland FD" not in bursts, sorted(bursts))
    check("Hudson FD's burst (beyond five miles) goes to the quiet feed, not as an alert",
          "Hudson FD" in bursts and bursts["Hudson FD"]["tier"] == SD.FYI, bursts.get("Hudson FD"))
    ashland_near = geo.town("Ashland").miles <= 5.0
    new = [c for c in calls["deliver"][sent_before:] if c["key"].startswith("scanner:")]
    newq = [c for c in calls["deliver"][sent_before:] if c["key"].startswith("scanner-fyi:")]
    check("a burst near him reaches him as an ALERT" if ashland_near else "no burst beyond five miles is an alert",
          (len(new) == 1 and new[0]["tier"] == ALERT and "unusually busy" in new[0]["text"]) if ashland_near
          else not new, new)
    check("...and Hudson's is one quiet line that says it can't make out the words",
          len(newq) == (1 if ashland_near else 2) and "Hudson Fire is unusually busy" in " ".join(c["text"] for c in newq)
          and "can't make out the words" in newq[-1]["text"], newq)

    print("\n-- small calls never swallow big signals --")

    async def burst_on(name: str, first_id: int) -> None:
        hits = []
        for j in range(9):
            ts = now - 500 + j * 50
            hits.append({"id": first_id + j, "start": ts, "end": ts + 3, "duration": 3, "channel": 9,
                         "name": name, "freq": 33.5, "audio_path": "",
                         "quality": {"class": "digital", "confidence": 0.9}})
        desk.after_id = first_id - 1
        await desk.ingest(hits)
        await desk.settle()
    row = {"id": BASE + 40, "ts": now - 30, "end_ts": now - 28, "duration": 2, "name": "Southboro FD",
           "town": "Southborough", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    alarm_id = await desk.on_text(row, "Engine 2 respond to 14 Elm Street for a fire alarm activation.", 0.9,
                                  annotate=False)
    await desk.settle()
    await burst_on("Southboro FD", BASE + 6000)
    sb = [i for i in S.incidents_since(now - 900, "Southborough") if i["burst"] and not i["types"]]
    check("an open fire alarm does not swallow a burst on the same fire channel",
          alarm_id and len(sb) == 1 and sb[0]["id"] != alarm_id and S.incident(alarm_id)["burst"] == 0,
          (alarm_id, [i["id"] for i in sb]))
    await burst_on("Natick FD", BASE + 6100)
    check("...but a working fire already open there explains the traffic and takes it",
          S.incident(aid["id"])["burst"] == 1
          and not [i for i in S.incidents_since(now - 900, "Natick") if i["burst"] and not i["types"]],
          S.incident(aid["id"]))
    row = {"id": BASE + 41, "ts": now - 20, "end_ts": now - 18, "duration": 3, "name": "Framnghm FD",
           "town": "Framingham", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    brush = await desk.on_text(row, "Engine 4 respond for a brush fire behind the high school.", 0.9, annotate=False)
    await desk.settle()
    check("a finished fire does not absorb a new call elsewhere in town",
          brush and brush != fire["id"] and S.incident(fire["id"])["status"] == "over", (brush, fire["id"]))

    print("\n-- what the first live morning taught it (2026-11-07) --")
    # Natick FD's ordinary traffic against a baseline of nothing came within a
    # test mute of his phone as "unusually busy".
    # "Days of history" are days the desk was LISTENING at this hour (this
    # gate planted five above), so a desk on its first day is simulated.
    sent_before = len(calls["deliver"])
    ids_before = {i["id"] for i in S.incidents_since(now - 3600)}
    real_baseline = desk._baseline
    desk._baseline = lambda name, hour, now_, window: (0.0, 0)
    try:
        await burst_on("Sherborn FD", BASE + 6200)
    finally:
        desk._baseline = real_baseline
    fresh = [i for i in S.incidents_since(now - 3600)
             if i["id"] not in ids_before and i["burst"] and i["channel"] == "Sherborn FD"]
    check("a burst on a desk's first day is stored", len(fresh) == 1, fresh)
    check("...and never sent: busy means busier than usual, and usual takes days",
          len(calls["deliver"]) == sent_before, calls["deliver"][sent_before:])
    # "...for the residential fire activation" (0.41) became a structure fire.
    rd = R.read("Number 22 Always for the residential fire activation.", "Wayland")
    check("'residential fire activation' is an alarm call, not a structure fire",
          "structure_fire" not in rd.types and "alarm" in rd.types, rd.types)
    rd = R.read("Engine 3 respond to a residential fire alarm on Main Street", "Wayland")
    check("...and so is 'residential fire alarm'", "structure_fire" not in rd.types, rd.types)
    shaky = {"id": BASE + 42, "ts": now - 15, "end_ts": now - 13, "duration": 3, "name": "Wayland FD",
             "town": "Wayland", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(shaky)
    first = await desk.on_text(shaky, "Engine 1 respond to 9 Pond Road for a structure fire.", 0.45, annotate=False)
    check("one shaky transmission saying 'structure fire' starts nothing", first is None, first)
    again = dict(shaky, id=BASE + 43, ts=now - 5, end_ts=now - 3)
    S.log_hit(again)
    second = await desk.on_text(again, "Ladder 1 on the way to Pond Road, structure fire.", 0.5, annotate=False)
    await desk.settle()
    check("...a second transmission from the same town agreeing does", bool(second), second)
    sure = dict(shaky, id=BASE + 44, ts=now - 2, end_ts=now - 1, town="Hudson", name="Hudson FD")
    S.log_hit(sure)
    clear = await desk.on_text(sure, "Engine 2 respond to 40 Main Street for a structure fire.", 0.9, annotate=False)
    await desk.settle()
    check("a clear transcript is believed on its own", bool(clear), clear)

    print("\n-- what the first live afternoon taught it (2026-11-07) --")
    # Four hours, ~400 transmissions, two crashes and a reported fire built -
    # and not one line reached him. Every real call now gets a quiet one.
    check("the agreed shaky 'structure fire' in Wayland went to the quiet feed",
          second and S.incident(second)["tier"] == SD.FYI, second and S.incident(second))
    check("...and so did the clear one in Hudson", clear and S.incident(clear)["tier"] == SD.FYI,
          clear and S.incident(clear))
    said_rows = (S.incident(clear) or {}).get("said") or [{}]
    check("an incident keeps how clearly each transmission was heard", said_rows[-1].get("conf") == 0.9, said_rows)
    # "Master 3, Fox drive, Charlie, Romeo 213" was a licence plate in the
    # phonetic alphabet; "cut it about one place" was not a street.
    for text, want in (
            ("Where'd you think? This is gonna be a hit and run. Master 3, Fox drive, Charlie, Romeo 213, SCR21.", ""),
            ("Everybody dispatch it in two car three ambulance one. Why do you cut it about one place?", ""),
            ("Engine 2 respond to 14 Elm Street, Natick, for a fire alarm activation.", "14 Elm St"),
            ("engine 2 respond to 14 elm street for a fire alarm", "14 Elm St"),
            ("Hemenway Road, Engine 5 and Forestry 2 respond for a brush fire.", "Hemenway Rd"),
            ("Missing elderly male walked away from 40 Lincoln Street.", "40 Lincoln St"),
            ("Plate is Lincoln David 4 Main Street Adam Boy.", "")):
        got = R.extract_address(text)[0]
        check(f"address in {text[:52]!r} -> {want or 'none'}", got == want, got)
    # an incident named by an older reading is re-read by today's rules
    wl = {"id": BASE + 45, "ts": now - 900, "end_ts": now - 898, "duration": 3, "name": "Wayland FD",
          "town": "Wayland", "agency": "FD", "quality": None, "audio_path": ""}
    old = S.create_incident({
        "first_ts": now - 900, "last_ts": now - 898, "town": "Wayland", "miles": 5.3, "agency": "FD",
        "channel": "Wayland FD", "family": "fire", "kind": "structure_fire", "label": "a reported structure fire",
        "severity": 6, "types": ["structure_fire"], "address": "", "street": "", "roads": [], "units": [],
        "said": [{"ts": wl["ts"], "text": "Number 22 Always for the residential fire activation. No.", "hit": wl["id"],
                  "channel": "Wayland FD"}], "hits": 1, "status": "active"})
    gone = S.create_incident({
        "first_ts": now - 800, "last_ts": now - 798, "town": "Wayland", "miles": 5.3, "agency": "FD",
        "channel": "Wayland FD", "family": "traffic", "kind": "mva", "label": "a crash",
        "severity": 3, "types": ["mva"], "address": "", "street": "", "roads": [], "units": [],
        "said": [{"ts": now - 800, "text": "Okay, copy that, thank you.", "hit": BASE + 46,
                  "channel": "Wayland FD"}], "hits": 1, "status": "active"})
    junk = S.create_incident({
        "first_ts": now - 700, "last_ts": now - 698, "town": "Sudbury", "miles": 5.3, "agency": "FD",
        "channel": "Sudbury FD", "family": "traffic", "kind": "mva", "label": "a crash", "severity": 3,
        "types": ["mva"], "address": "It About One Pl", "street": "it about one pl", "roads": [], "units": [],
        "said": [{"ts": now - 700, "text": "Everybody dispatch it in two car three ambulance one. Two car crash. "
                                           "Why do you cut it about one place?", "hit": BASE + 47,
                  "channel": "Sudbury FD"}], "hits": 1, "status": "over"})
    long_line = ("Where'd you think? We'll do that. This is gonna be a hit and run with just one vehicle. I can give "
                 "you the plating operator when you're ready. Master 3, Fox drive, Charlie, Romeo 213, SCR21. But if "
                 "you just attach the female RO and ATR we have just been out there for a while, sir, all set. "
                 "Copy that, we will stand by on the side of the road until the tow arrives.")[:300]
    plate = S.create_incident({            # closed, so it takes no later test's crash
        "first_ts": now - 650, "last_ts": now - 648, "town": "Wellesley", "miles": 7.8, "agency": "PD",
        "channel": "Wellesley PD", "family": "traffic", "kind": "mva", "label": "a crash", "severity": 3,
        "types": ["mva"], "address": "3 Fox Dr", "street": "fox dr", "roads": [], "units": [],
        "said": [{"ts": now - 650, "text": long_line, "hit": BASE + 48, "channel": "Wellesley PD"}],
        "hits": 1, "status": "over"})
    moved = S.create_incident({            # the Wayland lookout as it was stored at 4:08 PM
        "first_ts": now - 640, "last_ts": now - 638, "town": "Framingham", "miles": 0.4, "agency": "PD",
        "channel": "Wayland PD", "family": "police", "kind": "welfare_check", "label": "a well-being check",
        "severity": 3, "types": ["welfare_check"], "address": "", "street": "", "roads": [], "units": [],
        "said": [{"ts": now - 640, "hit": BASE + 49, "channel": "Wayland PD",
                  "text": "Okay, do a well being check. 72 year old female left about four hours ago, on foot. "
                          "They would like her transport it back to Framingham."}], "hits": 1, "status": "over"})
    aid_kept = S.create_incident({         # a mutual-aid call keeps its town
        "first_ts": now - 630, "last_ts": now - 628, "town": "Natick", "miles": 4.7, "agency": "FD",
        "channel": "Wayland FD", "family": "fire", "kind": "working_fire", "label": "a working fire",
        "severity": 9, "types": ["working_fire"], "address": "", "street": "", "roads": [], "units": [],
        "said": [{"ts": now - 630, "hit": BASE + 50, "channel": "Wayland FD",
                  "text": "Mutual aid to Natick, Engine 1 respond for a working fire."}], "hits": 1, "status": "over"})
    n = desk._rejudge(now - 3600)
    check("the re-read puts a call back in its channel's town when it was moved by a passing mention",
          S.incident(moved)["town"] == "Wayland", S.incident(moved)["town"])
    check("...and a mutual-aid call keeps the town it was sent to", S.incident(aid_kept)["town"] == "Natick",
          S.incident(aid_kept)["town"])
    p_ = S.incident(plate)
    check("a call whose lines were cut short still loses an address read from a spelled-out plate",
          len(long_line) == 300 and p_ and p_["address"] == "" and p_["types"] == ["mva"], p_)
    j = S.incident(junk)
    check("this morning's 'It About One Place' is dropped, and the units it missed are read",
          j and j["address"] == "" and j["units"] == ["Car 3", "Ambulance 1"] and "2 vehicles" in j["details"], j)
    o, g2 = S.incident(old), S.incident(gone)
    check("this morning's misread 'structure fire' is re-read as the fire alarm it was",
          o and o["types"] == ["alarm"] and o["label"] == "a fire alarm" and o["kind"] == "alarm", o)
    check("...an incident today's rules see no call in is closed and drops out of his answers",
          g2 and g2["types"] == [] and g2["status"] == "over", g2)
    check("...and incidents the rules still agree with are left alone",
          n >= 2 and S.incident(fire["id"])["types"] == fire2["types"], (n, S.incident(fire["id"])["types"]))

    print("\n-- the details a call carries (his words: 'I need more details') --")
    for text, want in (
            ("For a two-car MPA, a trunk, and a Toyota wrap war.", ["2 vehicles"]),
            ("This is gonna be a hit and run with just one vehicle.", ["hit and run", "1 vehicle"]),
            ("Car 3 on scene, two patients, one being transported to MetroWest, airbags deployed.",
             ["2 patients", "airbags deployed", "units on scene", "transported to MetroWest"]),
            ("No injuries, two vehicles, fluids on the roadway.", ["2 vehicles", "no injuries", "fluids on the road"]),
            ("Minor injuries, no entrapment, units can clear.", ["minor injuries"]),
            ("Engine 2 on location, smoke showing from the second floor, everyone is out.",
             ["smoke showing", "everyone out", "units on scene"]),
            ("No smoke or fire showing, alarm was burnt food.", []),
            ("Patient refused transport, units clearing.", ["a patient refused transport"]),
            ("Two car crash on the Mass Pike westbound near exit 117, all lanes blocked.", ["2 vehicles", "road blocked"]),
            ("Everybody dispatch it in two car three ambulance one.", [])):
        got = R.details(text)
        check(f"details of {text[:50]!r}", got == want, got)
    # four Wellesley PD crash reports in two hours were one incident (2026-11-07)
    wp = {"id": BASE + 50, "ts": now - 3000, "end_ts": now - 2998, "duration": 2, "name": "Wellesley PD",
          "town": "Wellesley", "agency": "PD", "quality": None, "audio_path": ""}
    S.log_hit(wp)
    c1 = await desk.on_text(wp, "Two car MVA at the light, units respond.", 0.9, annotate=False)
    wp2 = dict(wp, id=BASE + 51, ts=now - 2950, end_ts=now - 2948)
    S.log_hit(wp2)
    c2 = await desk.on_text(wp2, "Car 2 respond for another motor vehicle accident, Washington Street.", 0.9,
                            annotate=False)
    wp3 = dict(wp, id=BASE + 52, ts=now - 120, end_ts=now - 118)
    S.log_hit(wp3)
    c3 = await desk.on_text(wp3, "Report of a crash, one vehicle into a pole.", 0.9, annotate=False)
    await desk.settle()
    check("'another motor vehicle accident' is a second crash, not the first one again",
          c1 and c2 and c1 != c2, (c1, c2))
    check("...and a crash reported 48 minutes after the last word about one is a new call",
          c3 and c3 not in (c1, c2), (c1, c2, c3))
    got = R.extract_units("Everybody dispatch it in two car three ambulance one. Engine 2 respond.")
    check("units said as words are read ('car three, ambulance one')", got == ["Car 3", "Ambulance 1", "Engine 2"], got)
    import sqlite3
    old_db = sqlite3.connect(":memory:")
    old_db.execute("CREATE TABLE scanner_incidents (id INTEGER PRIMARY KEY, first_ts REAL, last_ts REAL)")
    added = S._migrate(old_db)
    cols = {r[1] for r in old_db.execute("PRAGMA table_info(scanner_incidents)")}
    check("an install from before today gets the new columns, once",
          added == ["msg_id", "details", "edited_ts"] and {"msg_id", "details", "edited_ts"} <= cols
          and S._migrate(old_db) == [], (added, cols))
    import remote_telegram as RT
    sent_api: list = []
    tg_obj = RT.telegram

    async def fake_api(method, http_timeout=30, _token=None, **params):
        sent_api.append((method, params))
        return {"message_id": 4242} if method == "sendMessage" else True
    real_api, real_remote = tg_obj._api, dict(config.data.get("remote") or {})
    tg_obj._api = fake_api
    config.data.setdefault("remote", {})["telegram_chat_id"] = 123
    try:
        ok = await tg_obj._send("SCANNER - Sudbury (5 mi)", silent=True)
        check("a sent message's id is kept for whoever sent it", ok and tg_obj.last_sent_id == 4242,
              tg_obj.last_sent_id)
        ok = await tg_obj.edit_text(4242, "SCANNER - Sudbury (5 mi)\nUnits: Car 3")
        check("...and the message can be edited in place",
              ok and sent_api[-1][0] == "editMessageText" and sent_api[-1][1]["message_id"] == 4242
              and "Car 3" in sent_api[-1][1]["text"], sent_api[-1:])
        import delivery as DV
        real_present, real_tg_mod = DV.is_present, RT.telegram

        class TG:
            last_sent_id = None

            async def send_proactive(self, text, tier="notable", subject="", silent=False, buttons=None):
                self.last_sent_id = 9191
                self.silent = silent
                return True
        fake_tg = TG()
        DV.is_present = lambda: False
        RT.telegram = fake_tg
        try:
            r = await DV.delivery._deliver("From the scanner: a crash in Sudbury.", ALERT,
                                           key=f"scanner-fyi:gate-{time.time()}", subject="scanner-fyi: a crash - Sudbury",
                                           written="SCANNER - Sudbury (5 mi)")
        finally:
            DV.is_present, RT.telegram = real_present, real_tg_mod
        check("delivery hands back the message id, and the quiet line went silently",
              r.get("delivered") == "telegram" and r.get("message_id") == 9191 and fake_tg.silent is True, r)
    finally:
        tg_obj._api = real_api
        config.data["remote"] = real_remote
    src = Path(RT.__file__).read_text(encoding="utf-8")
    seg = src[src.index("async def send_voice_clip"):src.index("async def _poll_loop")]
    check("the recording can go silently, threaded under its message - even as the fallback document",
          'data["disable_notification"] = "true"' in seg and 'data["reply_parameters"] = _reply_parameters(reply_to)' in seg
          and "silent=silent" in seg and "reply_to=reply_to)" in seg, "")
    rp = json.loads(RT._reply_parameters(4242))
    check("...and a deleted parent costs only the threading, never the recording",
          rp == {"message_id": 4242, "allow_sending_without_reply": True}, rp)

    print("\n-- what the code review found (2026-11-07) --")
    for text, want in (
            ("Two patients, non-life-threatening injuries, transporting one to MetroWest.",
             ["non-life-threatening injuries", "2 patients", "transported to MetroWest"]),
            ("Two vehicles, no serious injuries, Route 9 eastbound.", ["2 vehicles"]),
            ("Driver is not seriously injured.", []),
            ("Command to dispatch, not everyone is out, possible occupant on the second floor.", []),
            ("Command, is everyone out of the building?", []),
            ("Car 4, subject is refusing to leave the store.", []),
            ("Negative transport, patient signed off.", []),
            ("No transport needed, units can clear.", []),
            ("Engine 1 respond for a fire in a detached two car garage at 12 Elm Street.", []),
            ("Ladder 2 car fire Route 9 westbound.", []),
            ("No airbag deployment, minor damage.", ["minor damage"]),
            ("Airbags did not deploy.", []),
            ("Patient is alert, giving fluids en route to MetroWest.", ["transported to MetroWest"]),
            ("CPR not in progress, obvious death.", []),
            ("Car 3, not on scene yet, still en route.", []),
            ("Medic 2 patient is conscious and breathing.", ["conscious and breathing"]),
            ("No injuries, two vehicles, tow requested.", ["2 vehicles", "no injuries", "tow requested"]),
            ("Milford ambulance transporting to Leonard Morse.", ["transported to Leonard Morse"])):
        got = R.details(text)
        check(f"review: details of {text[:48]!r}", got == want, got)
    for text, want in (
            ("Car 3 the caller states Union Avenue, two cars blocking.", "Union Ave"),
            ("Caller is John Young at 45 Union Avenue, motor vehicle accident.", "45 Union Ave"),
            ("Car 2 for a lift assist Pleasant Street.", "Pleasant St"),
            ("Engine 2, medical, Main St. and Concord St.", "Main St and Concord St"),
            ("Master 3, Fox drive, Charlie, Romeo 213.", "")):
        got = R.extract_address(text)[0]
        check(f"review: address in {text[:48]!r} -> {want or 'none'}", got == want, got)

    rsent, redits, conc = [], [], {"now": 0, "max": 0}
    rdesk = SD.ScannerDesk()
    rdesk.quiet_hours = lambda: False
    rdesk.REFRESH_GAP_S = 0.0
    rdesk.summariser = fake_summary
    rdesk.clip_sender = fake_clip

    async def slow_deliver(text, tier, key="", subject="", written=""):
        await asyncio.sleep(0.3)                  # a real Telegram round trip
        rsent.append((tier, key, written))
        return {"delivered": "telegram", "tier": tier, "message_id": 8000 + len(rsent), "sound": tier == URGENT}

    async def slow_edit(mid, text):
        conc["now"] += 1
        conc["max"] = max(conc["max"], conc["now"])
        await asyncio.sleep(0.2)
        conc["now"] -= 1
        redits.append((mid, text))
        return True
    rdesk.deliverer, rdesk.editor = slow_deliver, slow_edit

    def rrow(n, name, town, agency, ago):
        r = {"id": BASE + 7000 + n, "ts": now - ago, "end_ts": now - ago + 2, "duration": 2, "name": name,
             "town": town, "agency": agency, "quality": None, "audio_path": ""}
        S.log_hit(r)
        return r
    a1 = await rdesk.on_text(rrow(1, "Hopkinton FD", "Hopkinton", "FD", 300),
                             "Hopkinton, motor vehicle accident, Main Street.", 0.45, annotate=False)
    await rdesk.settle()
    a2 = await rdesk.on_text(rrow(2, "Hopkinton FD", "Hopkinton", "FD", 280),
                             "Car 3, motor vehicle accident on Main Street, two cars.", 0.5, annotate=False)
    await rdesk.settle()
    check("review: a shaky crash that a second transmission confirms is sent (it was never asked again)",
          a1 == a2 and S.incident(a1)["tier"] == SD.FYI and len(rsent) == 1, (S.incident(a1), rsent))
    rsent.clear()
    b1 = await rdesk.on_text(rrow(3, "Weston FD", "Weston", "FD", 200),
                             "Engine 1 respond to 12 Elm Street for a gas leak, odor of gas in the basement.", 0.9,
                             annotate=False)
    await asyncio.sleep(0.1)                      # the line is being sent
    await rdesk.on_text(rrow(4, "Weston FD", "Weston", "FD", 190),
                        "Engine 1 on scene, two patients, transporting one to MetroWest.", 0.9, annotate=False)
    await rdesk.settle()
    check("review: a follow-up heard while the line was being sent still reaches his message",
          len(rsent) == 1 and redits and "MetroWest" in redits[-1][1] and redits[-1][0] == S.incident(b1)["msg_id"],
          (rsent, redits[-1:]))
    check("review: one call's message is never edited twice at once", conc["max"] == 1, conc)
    rsent.clear()
    c1 = await rdesk.on_text(rrow(5, "Dover FD", "Dover", "FD", 150),
                             "Engine 1, Ladder 1 respond to 45 Main Street for a reported structure fire, smoke showing.",
                             0.9, annotate=False)
    await rdesk.settle()
    c2 = await rdesk.on_text(rrow(6, "Dover FD", "Dover", "FD", 140),
                             "Command, dispatch, we're getting another report of heavy smoke from the roof.", 0.9,
                             annotate=False)
    await rdesk.settle()
    check("review: 'another report of heavy smoke' is the same fire - one incident, one alert",
          c1 == c2 and len([s for s in rsent if s[1].startswith("scanner:")]) == 1, (c1, c2, rsent))
    d1 = await rdesk.on_text(rrow(7, "Holliston PD", "Holliston", "PD", 120),
                             "Two car crash by the mall, no injuries.", 0.9, annotate=False)
    await rdesk.settle()
    d2 = await rdesk.on_text(rrow(8, "Holliston PD", "Holliston", "PD", 110),
                             "Car 12 on location with the lockout at 30 Central Street.", 0.9, annotate=False)
    await rdesk.settle()
    check("review: a police unit's status about another call joins nothing and takes no address",
          d2 is None and S.incident(d1)["address"] == "", (d2, S.incident(d1)["address"]))
    old_fire = S.create_incident({
        "first_ts": now - 3000, "last_ts": now - 2000, "town": "Maynard", "miles": 9.0, "agency": "FD",
        "channel": "Maynard FD", "family": "fire", "kind": "working_fire", "label": "a working fire", "severity": 9,
        "types": ["working_fire"], "address": "9 Maple St", "street": "maple st", "roads": [], "units": [],
        "said": [{"ts": now - 2100 + k, "text": "Units can clear, fire is out, returning.", "hit": BASE + 7100 + k,
                  "channel": "Maynard FD"} for k in range(6)], "hits": 14, "status": "over"})
    rdesk._rejudge(now - 3600)
    of = S.incident(old_fire)
    check("review: a call kept by an older build (six of fourteen lines) is not 'closed' by the re-read",
          of["types"] == ["working_fire"] and of["address"] == "9 Maple St", of)
    seen_clip = {}

    async def capture_clip(inc_, row_, silent=False, reply_to=None, kind=""):
        seen_clip.update(silent=silent, reply_to=reply_to, kind=kind)
    rdesk._send_clip = capture_clip

    async def quiet_urgent(text, tier, key="", subject="", written=""):
        return {"delivered": "telegram", "tier": tier, "message_id": 8800, "sound": False}
    rdesk.deliverer = quiet_urgent
    caps_before = config.data["scanner"].get("max_per_day")
    config.data["scanner"]["max_per_day"] = {"urgent": 99, "alert": 99, "fyi": 99}
    try:
        trig = rrow(9, "Framingham EMS", "Framingham", "EMS", 60)
        hot = S.create_incident({
            "first_ts": now - 60, "last_ts": now - 58, "town": "Framingham", "miles": 0.4, "agency": "EMS",
            "channel": "Framingham EMS", "family": "fire", "kind": "working_fire", "label": "a working fire",
            "severity": 9, "types": ["working_fire"], "address": "7 Ash St", "street": "ash st", "roads": [],
            "units": [], "said": [{"ts": now - 60, "text": "Working fire, 7 Ash Street.", "conf": 0.9},
                                  {"ts": now - 50, "text": "Engine 2 on scene, working fire.", "conf": 0.9}],
            "hits": 1, "status": "active"})
        await rdesk._consider(hot, trig, "")
    finally:
        if caps_before is None:
            config.data["scanner"].pop("max_per_day", None)
        else:
            config.data["scanner"]["max_per_day"] = caps_before
    check("review: an URGENT that went silently (sound budget spent) sends its recording silently too",
          S.incident(hot)["tier"] == URGENT and seen_clip.get("silent") is True
          and seen_clip.get("reply_to") == 8800, (S.incident(hot)["tier"], seen_clip))
    long_inc = {"town": "Weston", "miles": 9.0, "types": ["mva"], "label": "a crash", "channel": "Weston FD",
                "first_ts": now - 600, "last_ts": now, "hits": 10, "units": [], "details": [], "address": "",
                "said": [{"ts": now - 600 + k, "text": ("word " * 80).strip()} for k in range(10)]}
    check("review: ten full lines fit one Telegram message as it is",
          len(rdesk.written(long_inc, SD.FYI)) <= 3900)
    rdesk.MAX_WRITTEN = 1200                    # the safety net itself, exercised
    lw = rdesk.written(long_inc, SD.FYI)
    rdesk.MAX_WRITTEN = SD.ScannerDesk.MAX_WRITTEN
    check("...and past the limit the oldest lines give way, the header and the footer stay",
          len(lw) <= 1200 and lw.startswith("SCANNER - Weston") and lw.count('  "') < 10
          and lw.splitlines()[-1].startswith("First heard"), (len(lw), lw.count('  "')))
    import delivery as DV2
    saved = (list(DV2.delivery._sent), list(DV2.delivery._quiet_sent))
    import notify_policy as NP2
    qcap = int(config.get("proactive", "max_quiet_per_hour", default=NP2.DEFAULT_QUIET_PER_HOUR))
    try:
        DV2.delivery._sent.clear()
        DV2.delivery._quiet_sent[:] = [time.time()] * qcap
        check("review: the quiet feed has its own hourly ceiling...",
              DV2.delivery.has_budget(ALERT, "scanner_fyi") is False, qcap)
        check("...which never spends the room his reminders need", DV2.delivery.has_budget(ALERT) is True
              and DV2.delivery.has_budget(ALERT, "reminder") is True)
        DV2.delivery.note_sent("scanner_fyi")
        check("...and a quiet call's recording is counted there, not in the shared ceiling",
              len(DV2.delivery._sent) == 0 and len(DV2.delivery._quiet_sent) == qcap + 1)
    finally:
        DV2.delivery._sent[:], DV2.delivery._quiet_sent[:] = saved
    import remote_telegram as RT2
    api_calls: list = []

    async def api2(method, http_timeout=30, _token=None, **params):
        api_calls.append((method, params))
        return True
    real_api2, real_remote2 = RT2.telegram._api, dict(config.data.get("remote") or {})
    RT2.telegram._api = api2
    config.data.setdefault("remote", {})["telegram_chat_id"] = 123
    RT2.telegram._urgent["gate1234"] = {"text": "x", "at": time.time(), "sent": 1, "mid": 5555}
    try:
        await RT2.telegram.edit_text(5555, "SCANNER - URGENT - Framingham (home)\nUnits: Engine 2")
        kb = (api_calls[-1][1].get("reply_markup") or {}).get("inline_keyboard") or [[{}]]
        check("review: editing an unanswered URGENT keeps its 'Got it' button",
              kb[0][0].get("callback_data") == "ack:gate1234", api_calls[-1:])
        await RT2.telegram.edit_text(6666, "SCANNER - Weston (9 mi)")
        check("...and a quiet call's edit carries no buttons", "reply_markup" not in api_calls[-1][1], api_calls[-1:])
    finally:
        RT2.telegram._urgent.pop("gate1234", None)
        RT2.telegram._api = real_api2
        config.data["remote"] = real_remote2

    print("\n-- one line must say something about its call (2026-11-07, Natick 3:50 PM) --")
    for text, want in (
            ("Anyone, A1. Why do we have a crash be on anyone A1? I need a crash. I need my", False),
            ("This is the crash. I could have an OS going over the uh T should be shoot to both individuals.", False),
            ("For a two-car MPA, a trunk, and a Toyota wrap war.", True),
            ("Everybody dispatch it in two car three ambulance one.", True),
            ("46 Hey Joe at the back, for another motor vehicle accident.", True),
            ("Responsive 10, New Hampshire out for well-being checked.", True),
            ("RP wants to report her phone being stolen out of a car.", True),
            ("Engine 2 respond to 14 Elm Street for a fire alarm activation.", True)):
        got = R.anchored(text)
        check(f"{'anchored' if want else 'not anchored'}: {text[:50]!r}", got is want, got)
    rq = R.read("Command, do we have a structure fire on Elm Street?", "Natick")
    check("a kind named only in a question is not a report of one", "structure_fire" not in rq.types, rq.types)
    rq = R.read("Why do we have a crash be on anyone A1? I need a crash.", "Natick")
    check("...but the same word in a statement still is", "mva" in rq.types, rq.types)

    print("\n-- the Wayland lookout (4:08 PM, 2026-11-07) --")
    # Sent as "a well-being check here in Framingham. A patient transported."
    t891 = ("Okay, do a well being check. Uh we're number 10, then um apartment number thirteen. This was called in "
            "by Metro Cramium Hospital. Uh this party was transported there last night from us at approximately 1145 "
            "after we received a call. 72 year old female left off about four to five hours ago. As far as we know, "
            "on foot. Last thing wearing a black hoodie or sweatshirt, blonde short hair. Even that she is there, uh "
            "they would like her transport it back to Framingham like I said. Copy.")
    r891 = R.read(t891, "Wayland")
    check("a town named as a destination ('back to Framingham') does not move the call", r891.towns == [], r891.towns)
    check("...a description read out is police looking for someone", "lookout" in r891.types
          and r891.label == "police looking for someone", (r891.types, r891.label))
    d891 = R.details(t891)
    check("...with who: age, clothing, hair, on foot", d891 == ["72-year-old female", "wearing a black hoodie",
                                                                "blonde short hair", "on foot"], d891)
    check("...and 'transported there last night' or 'would like her transport' is no transport now",
          not any("transport" in d for d in d891), d891)
    for text, want in (("Car 3 responding to Natick for the crash on Route 9.", ["Natick"]),
                       ("We have a crash in Natick at the mall.", ["Natick"]),
                       ("Mutual aid to Natick, Engine 2 respond to 88 Eliot Street for a working fire.", ["Natick"]),
                       ("Patient wants to go back to Framingham.", []),
                       ("Units coming from Natick, crash on Route 9.", [])):
        got = R.read(text, "Wayland").towns
        check(f"town of {text[:46]!r} -> {want or 'the channel town'}", got == want, got)
    rw = R.read("I just went in and walked out of the parking lot. Can you check?", "Wellesley")
    check("'walked out of the parking lot' is not a missing person", "missing_person" not in rw.types, rw.types)
    th = await desk.on_text(rrow(20, "Wellesley PD", "Wellesley", "PD", 40),
                            "RP wants to report her phone being stolen out of a car.", 0.9, annotate=False)
    mp = await desk.on_text(rrow(21, "Wellesley PD", "Wellesley", "PD", 30),
                            "Units be advised, report of a missing person, elderly male eloped from Elm Street.", 0.9,
                            annotate=False)
    await desk.settle()
    check("a theft and a missing person ten minutes apart on one police channel are two calls",
          th and mp and th != mp and S.incident(th)["label"] == "a theft report", (th, mp, S.incident(th)["label"]))
    t911 = ("Oh Number five, we were clean. The only female uh who's calling. Um is vomiting and severe pain. "
            "Uh she broke her arm yesterday, had said it off so but she fell again on a day.")
    check("'who's calling' frames a call (the 4:13 PM Wayland fall)", R.anchored(t911), R.read(t911, "Wayland").types)

    print("\n-- his medical and police calls, his night (his answers, 2026-11-07) --")
    for text, want in (
            ("RP wants to report her phone being stolen out of a car.", "larceny"),
            ("Shoplifter in custody at the Target, Worcester Road.", "larceny"),
            ("Report of a suspicious male checking car doors on Elm Street.", "suspicious"),
            ("Car 4, motor vehicle stop, Route 9 at Speen Street.", "traffic_stop"),
            ("Can you do a well-being check at 12 Oak Street, elderly resident not answering.", "welfare_check"),
            ("Engine 2, Rescue 1 for an unresponsive male in the parking lot.", "unconscious"),
            ("Respond to 40 Main Street for a 70 year old with difficulty breathing.", "breathing"),
            ("Ambulance 1 for chest pains, 58 year old male.", "chest_pain"),
            ("Possible stroke, slurred speech, 14 Pine Road.", "stroke"),
            ("Child having a seizure at the playground.", "seizure"),
            ("Elderly female fell in the bathroom, lift assist.", "fall"),
            ("Engine 1 for a medical call, abdominal pain.", "medical")):
        rd = R.read(text, "Natick")
        check(f"read as {want}: {text[:46]!r}", want in rd.types and not rd.danger, rd.types)
    for text, nope in (("Patient is not unconscious, alert and talking.", "unconscious"),
                       ("Nothing stolen, door was left open.", "larceny"),
                       ("No chest pains, just a fall.", "chest_pain"),
                       ("Fire alarm, the fall alarm on the third floor.", "fall")):
        rd = R.read(text, "Natick")
        check(f"not read as {nope}: {text[:46]!r}", nope not in rd.types, rd.types)
    # the morning summary: yesterday's night, planted, summarised once
    # his night is his quiet hours (05:30 end, his call): the summary arrives as it ends
    config.data.setdefault("proactive", {}).update({"quiet_start": "22:30", "quiet_end": "05:30"})
    yday = dt.datetime.fromtimestamp(now) - dt.timedelta(days=1)
    eight = yday.replace(hour=5, minute=30, second=0, microsecond=0)     # the end of his night
    night = eight.timestamp() - 4.5 * 3600                # 1 AM
    S.log_hit({"id": BASE + 7310, "ts": eight.timestamp() - 8 * 3600, "end_ts": eight.timestamp() - 8 * 3600 + 3,
               "duration": 3, "name": "Maynard FD", "town": "Maynard", "agency": "FD"})    # 9:30 PM: his evening
    S.create_incident({"first_ts": eight.timestamp() - 8 * 3600, "last_ts": eight.timestamp() - 8 * 3600 + 60,
                       "town": "Maynard", "miles": 9.0, "agency": "FD", "channel": "Maynard FD", "family": "fire",
                       "kind": "alarm", "label": "a fire alarm", "severity": 2, "types": ["alarm"], "address": "",
                       "street": "", "roads": [], "units": [], "details": [], "said": [], "hits": 1,
                       "status": "over"})
    for k in range(5):
        S.log_hit({"id": BASE + 7300 + k, "ts": night + k * 600, "end_ts": night + k * 600 + 3, "duration": 3,
                   "name": "Maynard FD", "town": "Maynard", "agency": "FD"})
    S.create_incident({"first_ts": night, "last_ts": night + 300, "town": "Maynard", "miles": 9.0,
                       "agency": "FD", "channel": "Maynard FD", "family": "ems", "kind": "chest_pain",
                       "label": "a chest pain call", "severity": 4, "types": ["chest_pain"],
                       "address": "14 Pine Rd", "street": "pine rd", "roads": [], "units": ["Ambulance 1"],
                       "details": ["transported to Emerson"], "said": [], "hits": 2, "status": "over"})
    import volatile
    kept_morning = volatile.get(SD.ScannerDesk.MORNING_KEY)
    msent: list = []

    async def morning_deliver(text, tier, key="", subject="", written=""):
        msent.append((text, tier, key, subject, written))
        return {"delivered": "telegram", "tier": tier}
    mdesk = SD.ScannerDesk()
    mdesk.deliverer = morning_deliver
    try:
        volatile.put(SD.ScannerDesk.MORNING_KEY, {"day": ""}, source="gate")
        early = await mdesk._morning_summary(eight.timestamp() - 600)
        first_ = await mdesk._morning_summary(eight.timestamp() + 300)
        again = await mdesk._morning_summary(eight.timestamp() + 900)
    finally:
        if kept_morning:
            volatile.put(SD.ScannerDesk.MORNING_KEY, kept_morning.get("value") or {}, source="gate")
        else:
            volatile.forget(SD.ScannerDesk.MORNING_KEY)
    check("the morning summary waits for the end of his night (5:30 AM)", early is False)
    check("...goes once as it ends, quietly (the quiet feed's kind) under a key for the day",
          first_ is True and len(msent) == 1 and msent[0][3].startswith("scanner-fyi")
          and msent[0][2] == f"scanner-night:{eight.strftime('%Y-%m-%d')}", msent)
    check("...and never twice in a morning", again is False and len(msent) == 1, len(msent))
    mw = msent[0][4] if msent else ""
    check("...listing the night's calls with the place, the distance and what was heard",
          "SCANNER - overnight, 10:30 PM to 5:30 AM" in mw
          and "A chest pain call - 14 Pine Rd, Maynard (9 mi) - transported to Emerson" in mw
          and msent[0][0].startswith("Overnight on the scanner, sir: a chest pain call in Maynard"), msent[:1])
    check("...and only his night: a 9:30 PM call (his evening, told live) is not in it",
          "fire alarm" not in mw.lower(), mw)
    import delivery as DV3
    spoke: list = []
    real3 = (DV3._in_quiet_hours, DV3.is_present, DV3.delivery._speak, RT2.telegram)

    async def no_speak(text, interrupt):
        spoke.append(text)
        return True

    class TG3:
        last_sent_id = None

        async def send_proactive(self, text, tier="notable", subject="", silent=False, buttons=None):
            self.silent = silent
            return True
    DV3._in_quiet_hours, DV3.is_present, DV3.delivery._speak = (lambda: True), (lambda: True), no_speak
    RT2.telegram = TG3()
    config.data.setdefault("remote", {})["telegram_chat_id"] = 123
    try:
        r3 = await DV3.delivery._deliver("From the scanner: a crash in Sudbury.", ALERT,
                                         key=f"scanner-fyi:night-{time.time()}", subject="scanner-fyi: a crash - Sudbury")
    finally:
        DV3._in_quiet_hours, DV3.is_present, DV3.delivery._speak, RT2.telegram = real3
        config.data["remote"] = real_remote2
    check("at night a quiet call is never spoken, even with him at the PC: it waits on his phone, silent",
          not spoke and r3.get("delivered") == "telegram" and RT2.telegram is real3[3], (spoke, r3))

    print("\n-- the daily cap --")
    cap = int(config.get("scanner", "max_per_day", default={}).get("urgent", 4))
    used = S.sent_count(URGENT, time.time() - 86400)
    for _ in range(max(0, cap - used)):
        S.record_sent(fire["id"], URGENT, "telegram", "gate filler")
    got, why = desk.decide(inc("Framingham", ["shots_fired"]))
    check(f"after {cap} URGENTs in a day the next is held", got is None and "cap" in why, why)

    print("\n-- his questions --")
    console.doctor = None
    a = await desk.answer("health")
    check("'is the scanner working' says up, connected, and that the audio is weak",
          "console is up" in a["spoken"] and "connected" in a["spoken"] and "weak" in a["spoken"]
          and "minus 35" in a["spoken"], a)
    a = await desk.answer("doctor")
    check("the doctor with no report yet says so", "hasn't written a report" in a["spoken"], a)
    console.doctor = {"summary": "Scan list tuned for the evening", "changes": [
        {"action": "locked out", "channel": "Newton PD", "reason": "digital voice"},
        {"action": "raised priority", "channel": "Framnghm FD", "reason": "busiest local fire channel"}]}
    a = await desk.answer("doctor")
    check("the doctor's changes are read out with their reasons",
          "2 changes" in a["spoken"] and "Newton PD" in a["spoken"] and "digital voice" in a["spoken"], a)
    # THE REAL SHAPE (console 1.3.0, read off his live report 2026-11-07):
    # the summary is counts, each change names its channel in before/after.
    # The first reader answered "nothing I can read out" to exactly this.
    ch = lambda n, f: {"index": 1, "name": n, "freq": f, "mod": "AUTO", "tone": 0}
    console.doctor = {"summary": {"observing": 92, "keep": 2, "remove": 11}, "changes": [
        {"at": 1, "kind": "remove", "index": 17, "before": ch("Holdon PD", 460.3875), "after": None,
         "why": "Holden is 22.0 mi from home, outside your 20-mile radius", "backup": "b.json"},
        {"at": 1, "kind": "remove", "index": 23, "before": ch("Leominster PD", 453.325), "after": None,
         "why": "Leominster is 22.5 mi from home, outside your 20-mile radius", "backup": "b.json"},
        {"at": 2, "kind": "add", "index": 451, "before": None, "after": ch("Marlborough PD", 470.1),
         "why": "a must-have town with nothing programmed", "backup": "b.json"},
        {"at": 3, "kind": "level", "index": 0, "before": {"volume": 5}, "after": {"volume": 13},
         "why": "loudest level that does not clip", "backup": None}]}
    a = await desk.answer("doctor")
    s = a["spoken"]
    check("the 1.3.0 report is read: removals with their reason, additions, the volume, the standing",
          "removed 2 channels" in s and "outside your radius" in s and "added 1 channel to the trial bank" in s
          and "Marlborough PD" in s and "volume to 13" in s and "listening to 92 channels" in s, s)
    a = await desk.answer("last")
    check("'what was that call' names the channel and how long ago",
          a["spoken"].startswith("That was ") and "ago" in a["spoken"], a)
    a = await desk.answer("now")
    check("'what's on the scanner' leads with what matters and counts the rest",
          "45 Union Avenue" in a["spoken"] and "calls in all" in a["spoken"], a)
    a = await desk.answer("town", "Sudbury")
    check("'what's happening in Sudbury' gives the incident and the distance",
          a["spoken"].startswith("In Sudbury, ") and "gas leak" in a["spoken"] and "mile" in a["spoken"], a)
    a = await desk.answer("town", "Pittsfield")
    check("a town with no channel on his scanner says so", "not hearing any Pittsfield" in a["spoken"], a)
    a = await desk.answer("summary")
    check("'scanner summary' is the brief line", "calls" in a["spoken"] and "worth knowing" in a["spoken"], a)
    line = SD.brief_line(now - 6 * 3600)
    check("brief_line names the incidents worth knowing", "worth knowing" in line and "45 Union Avenue" in line
          and "over by" in line, line)
    st = desk.status()
    check("status() says the console is up with the day's counts",
          st["console_up"] and st["connected"] and st["hits_24h"] >= 5 and st["audio"]["weak_input"] is True, st)
    st = SD.status()
    check("...and the module-level status() the briefing calls answers too",
          isinstance(st, dict) and "console_up" in st and st["hits_24h"] >= 5, st)

    print("\n-- the reflexes pick the question and the town --")
    from brain.skills import SKILL_BY_NAME, slots_scanner_town
    town_skill = SKILL_BY_NAME["scanner_town"]
    for seed in town_skill.seeds:
        check(f"seed {seed!r} finds its town", bool(slots_scanner_town(seed)), slots_scanner_town(seed))
    for said, want in (("what's happening in natick right now", "Natick"), ("anything going on over in west newton", "Newton"),
                       ("any calls in marlboro tonight", "Marlborough"), ("what's going on in the town of hudson", "Hudson")):
        got = slots_scanner_town(said)
        check(f"{said!r} -> {want}", got == {"town": want}, got)
    for said in ("what's happening in the world", "what's happening in business", "what's going on in ukraine",
                 "what's happening in the market"):
        check(f"{said!r} steps aside for the news", slots_scanner_town(said) is None, slots_scanner_town(said))
    for name in ("scanner_now", "scanner_town", "scanner_last", "scanner_summary", "scanner_health", "scanner_doctor"):
        sk = SKILL_BY_NAME.get(name)
        check(f"{name} runs the internal _scanner tool", sk and sk.tool == "_scanner" and sk.fixed_args.get("ask"))
    from tools.registry import registry
    SD.register_all()
    check("the tool is registered and hidden from the model",
          "_scanner" in registry._tools and not any(s["function"]["name"] == "_scanner" for s in registry.schemas()))

    print("\n-- the console absent, and started at most once per few minutes --")
    exe = TMP / "BC125AT Console.exe"
    exe.write_bytes(b"MZ")
    config.data["scanner"].update({"console_exe": str(exe), "autostart": True, "autostart_every_s": 300})
    sup = SD.ScannerDesk()
    sup.client = ConsoleClient(f"http://127.0.0.1:{closed_port()}")
    clock = [1_000_000.0]
    sup.now = lambda: clock[0]
    spawned: list[str] = []
    running = [False]
    sup.spawner = lambda path: spawned.append(path) or True
    sup.process_running = lambda: running[0]
    d1 = await sup.tick()
    check("an absent console is survived and retried later, not hammered", sup.up is False and d1 >= 5, d1)
    check("...and started once", spawned == [str(exe)], spawned)
    await sup.tick()
    clock[0] += 100
    await sup.tick()
    check("...and not again inside five minutes", len(spawned) == 1, spawned)
    clock[0] += 250
    await sup.tick()
    check("...but again after five minutes", len(spawned) == 2, spawned)
    running[0] = True
    clock[0] += 400
    await sup.tick()
    check("a console already running (his window) is left alone", len(spawned) == 2, spawned)
    running[0] = False
    config.data["scanner"]["autostart"] = False
    clock[0] += 400
    await sup.tick()
    check("autostart off means never", len(spawned) == 2, spawned)
    config.data["scanner"].update({"autostart": True, "console_exe": str(TMP / "missing.exe")})
    clock[0] += 400
    await sup.tick()
    check("a console that is not installed is not started", len(spawned) == 2, spawned)
    config.data["scanner"]["console_exe"] = str(exe)
    clock[0] += 400
    await sup.tick()
    clock[0] += 400
    await sup.tick()
    check("after three failed starts in a row it backs off to hourly", len(spawned) == 3, spawned)
    clock[0] += 3700
    await sup.tick()
    check("...and tries again an hour on", len(spawned) == 4, spawned)
    src = (Path(SD.__file__)).read_text(encoding="utf-8")
    seg = src[src.index("def _spawn_console"):src.index("def _console_process_running")]
    check("the console is started hidden and outside JARVIS's process tree",
          '"cmd.exe", "/d", "/c", "start"' in seg and "CREATE_NO_WINDOW" in seg and "--serve" in seg)
    check("nothing in the desk ever kills it", "terminate(" not in src and "taskkill" not in src.replace(
        "taskkill /T", "").replace("taskkill /F /T", "") and ".kill(" not in src)

    print("\n-- his voice wins the recogniser --")
    from audio.stt import STT
    s = STT()
    s._model, s._engine = object(), "parakeet"
    order: list[int] = []

    def slow_run(model, audio):
        time.sleep(0.15)
        order.append(len(audio))
        return f"n{len(audio)}"
    s._run = slow_run
    fg1 = asyncio.ensure_future(s.transcribe(np.zeros(100, dtype=np.float32)))
    await asyncio.sleep(0.02)
    bg = asyncio.ensure_future(s.transcribe_background(np.zeros(200, dtype=np.float32), max_wait=5))
    await asyncio.sleep(0.05)
    fg2 = asyncio.ensure_future(s.transcribe(np.zeros(300, dtype=np.float32)))
    await asyncio.gather(fg1, bg, fg2)
    check("a turn that arrives while a radio clip waits goes first", order == [100, 300, 200], order)
    check("...and the background call still gets its words", bg.result() == "n200", bg.result())
    s2 = STT()

    def must_not_load():
        raise AssertionError("the desk loaded the model")
    s2._ensure = must_not_load
    got = await s2.transcribe_background(np.zeros(10, dtype=np.float32), max_wait=0.3)
    check("the desk never loads the model itself", got is None)
    busy = {"n": 0}

    def busy_twice():
        busy["n"] += 1
        return busy["n"] <= 2
    desk.voice_busy = busy_twice
    before = len(calls["stt"])
    h9 = plant(9, "Framnghm FD", "Engine 2 respond to 5 Oak Street for a fire alarm activation.", 3.09, ago=1)
    row9 = desk._row(next(h for h in console.hits if h["id"] == h9))
    await desk._transcribe_one(row9)
    check("a clip waits while he is speaking, then is heard", busy["n"] >= 3 and len(calls["stt"]) == before + 1,
          (busy, calls["stt"][before:]))
    desk.voice_busy = lambda: True
    stale = dict(row9, end_ts=time.time() - 3600, ts=time.time() - 3605)
    await desk._transcribe_one(stale)
    check("...and one that waited past ten minutes is dropped, not chewed through", len(calls["stt"]) == before + 1)
    desk.voice_busy = lambda: False
    desk.retry_pause = 0.0
    tries = {"n": 0}

    async def not_ready(chunk):
        tries["n"] += 1
        return None                       # the recogniser not loaded yet (boot)
    desk.transcriber = not_ready
    desk._queue.clear()
    await desk._transcribe_one(row9)
    check("a clip the recogniser could not take yet goes back to the front of the queue",
          tries["n"] == 1 and len(desk._queue) == 1 and desk._queue[0]["id"] == h9, (tries, len(desk._queue)))
    desk._queue.clear()
    desk.transcriber = fake_stt

    print("\n-- a console whose log was reset is followed, not lost --")
    mine = S.last_hit()
    ahead = float(mine.get("end_ts") or mine["ts"]) + 600
    rs = SD.ScannerDesk()
    rs.client = ConsoleClient(console.url)
    rs.health = {"last_hit_ts": ahead}
    rs.after_id = BASE + 100000
    await rs.ingest([], last_id=BASE + 5)
    check("ids that went backwards while the console has newer traffic: follow it from the start",
          rs.after_id == 0, rs.after_id)
    rs.after_id = BASE + 100000
    await rs.ingest([], last_id=BASE + 100000)
    check("a console with nothing past the cursor is not 'reset'", rs.after_id == BASE + 100000, rs.after_id)
    rs.health = {"last_hit_ts": float(mine.get("end_ts") or mine["ts"])}
    await rs.ingest([], last_id=None)
    check("...nor one whose newest is already logged", rs.after_id == BASE + 100000, rs.after_id)
    rs.health = {"last_hit_ts": ahead}
    fake = [time.time()]
    rs.now = lambda: fake[0]
    await rs.ingest([], last_id=None)
    check("with no last_id it waits before deciding", rs.after_id == BASE + 100000, rs.after_id)
    fake[0] += 301
    await rs.ingest([], last_id=None)
    check("...and follows from the start after five minutes behind", rs.after_id == 0, rs.after_id)
    probe = {"id": BASE + 30, "ts": now - 50, "end_ts": now - 48, "duration": 2, "name": "Natick PD",
             "town": "Natick", "agency": "PD"}
    S.log_hit(probe)
    S.set_transcript(BASE + 30, "first words", 0.9)
    S.log_hit(probe)
    check("the same transmission seen twice keeps its words", S.hit(BASE + 30)["transcript"] == "first words")
    S.log_hit(dict(probe, ts=now - 5, end_ts=now - 3, name="Natick FD"))
    got = S.hit(BASE + 30)
    check("a different transmission under an old id replaces it",
          got["name"] == "Natick FD" and got["transcript"] is None, got)

    print("\n-- the phone clip function exists and no gate calls it --")
    import remote_telegram
    check("remote_telegram has send_voice_clip", callable(getattr(remote_telegram.TelegramBridge, "send_voice_clip", None)))


def main() -> int:
    asyncio.run(run())
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
