"""Held-out accuracy for the Brain reflex router. Run: python tests/test_brain.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain.router import brain

CASES = [
    ("jarvis what time is it now", "time"), ("do you know what time it is", "time"),
    ("what's today's date", "date"), ("what day of the week is today", "date"),
    ("crank the volume to 65 percent", "volume_set"), ("volume 35 please", "volume_set"),
    ("mute the speakers", "mute"), ("turn the audio back on", "unmute"),
    ("hey jarvis open spotify for me", "open_app"), ("could you launch notepad", "open_app"),
    ("close spotify", "close_app"), ("quit notepad please", "close_app"),
    ("grab a screenshot and save it to my desktop", "screenshot"), ("take a screenshot please", "screenshot"),
    ("search the web for the best gaming laptop under 1500", "search"), ("look up tomorrow's weather in framingham", "weather"),
    ("show me a picture of a worm", "images"), ("show me some photos of saturn", "images"),
    ("what's on my screen right now", "screen"), ("have a look at my screen", "screen"),
    ("remind me in 25 minutes to call dad", "reminder"), ("set a reminder for 6 pm to start dinner", "reminder"),
    ("remember that i like my coffee black", "remember"),
    ("how's the computer doing", "stats"), ("what windows do i have open", "windows"),
    ("tell me about the history of the roman empire", None), ("write me a haiku about rain", None),
    ("what's the difference between ram and vram", None), ("who directed spider-man homecoming", None),
    ("open the pod bay doors", None), ("what should i have for dinner", None), ("how much does a tesla cost", None),
    ("what's the weather like on mars", None), ("can you explain how wake words work", None),
    # definitions, not measurements — these used to land on the system-stats reflex
    ("what does cpu stand for", None), ("what is a cpu", None), ("what does ram mean", None),
    ("what is a solid state drive", None),
    # ...while the live readings that look similar must still be reflexes
    ("what's the time", "time"), ("what's the date", "date"), ("what's the cpu at", "stats"),
]

async def main() -> int:
    await brain.load()
    print(f"loaded {brain.example_count} examples")
    ok = 0; lat = []
    for text, want in CASES:
        t0 = time.time(); d = await brain.decide(text); lat.append((time.time() - t0) * 1000)
        got = d[0].name if d else None
        conf = d[2] if d else (await brain.classify(text))[1]
        ok += got == want
        print(f"  {'PASS' if got == want else 'FAIL'} {text[:50]:50} -> {str(got):10} ({conf:.2f}){' ' + str(d[1]) if d else ''}")
    print(f"\nACCURACY {ok}/{len(CASES)} | median decide {sorted(lat)[len(lat)//2]:.0f} ms")
    return 0 if ok >= len(CASES) - 2 else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
