"""The voice spec: one pack voice, or a blend that exists between two.

Offline; no model is loaded. What is gated is that a spec parses the way
Settings will write it, that a typo costs him nothing, and that British voices
get British phonemes.

Run: python tests/test_voice_spec.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from audio.tts import DEFAULT_VOICE, kokoro_lang, parse_voice

    print("\n-- one voice --")
    check("a pack voice is itself", parse_voice("bm_daniel") == [("bm_daniel", 1.0)])
    check("spaces are ignored", parse_voice(" bm_daniel ") == [("bm_daniel", 1.0)])

    print("\n-- a blend --")
    got = parse_voice("bm_george+bm_daniel")
    check("equal parts by default",
          got == [("bm_george", 0.5), ("bm_daniel", 0.5)], got)
    got = parse_voice("bm_george:0.7+bm_daniel:0.3")
    check("weights are honoured", got == [("bm_george", 0.7), ("bm_daniel", 0.3)], got)
    got = parse_voice("bm_george:3+bm_daniel:1")
    check("...and normalised", got == [("bm_george", 0.75), ("bm_daniel", 0.25)], got)
    got = parse_voice("bm_george:0.6+bm_lewis:0.2+bm_fable:0.2")
    check("three is fine", len(got) == 3 and abs(sum(w for _, w in got) - 1) < 1e-9, got)

    print("\n-- a typo costs him nothing --")
    for bad in ("", "jarvis", "bm_george:x", "paul_bettany", None, "+", "bm_george:0"):
        got = parse_voice(bad)
        check(f"{bad!r} still speaks", got and all(n.startswith(("af_", "am_", "bf_", "bm_"))
                                                  for n, _ in got), got)
    check("nothing usable falls back to the default",
          parse_voice("paul_bettany") == [(DEFAULT_VOICE, 1.0)])
    check("a bad weight is one part, not a crash",
          parse_voice("bm_george:x+bm_daniel") == [("bm_george", 0.5), ("bm_daniel", 0.5)])
    check("a zero-weight part is dropped",
          parse_voice("bm_george:0+bm_daniel") == [("bm_daniel", 1.0)])

    print("\n-- British voices get British phonemes --")
    check("bm_ is en-gb", kokoro_lang(parse_voice("bm_daniel")) == "en-gb")
    check("am_ is en-us", kokoro_lang(parse_voice("am_michael")) == "en-us")
    check("a mostly-British blend is en-gb",
          kokoro_lang(parse_voice("bm_george:0.7+am_michael:0.3")) == "en-gb")
    check("a mostly-American blend is en-us",
          kokoro_lang(parse_voice("bm_george:0.3+am_michael:0.7")) == "en-us")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
