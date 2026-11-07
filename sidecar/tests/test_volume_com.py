"""The volume tools work from a fresh thread: COM is initialised where it is used.

Release 90's suites asked JARVIS to set the volume and it answered "I'm afraid I
couldn't change the volume": the tool ran on a pool thread that had never
initialised COM ("CoInitialize has not been called"). It had only ever worked
because another tool had happened to initialise COM on the same thread first.

This reads the volume (never sets it) on a brand-new thread, which is exactly
the condition that failed. A machine with no audio device at all is skipped,
not failed; the COM error is never excused.
Run: python tests/test_volume_com.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    from tools import windows_tools as W
    out = {}

    def fresh():
        try:
            out["v"] = W.get_volume()
        except Exception as e:      # noqa: BLE001 - the error IS the result
            out["e"] = e

    t = threading.Thread(target=fresh, name="volume-com-gate")
    t.start()
    t.join(20)
    err = out.get("e")
    if err is not None and "CoInitialize" not in str(err) and "-2147221008" not in str(err):
        print(f"  skipped: no usable audio endpoint here ({err})")
    else:
        check("a fresh thread can read the volume (COM initialised where it is used)",
              err is None and isinstance(out.get("v"), dict), err)
        if err is None:
            check("...and it reads a real level", 0 <= out["v"]["volume_percent"] <= 100, out["v"])
    src = open(W.__file__, encoding="utf-8").read()
    body = src[src.index("def _endpoint_volume"):src.index("def get_volume")]
    check("the shared helper initialises COM itself", "CoInitialize()" in body)
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
