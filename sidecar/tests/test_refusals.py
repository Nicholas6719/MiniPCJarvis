"""A click JARVIS must never make, wherever the control lives.

The owner's rule: a purchase or a destructive control is refused OUTRIGHT -
no confirmation, no "yes" that overrides it. Two sites click by name - a
native control through UI Automation (tools/uia.py) and a web element through
Playwright (browser/session.py) - and both judge the MATCHED element's real
name, not the words he used, because "click the yellow button" can resolve to
"Buy now" and the request contains no dangerous word at all.

Decisions pinned here (see tools/refusals.py for the reasoning):
  refused  buy / add to cart / place order / checkout / pay / PayPal /
           subscribe / unsubscribe / cancel subscription / delete account /
           deactivate / uninstall / Format... / erase / empty recycle bin /
           discard / close without saving / don't save
  allowed  seven, plus, OK, Next, Save, Cancel, Close, Send, Delete (a line,
           a row, an email to the bin), Remove, Format Painter, Format Cells,
           Payment methods, Formatting, Buyer's guide, Eraser

No live clicks: the UIA walk and the page are stand-ins, and the one path
that would reach the mouse is never taken. Run: python tests/test_refusals.py
"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "refusals.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


MUST_REFUSE = [
    "Buy now", "Buy", "Buy it now", "Buy again", "Purchase", "Complete purchase",
    "Order now", "Place order", "Place your order", "Pay", "Pay now", "PayPal",
    "Apple Pay", "Checkout", "Check out", "Proceed to checkout", "Confirm payment",
    "Confirm purchase", "Add to Cart", "Add to bag", "Add to basket", "Redeem",
    "Apply coupon", "Subscribe", "Subscribe & Save", "Unsubscribe",
    "Cancel subscription", "Cancel my subscription", "Delete account",
    "Delete my account", "Remove account", "Close account", "Deactivate",
    "Uninstall", "Format...", "Format", "Format disk", "Erase", "Erase all content",
    "Wipe", "Empty Recycle Bin", "Empty trash", "Discard", "Discard changes",
    "Close without saving", "Don't Save", "Don’t Save", "Do not save",
    "Delete permanently", "Permanently delete", "Delete forever",
    # real page text: the one that got clicked in PCJarvis before the list grew
    "Add to Cart\nShips from Amazon",
]

MUST_ALLOW = [
    "seven", "plus", "OK", "Next", "Back", "Save", "Save As", "Save all", "Cancel",
    "Close", "Close tab", "Send", "Reply", "Delete", "Delete line", "Delete row",
    "Remove", "Remove tag", "Format Painter", "Format Cells", "Format text",
    "Formatting", "Payment methods", "Buyer's guide", "Eraser", "Search",
    "Open", "Open file", "Print", "Copy", "Paste", "Undo", "Minimize",
    "Settings", "Help", "Sign in", "Subscribed", "Deactivated plugins",
    "Purchase history", "Orders", "Your cart",
]


class _FakeLocator:
    def __init__(self, label: str, present: bool, log: list):
        self._label, self._present, self._log = label, present, log

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._present else 0

    async def inner_text(self, timeout=0):
        return self._label

    async def get_attribute(self, name, timeout=0):
        return None

    async def click(self, timeout=0):
        self._log.append(self._label)


class _FakePage:
    """Every locator resolves to the same element, named `label`."""
    url = "https://shop.example/item"

    def __init__(self, label: str, log: list):
        self._label, self._log = label, log

    def get_by_role(self, role, name=None):
        return _FakeLocator(self._label, True, self._log)

    def get_by_text(self, text, exact=False):
        return _FakeLocator(self._label, True, self._log)

    def locator(self, sel):
        return _FakeLocator(self._label, True, self._log)

    async def wait_for_timeout(self, ms):
        pass


def main() -> int:
    from tools import refusals, uia
    from tools.refusals import is_dangerous

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\n-- the list --")
    bad = [n for n in MUST_REFUSE if not is_dangerous(n)]
    check("every purchase / destructive name is refused", not bad, str(bad))
    bad = [n for n in MUST_ALLOW if is_dangerous(n)]
    check("ordinary controls stay clickable", not bad, str(bad))
    check("is_dangerous returns the phrase it matched",
          (is_dangerous("Add to Cart") or "").lower() == "add to cart", str(is_dangerous("Add to Cart")))
    check("...case-insensitively", is_dangerous("ADD TO CART") is not None)
    check("...and nothing for an empty name", is_dangerous("") is None and is_dangerous(None) is None)
    r = refusals.refusal("Buy now")
    check("the refusal is an error the orchestrator can speak as-is",
          r.get("refused") is True and r.get("control") == "Buy now"
          and r["error"] == "I won't click 'Buy now' myself, sir. That one is yours.", str(r))

    print("\n-- both click sites use the one list, on the matched element's name --")
    uia_src = open(os.path.join(here, "tools", "uia.py"), encoding="utf-8").read()
    web_src = open(os.path.join(here, "browser", "session.py"), encoding="utf-8").read()
    check("uia.py imports refusals", "from tools import refusals" in uia_src)
    check("browser/session.py imports refusals", "from tools import refusals" in web_src)
    check("uia tests best[\"name\"] (the matched control), not `name` (his words)",
          'refusals.is_dangerous(best["name"])' in uia_src)
    check("the web click reads the element's label before clicking",
          "_element_label(first)" in web_src and "refusals.is_dangerous(label)" in web_src)
    check("...and the label is read BEFORE .click(", web_src.index("refusals.is_dangerous(label)")
          < web_src.index("await first.click("))
    check("browser_submit presses Enter and clicks no named control (nothing to judge)",
          'page.keyboard.press("Enter")' in web_src)
    orch = open(os.path.join(here, "orchestrator.py"), encoding="utf-8").read()
    check("the orchestrator speaks a refused result and stops (never re-asks)",
          'inner.get("refused")' in orch)
    check("browser_click has a confirmation phrase that names the target",
          '"browser_click": lambda a:' in orch and "a.get('target'" in orch)

    print("\n-- click_control: judged after the match, before Invoke --")
    saved_collect, saved_fg = uia._collect, uia._foreground_title
    uia._foreground_title = lambda: "Amazon.com - Brave"
    try:
        def fake_collect(window=None):
            return [{"name": "Add to Cart", "role": "button", "clickable": True,
                     "x": 10, "y": 10, "_el": None},
                    {"name": "Save for later", "role": "button", "clickable": True,
                     "x": 10, "y": 40, "_el": None}]
        uia._collect = fake_collect
        res = asyncio.run(uia.click_control("add to cart"))
        check("the matched 'Add to Cart' is refused", res.get("refused") is True
              and res.get("control") == "Add to Cart", str(res))
        res = asyncio.run(uia.click_control("the big cart button"))
        check("...and so is a request that never said 'cart' but matched it",
              res.get("refused") is True or "don't see" in res.get("error", ""), str(res))
        # The own-window guard: never JARVIS's own HUD, named or in front.
        res = asyncio.run(uia.click_control("Yes", window="JARVIS"))
        check("a click into the JARVIS window by name is refused",
              res.get("own_window") is True, str(res))
        uia._foreground_title = lambda: "JARVIS"
        res = asyncio.run(uia.click_control("Yes"))
        check("...and into the foreground window when that is JARVIS",
              res.get("own_window") is True, str(res))
        check("the guard compares against windows_tools' own title",
              "OWN_WINDOW_TITLE" in uia_src)
    finally:
        uia._collect, uia._foreground_title = saved_collect, saved_fg

    print("\n-- browser click: judged on what the element says --")
    from browser.session import browser
    saved_act = browser._act

    async def fake_act(action, fn):
        return await fn(page)

    try:
        browser._act = fake_act
        clicks = []
        page = _FakePage("Add to Cart", clicks)
        res = asyncio.run(browser.click("the yellow button"))
        check("'the yellow button' that resolves to 'Add to Cart' is refused",
              res.get("refused") is True and res.get("control") == "Add to Cart", str(res))
        check("...and nothing was clicked", clicks == [], str(clicks))
        res = asyncio.run(browser.click("Buy now"))
        check("his own words are judged first, with no page needed",
              res.get("refused") is True and clicks == [], str(res))
        page = _FakePage("Save for later", clicks)
        saved_observe = browser.observe

        async def fake_observe():
            return {"url": page.url, "title": "item"}
        browser.observe = fake_observe
        try:
            res = asyncio.run(browser.click("save for later"))
        finally:
            browser.observe = saved_observe
        check("an ordinary control is clicked, and the real label is reported",
              clicks == ["Save for later"] and res.get("clicked") == "Save for later", str(res))
    finally:
        browser._act = saved_act

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
