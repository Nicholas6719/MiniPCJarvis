"""Remote-hands gate (R2). Pure logic only — NOTHING here types, clicks or moves
the real mouse: a test that drives input would fight whoever is at the keyboard.
Run: python tests/test_input.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import input_tools as ip  # noqa: E402
from tools.registry import Risk, registry  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def main() -> int:
    ip.register_all()
    # --- grid maths: cells map inside the screen, junk is rejected -----------
    w, h = ip._screen_size()
    a1 = ip._cell_to_xy("A1")
    check("A1 lands in the first cell", a1 and a1[0] < w / ip.GRID_COLS and a1[1] < h / ip.GRID_ROWS, a1)
    last = f"{chr(ord('A') + ip.GRID_COLS - 1)}{ip.GRID_ROWS}"
    lx = ip._cell_to_xy(last)
    check(f"{last} lands in the last cell", lx and lx[0] > w * (ip.GRID_COLS - 1) / ip.GRID_COLS
          and lx[1] > h * (ip.GRID_ROWS - 1) / ip.GRID_ROWS, lx)
    check("cells are case/space tolerant", ip._cell_to_xy(" c4 ") == ip._cell_to_xy("C4"))
    for bad in ["Z9", "A0", f"A{ip.GRID_ROWS + 1}", "4C", "", "hello", "C99"]:
        check(f"rejects {bad!r}", ip._cell_to_xy(bad) is None, ip._cell_to_xy(bad))

    # --- every input tool is risk-gated (a remote request must stop and ask) --
    for name in ("type_text", "press_keys", "click_screen"):
        t = registry.get(name)
        check(f"{name} is registered", t is not None)
        if t:
            check(f"{name} requires confirmation", t.requires_confirmation, t.risk)
    grid = registry.get("screenshot_grid")
    check("screenshot_grid is SAFE (looking is not acting)",
          grid is not None and grid.risk == Risk.SAFE)

    # --- key combination parsing (no keys are actually sent: bad combos only) -
    check("unknown key combo refuses", not ip._press("frobnicate"))
    check("empty combo refuses", not ip._press(""))
    check("modifier-only combo refuses", not ip._press("ctrl+shift"))

    # --- click validation, without clicking ----------------------------------
    r = await ip.click_screen(cell="Q9")
    check("bad cell returns an error, no click", "error" in r, r)
    r = await ip.click_screen()
    check("no target returns an error, no click", "error" in r, r)

    # --- the lock check must be callable and boolean -------------------------
    check("lock detection returns a bool", isinstance(ip._locked(), bool))

    # --- confirmation phrasing says exactly what will happen -----------------
    from orchestrator import CONFIRM_PHRASE
    p = CONFIRM_PHRASE["type_text"]({"text": "hello there", "window": "Claude", "press_enter": True})
    check("type gate quotes the text", "hello there" in p and "Claude" in p and "enter" in p.lower(), p)
    p = CONFIRM_PHRASE["click_screen"]({"cell": "C4"})
    check("click gate names the cell", "C4" in p, p)
    p = CONFIRM_PHRASE["empty_recycle_bin"]({})
    check("empty-bin gate warns it is permanent", "cannot be undone" in p.lower(), p)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
