"""Fact store gate (brain roadmap realm 1). Offline — the timeless classifier is
injected, no LLM or web needed. Run: python tests/test_facts.py"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from brain.facts import FactStore, REALM2  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


async def yes(_q, _a):
    return True


async def no(_q, _a):
    return False


SRC = [{"url": "https://en.wikipedia.org/wiki/Eiffel_Tower", "title": "Eiffel Tower"}]


async def main() -> int:
    tmp = os.path.join(tempfile.mkdtemp(), "facts_test.db")
    fs = FactStore(tmp)

    # --- realm 2 triggers: the changeable realm is never stored, never served ---
    for t in ["what is the latest spiderman movie", "current price of an rtx 5090",
              "who is the president of france", "what's the weather today",
              "who won the celtics game", "best mini pc", "tallest building in the world",
              "when is the next iphone coming out"]:
        check(f"realm2 trigger: {t[:40]!r}", REALM2.search(t) is not None)
    for t in ["how tall is the eiffel tower", "who directed iron man",
              "what year did apollo 11 land", "how many meters in a mile"]:
        check(f"timeless phrasing passes: {t[:40]!r}", REALM2.search(t) is None)

    # --- store + serve round trip -------------------------------------------
    ok = await fs.consider("how tall is the eiffel tower",
                           "The Eiffel Tower stands about 330 meters tall.", SRC,
                           "research", classify=yes)
    check("timeless sourced fact stores", ok)
    hit = await fs.lookup("how tall is the eiffel tower")
    check("exact question serves", hit is not None and "330" in hit["answer"])
    para = await fs.lookup("what's the height of the eiffel tower")
    check("paraphrase serves", para is not None, para)
    miss = await fs.lookup("how tall is the empire state building")
    check("different subject does NOT serve", miss is None, miss)

    # --- the gates that keep it honest --------------------------------------
    check("classifier NO is rejected",
          not await fs.consider("who directed iron man", "Jon Favreau directed Iron Man.",
                                SRC, "search", classify=no))
    check("no sources is rejected",
          not await fs.consider("how many meters in a mile", "About 1609 meters.", [],
                                "search", classify=yes))
    check("realm2 question is rejected even if classifier says yes",
          not await fs.consider("what is the latest spiderman movie",
                                "No Way Home.", SRC, "search", classify=yes))
    check("realm2 ANSWER text is rejected too",
          not await fs.consider("tell me about the gpu",
                                "The current best GPU is the RTX 5090.", SRC,
                                "search", classify=yes))
    check("essay-length answers are rejected",
          not await fs.consider("explain the eiffel tower", "x" * 500, SRC,
                                "search", classify=yes))
    check("duplicate question does not double-store",
          not await fs.consider("how tall is the eiffel tower",
                                "It is 330 meters.", SRC, "search", classify=yes))

    # --- serving a realm-2 utterance never hits the store --------------------
    await fs.consider("who directed iron man", "Jon Favreau.", SRC, "search", classify=yes)
    check("realm2 utterance is never served from the store",
          await fs.lookup("who is the latest director of iron man") is None)

    # --- audit plumbing -------------------------------------------------------
    due = fs.due_for_audit()
    check("audit sees stored facts", len(due) >= 2, len(due))
    fs.demote(due[0]["id"], "test")
    check("demoted fact stops serving",
          await fs.lookup(due[0]["question"]) is None, due[0]["question"])
    before = due[1]["verified_ts"]
    fs.mark_verified(due[1]["id"])
    after = [f for f in fs.list_all() if f["id"] == due[1]["id"]][0]["verified_ts"]
    check("mark_verified bumps the stamp", after >= before)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
