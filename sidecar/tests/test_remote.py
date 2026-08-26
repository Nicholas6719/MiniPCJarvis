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

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
