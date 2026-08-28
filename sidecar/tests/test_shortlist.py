"""The tool shortlist may make the prompt smaller. It may NEVER hide the tool a
turn needs — a capability that silently disappears is far worse than a long
prompt. This gate asserts recall, not size. Run: python tests/test_shortlist.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os, tempfile  # noqa: E402
# never touch the real jarvis.db: the gates get a throwaway file that
# brain.load() re-seeds from the SKILLS list
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
from tools import (browser_tools, builtin, file_tools, input_tools, memory_tools,  # noqa: E402
                   task_tools, vision_tools, weather, web_tools, windows_tools)
from tools.registry import registry  # noqa: E402
from tools.shortlist import shortlist  # noqa: E402

fails = []

# (utterance, the tool that must survive the cut)
CASES = [
    ("what is the weather in boston", "get_weather"),
    ("open spotify", "open_application"),
    ("close notepad", "close_application"),
    ("type hello and press enter", "type_text"),
    ("click C4", "click_screen"),
    ("press ctrl s", "press_keys"),
    ("set the volume to 40", "set_volume"),
    ("turn it down a bit", "adjust_volume"),
    ("take a screenshot", "take_screenshot"),
    ("send me a grid screenshot", "screenshot_grid"),
    ("what is on my screen", "analyze_screen"),
    ("remind me in ten minutes to stretch", "set_reminder"),
    ("what reminders do i have", "list_reminders"),
    ("cancel my reminders", "cancel_reminders_matching"),
    ("what is in the recycle bin", "list_recycle_bin"),
    ("empty the recycle bin", "empty_recycle_bin"),
    ("put back the file i deleted", "restore_from_recycle_bin"),
    ("show me pictures of mars", "show_images"),
    ("what is on my clipboard", "get_clipboard"),
    ("lock the computer", "lock_computer"),
    ("switch to discord", "focus_window"),
    ("what windows are open", "list_windows"),
    ("minimize everything", "show_desktop"),
    ("bring my windows back", "restore_windows"),
    ("show me my downloads", "list_folder"),
    ("find the file called budget", "find_files"),
    ("remember that i park in spot twelve", "remember_fact"),
    ("look up who won the game", "web_search"),
    ("open example.com and tell me what it says", "browser_open"),
    ("skip this song", "media_control"),
]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    for m in (builtin, memory_tools, windows_tools, web_tools, task_tools,
              vision_tools, browser_tools, file_tools, weather, input_tools):
        m.register_all()
    total = len(registry._tools)
    await shortlist.build(registry)
    check("shortlist built", shortlist._matrix is not None)

    sizes, missed = [], []
    for utterance, want in CASES:
        picked = {t["function"]["name"] for t in await shortlist.pick(registry, utterance)}
        sizes.append(len(picked))
        if want not in picked:
            missed.append((utterance, want))
    check(f"every needed tool survives the cut ({len(CASES) - len(missed)}/{len(CASES)})",
          not missed, missed)
    avg = sum(sizes) / len(sizes)
    check(f"the prompt actually shrinks (avg {avg:.0f} of {total} tools)", avg < total * 0.75, avg)
    check("never sends fewer than the floor", min(sizes) >= 16, min(sizes))

    # a tool already used this turn is always re-offered, however it ranks
    picked = {t["function"]["name"] for t in
              await shortlist.pick(registry, "what time is it", keep={"empty_recycle_bin"})}
    check("a tool already used this turn stays offered", "empty_recycle_bin" in picked)

    # failure modes must fall back to everything, never to nothing
    check("empty utterance sends all tools",
          len(await shortlist.pick(registry, "   ")) == total)
    saved = shortlist._matrix
    shortlist._matrix = None
    check("an unbuilt index sends all tools",
          len(await shortlist.pick(registry, "open spotify")) == total)
    shortlist._matrix = saved

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
