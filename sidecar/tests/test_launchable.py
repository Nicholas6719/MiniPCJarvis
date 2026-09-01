"""What "open X" is allowed to mean.

The router canonicalises "open|launch|start|run|put on X" onto a single seed
sentence, so the cosine hits 1.00 against a real seed and the confidence
threshold never gets a vote. That made "open a bank account" route to open_app -
which speaks FIRST - so on 2026-08-31 he heard "Opening a bank account." before
anything was attempted.

The guard is: does the name match something this machine can actually launch?
Both directions matter. Refusing an English sentence is the point; refusing
Excel would be a worse bug than the one being fixed, and the first version of
this did exactly that, because Office has no Start Menu shortcut here.

Run: python tests/test_launchable.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.builtin import launchable_names, looks_launchable  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    check("the index is built and non-trivial", len(launchable_names()) > 20,
          len(launchable_names()))

    # things he might really ask for
    for app in ("notepad", "excel", "word", "powerpoint", "outlook", "teams",
                "spotify", "chrome", "brave", "edge", "settings", "calculator",
                "paint", "task manager", "file explorer"):
        check(f"{app!r} can be opened", looks_launchable(app))

    # the sentences that made him hear "Opening a bank account."
    for phrase in ("a bank account", "a diagnostic", "a movie", "the numbers",
                   "the door", "my feelings", "a new project", "the car",
                   "a campaign", "the process"):
        check(f"{phrase!r} is not an app", not looks_launchable(phrase))

    # and the shapes that should never reach the app resolver at all
    check("an empty name is not an app", not looks_launchable(""))
    check("a sentence-length string is not an app",
          not looks_launchable("everything you know about quantum mechanics please"))

    print('')
    print('ALL PASS' if not fails else f'{len(fails)} FAILURES')

    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
