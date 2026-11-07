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
                 "Southboro FD")
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

    def inc(town, types, roads=(), burst=0, status="active", channel="X"):
        t = geo.town(town) if town else None
        top = max(types, key=lambda k: R.KINDS[k].severity) if types else ""
        return {"town": town, "miles": round(t.miles, 1) if t else None, "types": list(types),
                "label": R.describe(list(types)) or "unusual activity", "roads": list(roads), "burst": burst,
                "status": status, "channel": channel, "family": R.KINDS[top].family if top else "fire"}
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
        ("a plain crash on Elm Street in Natick", inc("Natick", ["mva"]), None),
        ("a gas leak in Framingham", inc("Framingham", ["gas_leak"]), None),
        ("a stabbing in Waltham", inc("Waltham", ["stabbing"]), None),
        ("a fire alarm in Framingham", inc("Framingham", ["alarm"]), None),
        ("a burst on Ashland FD", inc("Ashland", [], burst=1, channel="Ashland FD"),
         ALERT if geo.town("Ashland").miles <= 5 else None),
        ("a burst on Hudson FD", inc("Hudson", [], burst=1, channel="Hudson FD"), None),
        ("a working fire that is over", inc("Framingham", ["working_fire"], status="over"), None),
        ("a channel with no town", inc("", ["working_fire"]), None),
    ]
    for name, i, want in table:
        got, why = desk.decide(i)
        check(f"{name} -> {want or 'stored'}", got == want, f"got {got}: {why}")
    quiet[0] = True
    got, _ = desk.decide(inc("Framingham", ["working_fire"]))
    check("quiet hours: an URGENT in his own town still reaches him", got == URGENT)
    got, why = desk.decide(inc("Natick", ["working_fire"]))
    check("quiet hours: an URGENT one town over waits", got is None and "quiet" in why, why)
    got, why = desk.decide(inc("Wellesley", ["mva"], ["Route 9"]))
    check("quiet hours: an ALERT waits", got is None and "quiet" in why, why)
    quiet[0] = False

    print("\n-- what he hears and reads --")
    i = inc("Framingham", ["smoke_showing", "structure_fire"])
    i.update(address="45 Union Ave", channel="Framnghm FD", said=[{"ts": time.time(), "text": DISPATCH[0][0],
                                                                   "channel": "Framnghm FD"}], summary="")
    spoken, written = desk.compose(i, URGENT)
    check("URGENT speaks the kind, the street in full and the town",
          spoken == "Scanner, sir: a structure fire with smoke showing at 45 Union Avenue here in Framingham.", spoken)
    check("...and the phone gets a header, the place and the words he can judge",
          written.startswith("SCANNER - URGENT - Framingham (home)") and "45 Union Ave" in written
          and "smoke showing" in written.splitlines()[-1], written)
    i2 = inc("Natick", ["entrapment"], ["Route 9"])
    i2.update(address="Route 9 eastbound at Speen St", channel="Natick FD", said=[], summary="")
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
        return {"delivered": "telegram", "tier": tier, "why": "he is not at the machine"}

    async def fake_summary(inc_, urgent):
        calls["summ"].append(urgent)
        return None                       # the rules' own words

    async def fake_clip(path, caption):
        calls["clip"].append((path, Path(path).exists(), caption))
        return True

    desk = SD.ScannerDesk()
    desk.client = ConsoleClient(console.url)
    desk.transcriber, desk.deliverer, desk.summariser, desk.clip_sender = fake_stt, fake_deliver, fake_summary, fake_clip
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
    check("the gas leak was stored, not sent", gas and gas["tier"] == "" and "stored" in gas["decided"], gas)
    check("one URGENT went out, keyed to the incident",
          len(calls["deliver"]) == 1 and calls["deliver"][0]["tier"] == URGENT
          and calls["deliver"][0]["key"] == f"scanner:{fire['id']}", calls["deliver"])
    check("the model was asked without waiting for a quiet moment (URGENT)", calls["summ"] == [True], calls["summ"])
    check("he was away, so the transmission itself went to his phone",
          len(calls["clip"]) == 1 and calls["clip"][0][1], calls["clip"])
    check("...and the clip file was cleaned up", not Path(calls["clip"][0][0]).exists() if calls["clip"] else False)
    check("the send was recorded", S.sent_count(URGENT, now - 60) >= 1)

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
    check("...and said to him as an update, same key",
          len(calls["deliver"]) == 2 and "second alarm" in calls["deliver"][1]["text"]
          and calls["deliver"][1]["key"] == f"scanner:{fire['id']}", calls["deliver"][-1:])
    h7 = plant(7, "Framnghm FD", "Fire is knocked down, units can clear.", 3.07, ago=1)
    await desk.tick()
    while desk._queue:
        await desk._transcribe_one(desk._queue.popleft())
    await desk.settle()
    check("the stand-down closed it", S.incident(fire["id"])["status"] == "over"
          and S.hit(h7)["incident_id"] == fire["id"])
    check("...and nothing more was sent", len(calls["deliver"]) == 2, len(calls["deliver"]))
    check("a different street in the same town is a different incident",
          S.incident(S.hit(h4)["incident_id"])["id"] != fire["id"])

    print("\n-- mutual aid, the model's veto, delivery's repeat window --")
    row = {"id": BASE + 20, "ts": now - 3, "end_ts": now - 1, "duration": 5, "name": "Framnghm FD",
           "town": "Framingham", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    iid = await desk.on_text(row, DISPATCH[29][0], 0.9, annotate=False)
    await desk.settle()
    aid = S.incident(iid)
    check("a Framingham engine sent to Natick makes a Natick incident", aid and aid["town"] == "Natick", aid)
    check(f"...told at the Natick tier ({near_natick})", calls["deliver"][-1]["tier"] == near_natick
          and calls["deliver"][-1]["key"] == f"scanner:{iid}", calls["deliver"][-1])

    async def veto(inc_, urgent):
        return {"summary": "A drill at the training tower, nothing real.", "active": False}
    desk.summariser = veto
    row = {"id": BASE + 21, "ts": now - 3, "end_ts": now - 1, "duration": 5, "name": "Waltham FD",
           "town": "Waltham", "agency": "FD", "quality": None, "audio_path": ""}
    S.log_hit(row)
    before = len(calls["deliver"])
    iid = await desk.on_text(row, "Engine 1 respond to 10 Main Street, working fire, fire showing.", 0.9, annotate=False)
    await desk.settle()
    check("an ALERT the model reads as over is held", len(calls["deliver"]) == before
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
    shots = S.incident(iid)
    check("a send delivery held as 'said recently' is not counted as told, and waits",
          held["n"] == 1 and shots["tier"] == "" and shots["pending"] in (URGENT, ALERT), shots)
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
    check("Hudson FD's burst is stored, not sent (not near him)",
          "Hudson FD" in bursts and bursts["Hudson FD"]["tier"] == "", bursts.get("Hudson FD"))
    ashland_near = geo.town("Ashland").miles <= 5.0
    new = calls["deliver"][sent_before:]
    check("a burst near him reaches him as an ALERT" if ashland_near else "a burst beyond five miles is stored",
          (len(new) == 1 and new[0]["tier"] == ALERT and "unusually busy" in new[0]["text"]) if ashland_near
          else not new, new)

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
