"""Does the shipped build actually do the four things he asked for?

Runs against the RUNNING app in his own session — not the source tree, not a
test double. The unit gates prove the geometry and the sentences; this proves
the frozen bundle loaded its models, that the routing survived being packed,
and above all how LONG the answers take, because "it was buffering" was half
of his complaint and no offline test can measure that.

It earned its place: two of his own phrasings — "open the camera" and "remember
my face" — were broken in a bundle whose entire offline suite was green, and
this is what found them. Run it after every deploy that touches routing.

What it cannot do alone: he has to be in front of the camera for enrollment
and for a finger count to mean anything. So it reports what it sees honestly —
"no hands in frame" is a PASS for an empty chair — and leaves the two
in-person steps to him.

NOT part of the build gate: it needs the app running and a camera, so it is a
live check like news_emergencies_live.py, not a unit test. Run it in HIS
session (%APPDATA% is virtualized in an agent shell):

    schtasks /Create /TN JARVIS_CAMVERIFY /TR "<repo>\\scripts\\camera_live.cmd" ^
             /SC ONCE /ST 23:59 /F  &&  schtasks /Run /TN JARVIS_CAMVERIFY
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

APP = os.path.join(os.environ["APPDATA"], "JARVIS")
TOKEN = open(os.path.join(APP, "session.token"), encoding="utf-8").read().strip()


def find_port():
    """Ask the OS which port the running sidecar is on.

    The Rust core hands it an EPHEMERAL port on every launch (52322 this time),
    so scanning a fixed range finds nothing and a stale port file would be a
    lie. The process is the source of truth.
    """
    import psutil
    for proc in psutil.process_iter(["name", "pid"]):
        if (proc.info["name"] or "").lower() != "jarvis-sidecar.exe":
            continue
        try:
            for c in proc.net_connections(kind="tcp"):
                if c.status == psutil.CONN_LISTEN and c.laddr.ip.startswith("127."):
                    return c.laddr.port
        except Exception:
            continue
    raise SystemExit("jarvis-sidecar.exe is not listening — is the app running?")


BASE = f"http://127.0.0.1:{find_port()}"
print("sidecar:", BASE)


def call(path, body=None, timeout=90):
    req = urllib.request.Request(
        BASE + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Jarvis-Token": TOKEN, "Content-Type": "application/json"},
        method="POST" if body is not None else "GET")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}"), time.time() - t0
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:200]}, time.time() - t0
    except Exception as e:
        return {"error": repr(e)}, time.time() - t0


print("\n=== 1. is he up ===")
h, dt = call("/health")
print(f"  health {dt*1000:.0f} ms  {json.dumps(h)[:160]}")

print("\n=== 2. routing, in the frozen build ===")
# The pairs that have actually collided: camera vs app-launching, face-enrollment
# vs remembering a fact, and the pin phrasing my finger-count words nearly broke.
for text, want in [
        ("open the camera", "camera_on"),
        ("open spotify", "open_app"),
        ("can you see me", "camera_sees"),
        ("what do you see", "look_at"),
        ("how many fingers am i holding up", "fingers"),
        ("learn my face", "face_learn"),
        ("remember my face", "face_learn"),
        ("remember that i drink my coffee black", "remember"),
        ("keep it for ten minutes", "ui"),
]:
    r, dt = call("/brain/classify", {"text": text})
    got = r.get("skill")
    ok = "PASS" if got == want else "FAIL"
    print(f"  {ok}  {text!r:42s} -> {got} ({r.get('confidence')}) want {want}")

print("\n=== 3. camera on ===")
r, dt = call("/camera", {"on": True}, timeout=60)
print(f"  open took {dt:.2f}s  {json.dumps(r)[:200]}")
time.sleep(3)                     # let exposure settle and presence take a pass

st, _ = call("/camera/status")
pres = st.get("presence") or {}
print(f"  on={st.get('on')} err={st.get('error')}")
print(f"  presence: present={pres.get('present')} faces={pres.get('faces')} "
      f"detect_ms={pres.get('detect_ms')} checks={pres.get('checks')}")
print(f"  identity: who={pres.get('who')} enrolled={pres.get('enrolled')}")

print("\n=== 4. how long the answers take (his 'it was buffering') ===")
for tool, args in [("camera_status", {}), ("count_fingers", {}), ("look", {})]:
    r, dt = call("/debug/tool", {"tool": tool, "args": args}, timeout=120)
    mark = "SLOW" if dt > 3.0 else "ok"
    print(f"  {tool:14s} {dt:5.2f}s  [{mark}]  {json.dumps(r)[:220]}")

print("\n=== 5. what he would HEAR right now ===")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from brain.skills import say_camera_sees, say_fingers
    st2, _ = call("/camera/status")
    print("  'can you see me'   ->", say_camera_sees({}, st2))
    fr, _ = call("/debug/tool", {"tool": "count_fingers", "args": {}}, timeout=120)
    print("  'how many fingers' ->", say_fingers({}, fr))
except Exception as e:
    print("  (source-tree skills unavailable here:", e, ")")

print("\nDONE")
