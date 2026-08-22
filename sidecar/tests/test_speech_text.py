import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audio.speech_text import clean_for_speech as c

tests = [
    "Spider-Man *Homecoming* was released in 2017.",
    r"Screenshot saved at C:\Users\nicho\AppData\Roaming\JARVIS\screenshots\screen-20260821.png.",
    "The war ended in 1946 and/or 1945. See **bold** and _italic_ and `code`.",
    "- First item\n- Second item with [a link](https://example.com/x)",
    "It is 42 degrees and the year 2026 was quiet.",
]
for t in tests:
    print(repr(c(t)))
