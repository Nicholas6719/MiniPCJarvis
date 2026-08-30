"""Held-out accuracy for the Brain reflex router. Run: python tests/test_brain.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os, tempfile  # noqa: E402
# never touch the real jarvis.db: the gates get a throwaway file that
# brain.load() re-seeds from the SKILLS list
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
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
    ("show me iron man", "images"), ("show me 5 images of spiderman", "images"),
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
    # the stage's voice hooks (§6.3/§6.5): every hint printed on screen must route
    ("show settings", "ui"), ("show me the history", "ui"), ("settings history", "ui"),
    ("bring that back", "ui"), ("keep it for ten minutes", "ui"), ("keep it", "ui"),
    ("make that bigger", "ui"), ("zoom in on the second one", "ui"), ("back to the grid", "ui"),
    ("hide everything", "ui"),
    # "wake up" must NEVER land on the sleep skill (he'd answer a wake by re-sleeping)
    ("wake up", "wakeack"), ("wake up jarvis", "wakeack"), ("time to wake up", "wakeack"),
    ("good morning jarvis", "wakeack"), ("are you there", "wakeack"),
    # misroutes found by the 2026-08-26 route sweep: each of these did the WRONG THING
    # both of these used to put JARVIS to sleep; they now have real handlers
    # (2026-08-27) instead of merely being guarded away from the sleep skill
    ("go to sleep in an hour", None),       # was: slept immediately
    ("wake me up at 7", None),              # was: "At your service." to an alarm request
    ("put on some music", None),            # was: launching an app called "some music";
                                            # slots_app now rejects music-words -> LLM plays
    ("switch to a british voice", None),    # was: hunting a window titled "a british voice"
    ("show me pictures from my trip", None),# was: web-searching his personal photos
    ("we're done here", "sleep"),           # dismissals still work
    # provenance (fact store receipts): he never volunteers sources, but answers for them
    ("how do you know that", "provenance"), ("what's your source", "provenance"),
    ("where did you get that", "provenance"),
    # the recycle bin (he could not see it at all until 2026-08-26 evening)
    ("what files are in the recycle bin", "recycle_bin"),
    ("what's in the trash", "recycle_bin"), ("what did i delete", "recycle_bin"),
    ("find the file called budget", "find_file"),   # must NOT become a restore
    # the remote click-grid capture must not fall into the plain screenshot reflex
    ("take a screenshot with the click grid on it", "grid_shot"),
    ("send me a grid screenshot", "grid_shot"),
    ("take a screenshot", "screenshot"),            # ...and plain stays plain
    ("take a screenshot to my desktop", "screenshot"),
    # 2026-08-27, from the Telegram logs: none of these routed anywhere, so a plain
    # request to stop being nagged reached the model, which had no tool for it either
    ("don't remind me to stretch anymore", "unremind"),
    ("stop reminding me to stretch", "unremind"),
    ("cancel my reminders", "unremind"),
    ("what reminders do i have", "reminders"),
    # ...and a courtesy must never be handed to the model (it parroted "Thank you
    # Jarvis, sir." straight back at him)
    ("thank you jarvis", "thanks"), ("thanks", "thanks"),
    ("remind me in 20 minutes to check the oven", "reminder"),   # still sets them
    # from the real-world suite: both of these reached the model, which acknowledged
    # ("Understood.") or did something else entirely (set a CPU alert)
    ("be quieter", "volume_rel"), ("turn it up", "volume_rel"),
    ("turn it down a bit", "volume_rel"), ("minimize everything", "show_desktop"),
    ("bring my windows back", "restore_windows"),
    ("show me my desktop", "folder"),   # the FOLDER — that meaning came first
    ("hide everything", "ui"),          # the HUD stage, not the windows
    ("set the volume to 40 percent", "volume_set"),   # absolute still absolute
    # recall answers from memory itself now (it was paying an 11 s LLM round to
    # speak a sentence already sitting on disk)
    ("what do you remember about my desk lamp", "recall"),
    ("what did i tell you about my car", "recall"),
    # a courtesy attached to a DISMISSAL is a dismissal — adding the thanks skill
    # briefly stole these from sleep mode (found by the 2026-08-27 audit)
    ("that's it thanks", "sleep"), ("thanks that is all", "sleep"),
    ("goodnight", "sleep"), ("thank you", "thanks"),
    # markets and news (2026-08-27). Prices and headlines are realm 2 — the point
    # of routing them is that they reach a LIVE tool, never the model's memory.
    ("what's apple trading at", "quote"), ("how is nvidia stock doing", "quote"),
    # by TICKER, and by tickers that are NOT among the seeds — the shape has to
    # generalise, or typing "NVDA" from the phone falls through to the LLM,
    # which answers it off a scraped web page instead of the live quote
    ("what's NVDA trading at", "quote"), ("how is AMD doing", "quote"),
    ("what is COIN at", "quote"), ("price of RIVN", "quote"),
    # ...but an uppercase PRODUCT is not a ticker. "the price of an RTX 5090" was
    # being rewritten to "an apple 5090" and sent to the stock tool instead of
    # the web — an article and a model number are both giveaways.
    ("look up what the current price of an RTX 5090 is", "search"),
    ("what is the price of an RTX 5090", None),
    ("what do analysts say about tesla", "analyst"), ("is nvidia a buy", "analyst"),
    ("how's the market doing", "markets"), ("how are the markets", "markets"),
    # HIS stocks are a different question from THE market, and the two sit close
    # enough in phrasing that it is worth pinning them apart
    # the handoff: "it" is whatever he was just told about
    ("send it to my phone", "to_phone"), ("send that to me", "to_phone"),
    ("give me the article", "article"), ("open that story", "article"),
    # ...and an article is not an application
    ("open notepad", "open_app"), ("open spotify", "open_app"),
    ("how are my stocks doing", "watchlist"), ("how's my portfolio", "watchlist"),
    ("check my stocks", "watchlist"),
    ("what's in the news", "news"), ("tell me the tech news", "news"),
    ("any breaking news", "breaking"),
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
