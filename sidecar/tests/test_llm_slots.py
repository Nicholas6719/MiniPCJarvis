"""The conversation owns llama slot 0; side calls go to slot 1.

llama-server's prompt cache is per slot. With one slot, a fact-classifier call
after a factual answer replaced the conversation's cached prefix, and the next
turn re-read the whole ~6k-token prompt: 20+ s to the first token, measured on
2026-09-04. Two slots, pinned by `id_slot`, keep them apart. Offline: the HTTP
client is faked and the request body inspected.

Run: python tests/test_llm_slots.py
"""
import asyncio
import contextlib
import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "slots.db"))

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


class _Resp:
    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        yield "data: [DONE]"


class _FakeClient:
    bodies: list[dict] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @contextlib.asynccontextmanager
    async def stream(self, method, url, json=None):
        _FakeClient.bodies.append(json)
        yield _Resp()


async def main() -> int:
    import httpx
    from llm import provider as P
    from llm.llama_server import llama

    real = httpx.AsyncClient
    httpx.AsyncClient = _FakeClient
    try:
        llm = P.LocalLLM()

        async def run(**kw):
            _FakeClient.bodies.clear()
            async for _ in llm.stream([{"role": "user", "content": "hi"}], **kw):
                pass
            return _FakeClient.bodies[-1]

        print("\n-- with two slots --")
        llama.n_slots = 2
        b = await run(slot=0)
        check("the conversation is pinned to slot 0", b.get("id_slot") == 0, b.get("id_slot"))
        b = await run()
        check("a side call defaults to slot 1", b.get("id_slot") == 1, b.get("id_slot"))
        b = await run(slot=7)
        check("...and never past the last slot", b.get("id_slot") == 1, b.get("id_slot"))

        # THREE slots (2026-09-05): the no-tools conversation gets a slot of its
        # own, and side calls go to the last one. With two, the news summaries
        # and the market story evicted the no-tools prefix - a knowledge
        # question after a brief re-read 1,813 tokens, 7.4 s to the first word.
        print("\n-- with three slots --")
        llama.n_slots = 3
        b = await run(slot=0)
        check("the tools shape is slot 0", b.get("id_slot") == 0, b.get("id_slot"))
        b = await run(slot=1)
        check("the no-tools shape is slot 1", b.get("id_slot") == 1, b.get("id_slot"))
        b = await run()
        check("a side call defaults to the LAST slot, 2", b.get("id_slot") == 2, b.get("id_slot"))
        check("the prefix cache is still asked for", b.get("cache_prompt") is True)

        print("\n-- with one slot --")
        llama.n_slots = 1
        b = await run(slot=0)
        check("no id_slot on a one-slot server (it would be refused)", "id_slot" not in b, b.keys())
        b = await run()
        check("...for side calls too", "id_slot" not in b)

        print("\n-- the two conversation call sites say so --")
        import orchestrator as O
        src = inspect.getsource(O.Orchestrator._llm_with_tools)
        check("_llm_with_tools pins the tools shape to slot 0 and the no-tools shape to slot 1",
              "slot=0 if round_tools is not None else 1" in src,
              "two prompt shapes on one slot evict each other: 21 s per switch")
        src = inspect.getsource(O.Orchestrator._warm_prompts)
        check("the boot warm primes each shape on its own slot",
              "slot=0 if tools is not None else 1" in src)
        from config import DEFAULTS
        args = DEFAULTS["llm"]["models"]["gpt-oss-20b"]["args"]
        check("gpt-oss runs three slots", args[args.index("-np") + 1] == "3", args)
        check("...with a context big enough for three of the old size",
              int(DEFAULTS["llm"]["models"]["gpt-oss-20b"]["context"]) >= 49152)
    finally:
        httpx.AsyncClient = real
        llama.n_slots = 1

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
