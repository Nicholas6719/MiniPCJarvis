"""Clicking by name instead of by pixel.

The grid screenshot (input_tools) is honest but coarse: it tells the user where
to aim, not what is there. Windows already publishes the truth through UI
Automation — every button, field, link and menu item with its name, its role and
where it is — which is how production desktop agents work: read the control
tree, fall back to vision only for canvas-drawn apps that publish nothing.

So "click the Send button" becomes exact and survives the window moving,
resizing or the display scaling; the grid stays for everything UIA cannot see.

Uses comtypes against UIAutomationCore directly — pywinauto and the uiautomation
package would both add a bundled dependency for what is a few hundred lines of
tree walking.
"""
from __future__ import annotations

import asyncio
import logging
import re

from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.uia")

# Control types worth offering a user (UIA_ControlTypeIds).
_INTERESTING = {
    50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "edit",
    50005: "hyperlink", 50006: "image", 50007: "listitem", 50008: "list",
    50009: "menu", 50011: "menuitem", 50012: "progressbar", 50013: "radiobutton",
    50015: "slider", 50018: "tab", 50019: "tabitem", 50020: "text",
    50021: "toolbar", 50024: "treeitem", 50025: "custom", 50026: "group",
    50029: "document", 50030: "pane", 50031: "header", 50033: "datagrid",
    50034: "dataitem", 50036: "splitbutton",
}
# ...and the ones a "click X" request almost always means.
_CLICKABLE = {50000, 50002, 50003, 50005, 50007, 50011, 50013, 50018, 50019,
              50024, 50034, 50036, 50004}

_MAX_ELEMENTS = 400          # a deep tree is a slow tree; this is plenty for a window
INVOKE_TIMEOUT_S = 2.5       # how long to wait on the app before using the mouse


def _uia():
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA
    return comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation), UIA


# Window classes Windows uses for menus, dropdowns and dialogs — the things
# that open ON TOP of an app and are separate top-level windows underneath.
_POPUP_CLASSES = ("#32768", "#32770", "popup", "menu", "dialog", "taskdialog",
                  "tooltips_class32", "combolbox")


def _is_popup(cls: str) -> bool:
    c = (cls or "").lower()
    return any(k in c for k in _POPUP_CLASSES)


def _collect(window_title: str | None = None) -> list[dict]:
    """Every named, on-screen control in the foreground (or named) window."""
    import comtypes
    comtypes.CoInitialize()
    iuia, UIA = _uia()
    root = iuia.GetRootElement()
    tops = root.FindAll(2, iuia.CreateTrueCondition())        # 2 = TreeScope_Children
    target = None
    if window_title:
        q = window_title.strip().lower()
        for i in range(tops.Length):
            el = tops.GetElement(i)
            if q in (el.CurrentName or "").lower():
                target = el
                break
        if target is None:
            return []
    else:
        import win32gui
        target = iuia.ElementFromHandle(win32gui.GetForegroundWindow())
    if target is None:
        return []

    # An open menu, a dropdown or a modal dialog is its OWN top-level window;
    # inside the app's tree it is at most an empty placeholder. Walking the
    # window alone therefore finds "File" but never "Save as". Only windows of
    # that class are taken — the app's other ORDINARY windows must stay out, or
    # a second Notepad's buttons get mixed in with this one's and a click by
    # name can land in the wrong window.
    roots = [target]
    try:
        pid = target.CurrentProcessId
        for i in range(tops.Length):
            el = tops.GetElement(i)
            try:
                if (el.CurrentProcessId == pid and not el.CurrentIsOffscreen
                        and _is_popup(el.CurrentClassName or "")
                        and not iuia.CompareElements(el, target)):
                    roots.append(el)
            except Exception:
                continue
    except Exception:
        pass
    roots = roots[:6]

    out: list[dict] = []
    walker = iuia.RawViewWalker

    def walk(el, depth: int) -> None:
        if len(out) >= _MAX_ELEMENTS or depth > 18:
            return
        child = walker.GetFirstChildElement(el)
        while child and len(out) < _MAX_ELEMENTS:
            try:
                if not child.CurrentIsOffscreen:
                    name = (child.CurrentName or "").strip()
                    ctype = child.CurrentControlType
                    if name and ctype in _INTERESTING:
                        r = child.CurrentBoundingRectangle
                        if r.right > r.left and r.bottom > r.top:
                            out.append({
                                "name": name[:80],
                                "role": _INTERESTING.get(ctype, str(ctype)),
                                "clickable": ctype in _CLICKABLE or bool(child.CurrentIsKeyboardFocusable),
                                "x": int((r.left + r.right) / 2),
                                "y": int((r.top + r.bottom) / 2),
                                "_el": child,
                            })
                    walk(child, depth + 1)
            except Exception:
                pass
            try:
                child = walker.GetNextSiblingElement(child)
            except Exception:
                break

    for r in roots:
        walk(r, 0)
    return out


# People say "click the Send button", not "click Send" — the article and the role
# noun are how the request is phrased, never part of the control's own label.
_QUERY_NOISE = re.compile(
    r"^\s*(?:the|a|an|that|this)\s+|"
    r"\s+(?:button|buttons|link|tab|menu|menu item|item|field|box|text box|"
    r"checkbox|icon|option|control|entry)\s*$", re.I)


def _normalise_query(q: str) -> str:
    out = (q or "").strip()
    for _ in range(3):                     # "the save button" -> "save"
        stripped = _QUERY_NOISE.sub("", out).strip()
        if stripped == out:
            break
        out = stripped
    return out or (q or "").strip()


# For the penalty only: drop articles and the words that describe a control's
# KIND, but keep everything that identifies WHICH one ("tab", "menu", "settings"),
# because that is exactly what distinguishes "Close tab" from "Close".
_ROLE_WORDS = {"the", "a", "an", "that", "this", "button", "buttons", "link",
               "links", "field", "box", "checkbox", "icon", "option", "control",
               "entry", "item"}


def _content_words(query: str) -> set:
    return {w for w in re.split(r"\W+", (query or "").lower())
            if len(w) > 1 and w not in _ROLE_WORDS}


def _score(query: str, name: str) -> float:
    """Best of the raw and stripped phrasings. Stripping is greedy on purpose
    ("the close tab button" -> "close"), so the raw form has to compete too or
    "Close tab" would lose to a plain "Close"."""
    base = max(_score_one(query, name), _score_one(_normalise_query(query), name))
    # A shorter name can match the stripped query exactly ("close" == "Close")
    # and so beat the control the user actually meant ("Close tab"). Penalise
    # every word they said that the name does not contain.
    said = _content_words(query)
    has = {w for w in re.split(r"\W+", name.lower()) if w}
    return base - 0.15 * len(said - has)


def _score_one(query: str, name: str) -> float:
    q, n = (query or "").lower().strip(), name.lower().strip()
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    if n.startswith(q) or q.startswith(n):
        return 0.9
    if q in n:
        return 0.8
    qw = {w for w in re.split(r"\W+", q) if w}
    nw = {w for w in re.split(r"\W+", n) if w}
    if not qw or not nw:
        return 0.0
    return 0.7 * len(qw & nw) / len(qw)


async def list_controls(window: str = "", clickable_only: bool = True,
                        limit: int = 40) -> dict:
    """What this window actually offers, straight from Windows."""
    try:
        found = await asyncio.to_thread(_collect, window or None)
    except Exception as e:
        log.warning("UIA walk failed", exc_info=True)
        return {"error": f"couldn't read that window's controls ({type(e).__name__})"}
    if not found:
        return {"error": f"no controls found in {window or 'the foreground window'} — "
                         "it may draw its own interface, so use a grid screenshot instead"}
    items = [{k: v for k, v in c.items() if k != "_el"} for c in found
             if c["clickable"] or not clickable_only]
    return {"window": window or "foreground", "count": len(items),
            "controls": items[:max(1, min(80, limit))]}


async def click_control(name: str, window: str = "", double: bool = False) -> dict:
    """Click the control called `name` — by its real identity, not coordinates."""
    from tools.input_tools import _locked
    if _locked():
        return {"error": "the PC is locked — Windows blocks clicking until it's unlocked"}
    if not (name or "").strip():
        return {"error": "which control should I click?"}
    try:
        found = await asyncio.to_thread(_collect, window or None)
    except Exception as e:
        return {"error": f"couldn't read that window's controls ({type(e).__name__})"}
    if not found:
        return {"error": "that window publishes no controls — try a grid screenshot and "
                         "a cell instead"}
    ranked = sorted(((_score(name, c["name"]), c) for c in found),
                    key=lambda p: p[0], reverse=True)
    best_score, best = ranked[0]
    if best_score < 0.55:
        near = ", ".join(c["name"] for _, c in ranked[:5])
        return {"error": f"I don't see a control called {name}. The closest are: {near}"}
    # Prefer the control's own Invoke: it works even when something overlaps it.
    def _invoke() -> bool:
        import comtypes
        comtypes.CoInitialize()
        try:
            pattern = best["_el"].GetCurrentPattern(10000)    # UIA_InvokePatternId
            if not pattern:
                return False
            from comtypes.gen import UIAutomationClient as UIA
            pattern.QueryInterface(UIA.IUIAutomationInvokePattern).Invoke()
            return True
        except Exception:
            return False

    def _mouse() -> None:
        import win32api
        import win32con
        x, y = best["x"], best["y"]
        win32api.SetCursorPos((x, y))
        for _ in range(2 if double else 1):
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)

    # Invoke is a call INTO the other app, and an app that is busy — or showing
    # a modal dialog — can simply never answer it. Unbounded, that hangs the
    # whole tool until its timeout and the click never happens at all, which is
    # the worst outcome of the three. So give Invoke a short go, then use the
    # mouse, which nothing can block.
    how = "clicked"
    try:
        if await asyncio.wait_for(asyncio.to_thread(_invoke), timeout=INVOKE_TIMEOUT_S):
            how = "invoked"
        else:
            await asyncio.to_thread(_mouse)
    except asyncio.TimeoutError:
        log.warning("UIA Invoke on %r did not answer in %.1fs - clicking it instead",
                    best["name"], INVOKE_TIMEOUT_S)
        how = "clicked (invoke timed out)"
        await asyncio.to_thread(_mouse)
    await bus.emit("remote_input", action="click_control", control=best["name"], how=how)
    return {"clicked": best["name"], "role": best["role"], "how": how,
            "match": round(best_score, 2), "x": best["x"], "y": best["y"]}


def register_all() -> None:
    registry.register(Tool(
        name="list_controls",
        description="List the buttons, fields, links and menu items a window actually "
                    "offers, read from Windows itself. Use before clicking by name, or "
                    "when the user asks what's on screen in an app.",
        parameters={"type": "object", "properties": {
            "window": {"type": "string", "description": "window title fragment; blank = foreground"},
            "clickable_only": {"type": "boolean"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 80}},
            "required": []},
        risk=Risk.SAFE, handler=list_controls, timeout=30))
    registry.register(Tool(
        name="click_control",
        description="Click a named control — 'click the Send button', 'click Save'. Exact "
                    "and survives the window moving; prefer this over grid coordinates.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "the control's visible label"},
            "window": {"type": "string"},
            "double": {"type": "boolean"}},
            "required": ["name"]},
        risk=Risk.MEDIUM, handler=click_control, timeout=30))
