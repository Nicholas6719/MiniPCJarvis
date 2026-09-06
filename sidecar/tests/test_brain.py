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
    # Was None, and that expectation encoded a BUG rather than a decision: "put on"
    # canonicalized to "open APP" (the determiner list covered "the/my/that/this"
    # but not "some"), open_app took it, slots_app refused the music-word, and it
    # fell through to the model. With the erasure fixed it lands where the
    # near-identical "play some music" has always landed. NOTE for later: both are
    # a play/pause keypress, which does nothing when nothing is playing.
    # NOT media_pause: that presses the Windows play/pause key, which goes
    # to whatever app owns the media session. His paused YouTube tab owned
    # it, so "play some music" started a video. Starting music from
    # nothing opens Spotify; media_pause keeps the sentences about what is
    # ALREADY playing.
    ("put on some music", "music_play"),
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
    # a unit conversion is the math reflex's, not the model's (2026-09-05: "how
    # many milliliters in a US cup" was met with "did you mean render that in 3D")
    ("how many milliliters in a US cup", "math"), ("convert 5 miles to kilometers", "math"),
    ("what's 30 celsius in fahrenheit", "math"), ("how many feet in a mile", "math"),
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
    # the market as a story (2026-09-05): what is driving it, what experts say
    ("what's going on in the market", "markets"), ("why is the market down today", "markets"),
    ("what's driving stocks today", "markets"),
    ("any earnings this week", "earnings"), ("when does apple report earnings", "earnings"),
    ("who's reporting earnings tomorrow", "earnings"),
    ("tell me about nvidia stock", "stock_context"),
    ("what should i know about apple stock", "stock_context"),
    ("give me the full picture on tesla", "stock_context"),
    # HIS stocks are a different question from THE market, and the two sit close
    # enough in phrasing that it is worth pinning them apart
    # the handoff: "it" is whatever he was just told about
    ("send it to my phone", "to_phone"), ("send that to me", "to_phone"),
    ("give me the article", "article"), ("open that story", "article"),
    # ...and an article is not an application
    ("open notepad", "open_app"), ("open spotify", "open_app"),
    ("how are my stocks doing", "watchlist"), ("how's my portfolio", "watchlist"),
    ("check my stocks", "watchlist"),
    # his own words from 2026-09-04, each of which fell to the model
    ("center it", "holo_move"), ("stop spinning", "holo_move"),
    ("stop it from spinning", "holo_move"),
    ("turn on hand view", "hands_on"),
    ("what are the systems", "stats"), ("systems check", "stats"),
    ("set my volume to fifty percent", "volume_set"),
    ("what's ten plus ten", "math"), ("what is five times six", "math"),
    # from the variety pass of 2026-09-06
    ("focus on image three", "ui"), ("focus on image eight", "ui"),
    ("good morning", "greeting"), ("how are you today", "greeting"), ("hello jarvis", "greeting"),
    ("what can you do", "capabilities"), ("what are you able to do", "capabilities"),
    ("and what year was it released", None),   # a fact about the thing just discussed, not a story's date
    ("what's in the news", "news"), ("tell me the tech news", "news"),
    ("any breaking news", "breaking"),
    # The camera is a subsystem, not an executable. Both halves are gated
    # together on purpose: the first version of this fix taught the camera to
    # answer "open the camera" and stole every "open spotify" with it, and the
    # second version protected app-launching so well that "open the camera" —
    # his own words for it — launched the Windows Camera app instead.
    ("open the camera", "camera_on"), ("bring up the camera", "camera_on"),
    ("close the camera", "camera_off"),
    ("open the calculator", "open_app"), ("close chrome", "close_app"),
    # enrollment, not a stored fact — and it must reach a skill, not silence
    ("remember my face", "face_learn"), ("learn my face", "face_learn"),
    ("remember that i park in the north garage", "remember"),
    # He asked JARVIS who he was and was told "user". Naming him in the system
    # prompt was not enough: "who am i" matched the memory RECALL skill at 1.000
    # and never reached the model at all. Both sides are gated, because the fix
    # must not swallow a genuine request to read his memory back.
    ("who am i", "whoami"), ("what is my name", "whoami"),
    ("do you know who i am", "whoami"),
    ("what do you know about me", "recall"),
    ("what did i tell you about my car", "recall"),
    # Something to WATCH opens in HIS browser; it is not a web search recited
    # into the side panel. His own sentence is the first case, "You Tube" and
    # all — speech recognition writes it as two words and every pattern matching
    # "youtube" missed it, so the request was right and the routing lost on a
    # space. The control phrasings are gated beside it: stealing "play the
    # video" would replace his pause button with a browser window.
    ("find me a you tube video of someone playing iron man ps3", "video"),
    ("find me a video of a rocket launch", "video"),
    ("search youtube for guitar lessons", "video"),
    ("play the video", "media_pause"), ("pause the music", "media_pause"),
    ("search the web for the best mini pc", "search"),
    # The HUD is the default home for what he asks to see — "it's meant to be an
    # OS". Naming the browser is the ONLY thing that sends it out, so both sides
    # are gated: the same sentence with and without the phrase.
    ("show me pictures of a nebula", "images"),
    ("show me iron man", "images"),
    ("show me iron man in my browser", "browser_search"),
    ("show me pictures of a nebula in my browser", "browser_search"),
    ("look up elden ring in brave", "browser_search"),
    # --- the six refusing guards that had no routing test at all ------------
    # An audit of every slots_() that can return None found 23 skills able to
    # refuse a phrasing and only 17 with a case proving where the utterance
    # LANDS. The other six were the same exposure that produced the "remember my
    # face" silence: the guard fires, nothing catches it, and he is answered with
    # nothing. These are the phrasings each guard is supposed to ACCEPT.
    ("tell me if the cpu goes above 90 percent", "watch"),
    ("let me know when memory is over 90", "watch"),
    ("stop watching the cpu", "unwatch"),
    ("switch to discord", "switch"),
    ("when i say lights out, mute and open spotify", "teach"),
    ("no i meant open spotify", "correction"),
    ("undelete report.docx", "restore_file"),
    # ---- the hologram (phase C) ------------------------------------------
    # THE CORRECTION HE MADE HIMSELF, and the reason these are gated in both
    # directions: "show me Spider-Man" means PICTURES. A hologram is only ever
    # an explicit request. Getting this backwards would make every image search
    # start a 3D render.
    ("show me spider-man", "images"),
    ("show me pictures of a bracket", "images"),
    # a NAMED thing that does not exist yet is a make (2026-09-06: routed to
    # holo_show with no name, it re-showed whatever was up - the arc reactor)
    ("show me spider-man as a hologram", "holo_make"),
    ("render me spider man's mask", "holo_make"), ("now render me spider man's mask", "holo_make"),
    ("show me that as a hologram", "holo_show"),
    ("project the bracket as a hologram", "holo_show"),
    ("let me see it in 3d", "holo_show"),
    ("hide the hologram", "holo_hide"),
    ("take the hologram down", "holo_hide"),
    # ...and "open the hologram" must not launch an app called hologram, which
    # is what _CANON's "open APP" rewrite made of it before the exclusion.
    ("open the hologram", "holo_show"),
    ("open spotify", "open_app"),          # ...and the exclusion breaks nothing
    ("rotate it 90 degrees", "holo_move"),
    ("turn it upside down", "holo_move"),
    # one part of it, and the film's "lose the footpaths" (2026-09-06)
    ("highlight the helmet", "holo_move"),
    ("zoom in on the gauntlet", "holo_move"),
    ("show me the chest plate on its own", "holo_move"),
    ("put all the parts back", "holo_move"),
    # additive edits to the REAL part
    ("add a lid to it", "holo_edit"),
    ("add a handle on the side", "holo_edit"),
    ("hollow it out", "holo_edit"),
    ("get rid of the handle", "holo_edit"),
    # the project file: how it is going, and closing it
    ("how's the spider-man suit going", "project_status"),
    ("where are we with the arc reactor project", "project_status"),
    ("close the project file", "project_close"),
    ("archive the spider-man suit project", "project_close"),
    # the film's own lines
    ("open a new project file, index as mark two", "project_start"),
    ("jarvis are you up", "wakeack"),
    ("show me the before and after", "holo_move"),
    ("what did it look like before", "holo_move"),
    ("throw it up", "holo_show"),
    ("wireframe that", "holo_make"),
    ("fabricate it", "holo_slice"),
    ("get it ready for the printer", "holo_slice"),
    # from the workbench variety pass of 2026-09-06 18:38
    ("file it under the project", "project_file"),
    ("save it to the project", "project_file"),
    ("remove the render", "holo_hide"),
    ("cut it in half", "holo_move"),
    ("show me the layers", "holo_move"),
    # Scrubbing the toolpath. The number is the hazard: _CANON erases plain
    # numbers before embedding, so "layer 50" must still route AND the slots
    # must still see the 50 in the raw sentence.
    ("show me layer 50", "holo_move"),
    ("next layer", "holo_move"),
    ("go back a layer", "holo_move"),
    ("show me the top layer", "holo_move"),
    ("put it back the way it was", "holo_move"),
    ("pull it apart", "holo_move"),
    ("will it print", "holo_check"),
    ("does it fit on the bed", "holo_check"),
    ("will it need supports", "holo_check"),
    # Editing the REAL part, which is a different thing from moving the view.
    # PICKING A PICTURE BY NUMBER, which is how he refers to them. "focus on
    # number 6" went to the WINDOW switcher at 1.00 and sent JARVIS looking for
    # a window called "number 6"; "image number 6" had no reflex at all.
    # "Create me a 3D image of X" — he asked for the Spider-Man emblem, JARVIS
    # thought for fourteen seconds and said "Sure." without doing anything. It
    # scored 0.84 for IMAGES ("image of X" looks like a picture search), whose
    # extractor then declined, so the whole thing fell to the model — which HAD
    # make_hologram and did not call it.
    ("create me a 3d image of the spider emblem", "holo_make"),
    # "3d RENDER of X" had no reflex at all — image/model/print were stripped and
    # render was not, so his phrasing fell to the model.
    ("create a 3d render of iron man's arc reactor", "holo_make"),
    # "RENDER <NAME>" — his own words, twice, as the thing that must work.
    # Measured on the installed build before these seeds existed: "render a
    # duck" reached holo_make at 0.842 while "render iron man mark 3" reached
    # NOTHING at 0.802 and "render me iron man mark 3" was nearest `images`. A
    # proper noun with a number on the end looks like neither a format request
    # nor a generic object.
    # THE WORKSPACE. "Pull up the Spider-Man suit" is a project; "show me
    # Spider-Man" is the images panel. Both directions are gated, because the
    # first seeds I wrote for this took every "open X" in this file at 1.00 —
    # `_CANON` turns "start a new project" into `open APP`, which is byte
    # identical to "open spotify".
    ("we're starting a new project", "project_start"),
    ("this should be its own project", "project_start"),
    ("pull up the spider-man suit", "project_recall"),
    ("where were we on the arc reactor", "project_recall"),
    ("what projects do we have", "project_list"),
    ("write that down for the project", "project_note"),
    ("render iron man mark 3", "holo_make"),
    ("render me iron man mark 3", "holo_make"),
    ("create iron man mark 3 in 3d", "holo_make"),
    ("render a duck", "holo_make"),
    ("render me a mandalorian helmet", "holo_make"),
    ("make me a 3d rendering of the batman logo", "holo_make"),
    # ANOTHER DESIGN of the same thing. The design comes from the reference
    # PICTURE, so this is the next usable picture, not a new search — and
    # without it "another design" went to `search` and web-searched those words.
    ("try a different design", "holo_again"),
    ("another design please", "holo_again"),
    ("show me another version of that", "holo_again"),
    # ...and a real web search is untouched
    ("search the web for the best mini pc", "search"),
    ("look up the population of tokyo", "search"),
    # Dictation writes "3d" as a WORD at least as often as a digit.
    ("create me a three d image of the spider emblem", "holo_make"),
    # ...and spoken numbers must be as strong as typed ones: this was 0.81
    # against 1.00 for the digit, close enough to the threshold to be a coin toss
    ("image number four", "ui"),
    ("make me a 3d model of the spider-man emblem", "holo_make"),
    ("make me a 3d print of the apple logo", "holo_make"),
    # ...and a picture search is still a picture search
    ("show me images of iron man", "images"),
    ("show me spider-man", "images"),
    # "make that image bigger" answered "I can't locate that image window, sir"
    ("make that image bigger", "ui"),
    ("image number 6", "ui"),
    ("show me image 4", "ui"),
    ("number 3", "ui"),
    ("just give me 1 through 4", "ui"),
    ("give me 1-4", "ui"),
    # ...and switching windows must be untouched by that
    ("focus on spotify", "switch"),
    ("switch to chrome", "switch"),
    # ...and a COUNT of images is still a search, not a pick
    ("show me 5 images of spiderman", "images"),
    ("make the hole bigger", "holo_edit"),
    ("make it taller", "holo_edit"),
    ("round off the corners", "holo_edit"),
    ("put the old version back", "holo_revert"),
    ("undo that change", "holo_revert"),
    # ...and "go back to the previous version" must not go looking for a window
    # called `previous version`, which is what "switch to APP" made of it.
    ("go back to the previous version", "holo_revert"),
    ("go back to notepad", "switch"),      # ...and the exclusion breaks nothing
    # MAKING a model, as against showing one he already has. One is instant; the
    # other can be three minutes and asks first, so they must not blur.
    ("make me a 3d model of a dragon", "holo_make"),
    ("create a 3d model of that", "holo_make"),
    ("design me a phone stand", "holo_make"),
    ("stop the render", "render_stop"),
    ("how's the model coming along", "render_how"),
    ("is that model done yet", "render_how"),
    # Hands (phase E). "Stop watching my hands" must not be read as cancelling a
    # CPU alert, which is what "stop watching METRIC" made of it.
    ("let me move it with my hands", "hands_on"),
    ("turn on hand control", "hands_on"),
    ("stop watching my hands", "hands_off"),
    ("hands off", "hands_off"),
    ("stop watching the cpu", "unwatch"),      # ...and the exclusion breaks nothing
    # MAKING a part, not EDITING one. He asked for "a plate 40 by 30 by 6
    # millimetres with a 5 millimetre hole", waited thirty seconds, was told it
    # was made, and never saw it — because that sentence contains "make", "hole"
    # and a measurement, and holo_edit owns "make the hole bigger". Every
    # holo_make seed named an object abstractly or named the format; not one
    # looked like the dimensioned part a person asks for when the point is
    # printing it. The signal is "me a" (a NEW thing) against "the" (the one
    # already on the stage).
    ("make me a plate 40 by 30 by 6 millimetres with a 5 millimetre hole", "holo_make"),
    ("make me a plate 40 by 30 by 6 mm with a 5 mm hole", "holo_make"),
    ("make me a 20 mm cube", "holo_make"),
    ("make me a hex spacer 12 mm tall", "holo_make"),
    ("make me a bracket with two holes", "holo_make"),
    ("print me a washer 20 mm across", "holo_make"),
    # ...and the edits it must NOT swallow
    ("make the hole bigger", "holo_edit"),
    ("make the wall thicker", "holo_edit"),
    ("change the hole to 5 millimetres", "holo_edit"),
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
