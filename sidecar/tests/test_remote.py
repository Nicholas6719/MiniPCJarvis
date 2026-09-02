"""Telegram bridge gate — the security-critical paths, offline (fake API, fake
config). A stranger must get SILENCE; only the paired chat commands JARVIS.
Run: python tests/test_remote.py"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import remote_telegram as rt  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class FakeConfig:
    def __init__(self):
        self.data = {"remote": {"telegram_chat_id": None}}

    def get(self, *keys, default=None):
        cur = self.data
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def set(self, *keys, value):
        cur = self.data
        for k in keys[:-1]:
            cur = cur.setdefault(k, {})
        cur[keys[-1]] = value


def msg(chat, text, sender=None):
    return {"update_id": 1, "message": {"chat": {"id": chat},
            "from": {"id": sender if sender is not None else chat}, "text": text}}


async def main() -> int:
    rt.config = FakeConfig()
    b = rt.TelegramBridge()
    b.token = "test:token"
    b.pairing_code = "424242"
    calls = []

    async def fake_api(method, timeout=30, **params):
        calls.append((method, params))
        return {}
    b._api = fake_api

    turns = []

    async def fake_turn(text):
        turns.append(text)
    b._remote_turn = fake_turn

    # --- unpaired: wrong code is silence, right code pairs -------------------
    await b._handle_update(msg(111, "hello jarvis"))
    check("unpaired stranger gets silence", not calls and not turns)
    await b._handle_update(msg(111, "/pair 000000"))
    check("wrong pairing code ignored", not calls and rt.config.get("remote", "telegram_chat_id") is None)
    await b._handle_update(msg(111, "/pair 424242"))
    check("correct code pairs the chat", rt.config.get("remote", "telegram_chat_id") == 111)
    check("pairing acknowledged", any(m == "sendMessage" for m, _ in calls))
    check("pairing code single-use", b.pairing_code is None)

    # --- paired: only chat 111 commands him ----------------------------------
    calls.clear()
    await b._handle_update(msg(222, "open notepad"))
    check("stranger message -> zero API calls", not calls and not turns)
    await b._handle_update(msg(111, "what time is it"))
    check("owner message runs a turn", turns == ["what time is it"])
    # a forwarded message with a different sender in the owner chat still counts
    # only when the SENDER is the owner
    await b._handle_update(msg(111, "rm everything", sender=333))
    check("owner chat + foreign sender rejected", turns == ["what time is it"])

    # --- confirmation buttons -----------------------------------------------
    from tools.registry import registry
    fut = asyncio.get_running_loop().create_future()
    registry._pending["cid123"] = fut
    calls.clear()
    await b._handle_update({"update_id": 2, "callback_query": {
        "id": "cb1", "from": {"id": 111}, "data": "confirm:cid123:yes"}})
    check("DO IT button resolves the gate", fut.done() and fut.result() is True)
    check("button press acknowledged", any(m == "answerCallbackQuery" for m, _ in calls))
    fut2 = asyncio.get_running_loop().create_future()
    registry._pending["cid456"] = fut2
    await b._handle_update({"update_id": 3, "callback_query": {
        "id": "cb2", "from": {"id": 999}, "data": "confirm:cid456:yes"}})
    check("stranger cannot press DO IT", not fut2.done())
    registry._pending.pop("cid456", None)

    # --- voice note gets an honest deferral, not silence ---------------------
    calls.clear()
    await b._handle_update({"update_id": 4, "message": {
        "chat": {"id": 111}, "from": {"id": 111}, "voice": {"file_id": "x"}}})
    check("voice note answered honestly", any(m == "sendMessage" for m, _ in calls))

    # --- SHOW HIM, DON'T TELL HIM -------------------------------------------
    # His instruction: "if I ever ask Jarvis to do anything on my computer —
    # minimizing something, opening something, deleting something — he should
    # always send me a screenshot so I can see that he had done it." From the
    # phone he cannot check; "File removed, sir." is a claim, a picture is
    # evidence, and it is also the context for whatever he says next. In the
    # exchange that prompted this he had to type "show me" after every action.
    b2 = rt.TelegramBridge()
    for tool in ("minimize_window", "delete_file", "open_application",
                 "close_window", "move_file", "type_text"):
        check(f"{tool} counts as changing his desktop",
              tool in b2.CHANGED_THE_DESKTOP)
    for tool in ("remember_fact", "web_search", "get_system_stats",
                 "watch_metric", "recall"):
        check(f"{tool} does NOT, so it sends no photo",
              tool not in b2.CHANGED_THE_DESKTOP,
              "a screenshot after remembering a fact is noise")

    # The collector has to record what ran, or there is nothing to decide with.
    b2._collect = {"text": ""}
    b2._on_event({"kind": "tool_call", "status": "success",
                  "tool": "minimize_window", "result": {}})
    check("the turn records what it actually did",
          "minimize_window" in (b2._collect.get("did") or []), b2._collect)

    # --- and it does not narrate the picture --------------------------------
    # "He doesn't need to say screenshot saved every time he does it — I'll know
    # he took a screenshot by him actually showing me."
    from brain.skills import say_screenshot
    check("a bare screenshot says nothing at all",
          say_screenshot({}, {"path": "x.png"}) == "",
          say_screenshot({}, {"path": "x.png"}))
    check("...but a failure still speaks up",
          "couldn't" in say_screenshot({}, {"error": "no"}))
    check("...and WHERE it was saved survives, since a picture cannot say that",
          "desktop" in say_screenshot({"destination": "desktop"}, {"path": "x.png"}))

    # --- a miss must not be a dead end --------------------------------------
    # "Remove that screenshot and whisper flow from my desktop" got "I'm sorry,
    # sir." The file was there — as "Wispr Flow.lnk", which is not how he spells
    # it and never will be. delete_file took an exact path, missed, and said so
    # in a way the model could do nothing with, so it apologised and stopped.
    import tempfile
    from pathlib import Path

    import tools.file_tools as ft

    tmp = Path(tempfile.mkdtemp())
    (tmp / "Wispr Flow.lnk").write_text("x")
    (tmp / "Screenshot 2026-09-02.png").write_text("x")
    real_roots = ft.roots
    ft.roots = lambda: {"desktop": tmp}
    try:
        for said in ("Wispr Flow", "wisper flow", "whisper flow", "WisprFlow"):
            got = ft._near_matches(said)
            check(f"{said!r} finds the shortcut he means",
                  any("Wispr Flow" in g for g in got), got)
        check("a name matching nothing stays empty",
              ft._near_matches("zzzznothingatall") == [])
        check("...and two letters are not enough to guess from",
              ft._near_matches("ab") == [])
        # The tool itself must hand the alternatives back, or the model has
        # nothing to try on its next round.
        out = await ft.delete_file("Wisper Flow")
        check("a missed delete offers the real path instead of giving up",
              bool(out.get("did_you_mean")), out)
        check("...and still reports it as not found, rather than pretending",
              "not found" in (out.get("error") or ""), out)
        check("...and deleted nothing on a guess",
              (tmp / "Wispr Flow.lnk").exists())
    finally:
        ft.roots = real_roots

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
