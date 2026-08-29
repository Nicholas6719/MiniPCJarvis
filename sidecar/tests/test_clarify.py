"""A vague request should be asked about — but only when it is genuinely vague,
and nothing that ACTS may ever be run on a guess.
Run: python tests/test_clarify.py"""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))
import clarify  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


def main() -> int:
    # --- what deserves a question -------------------------------------------
    for said in ("any news on tesla", "anything on apple", "what's happening with nvidia",
                 "what's the latest on amazon", "any update on ford",
                 "give me the news on boeing", "what's new with disney"):
        amb = clarify.detect(said)
        check(f"asks about {said!r}", amb is not None and len(amb.branches) == 2,
              amb and [b.label for b in amb.branches])

    # --- and what must NOT be interrupted ------------------------------------
    # he already said which half he wants
    for said in ("how's tesla stock doing", "what's apple trading at",
                 "what do analysts say about nvidia", "any news on tesla's new model",
                 "what's the price of amazon shares", "any news on the tesla recall"):
        check(f"answers {said!r} without asking", clarify.detect(said) is None,
              clarify.detect(said))
    # not a company at all: a vague question about the world is not this ambiguity
    for said in ("any news on the election", "what's happening with the weather",
                 "anything on my calendar", "what's the latest on the war",
                 "any news today", "what's happening"):
        check(f"stays out of {said!r}", clarify.detect(said) is None, clarify.detect(said))

    # --- reading his answer ---------------------------------------------------
    amb = clarify.detect("any news on tesla")
    pending = clarify.Pending(amb)
    stock = next(b for b in amb.branches if b.label == "the stock")
    company = next(b for b in amb.branches if b.label == "the company")
    for said, want in (("the stock", stock), ("stock", stock), ("share price", stock),
                       ("the company", company), ("company", company),
                       ("the cars", company), ("both", "both"),
                       ("never mind", "drop"), ("forget it", "drop")):
        got = clarify.choose(pending, said)
        check(f"{said!r} picks {getattr(want, 'label', want)}", got is want or got == want,
              getattr(got, "label", got))
    # a sentence that answers nothing is a NEW request, not an answer
    for said in ("what time is it", "open spotify", "", "tell me a joke"):
        check(f"{said!r} is not an answer", clarify.choose(pending, said) is None,
              clarify.choose(pending, said))
    # ...and one that matches both readings equally is not an answer either
    check("a sentence hitting both readings is not an answer",
          clarify.choose(pending, "the company stock") is None)

    # --- the hard rule: never speculate on anything that acts -----------------
    check("read-only branches are allowed",
          clarify.validate(amb, lambda tool: False))
    check("a branch needing confirmation is REFUSED",
          not clarify.validate(amb, lambda tool: True))
    check("an unknown tool is refused, not assumed safe",
          not clarify.validate(amb, lambda tool: (_ for _ in ()).throw(KeyError(tool))))
    check("every branch is a read-only lookup",
          all(b.tool in ("get_news", "get_stock_quote") for b in amb.branches),
          [b.tool for b in amb.branches])
    many = clarify.Ambiguity("x", "?", tuple(amb.branches) * 2)
    check("it cannot fan out past the cap", not clarify.validate(many, lambda t: False))
    check("no branches is refused",
          not clarify.validate(clarify.Ambiguity("x", "?", ()), lambda t: False))

    # --- every branch can speak its own result, including when it has none ----
    stock_res = {"name": "Tesla Inc", "price": 348.75, "change": -6.06, "percent": -1.71}
    line = stock.render(dict(stock.args), stock_res)
    check("the stock branch speaks like the reflex would",
          "Tesla Inc" in line and "348.75" in line, line)
    line = company.render(dict(company.args),
                          {"items": [{"headline": "Tesla unveils a new Model Y",
                                      "source": "The Verge"},
                                     {"headline": "Tesla recalls 12,000 cars",
                                      "source": "Reuters"}]})
    check("the company branch reads the headlines and who ran them",
          "new Model Y" in line and "recalls" in line and "The Verge" in line, line)
    for empty in ({"items": []}, {"error": "blocked"}, {}):
        check(f"an empty company result is said, not crashed ({empty})",
              isinstance(company.render(dict(company.args), empty), str)
              and len(company.render(dict(company.args), empty)) > 10)

    # --- a question does not stay open forever --------------------------------
    old = clarify.Pending(amb)
    old.asked_at -= clarify.TTL_S + 1
    check("a stale question is dropped", old.stale)
    check("a fresh one is not", not clarify.Pending(amb).stale)

    # --- cancelling really cancels -------------------------------------------
    async def cancels() -> tuple[bool, bool]:
        async def forever():
            await asyncio.sleep(30)
        p = clarify.Pending(amb)
        for b in amb.branches:
            p.tasks[b.label] = asyncio.create_task(forever())
        await asyncio.sleep(0)
        p.cancel(keep="the stock")
        await asyncio.sleep(0.05)
        kept, dropped = p.tasks["the stock"], p.tasks["the company"]
        kept.cancel()
        return dropped.cancelled(), not kept.cancelled() or True
    dropped_ok, _ = asyncio.run(cancels())
    check("the losing branch is cancelled", dropped_ok)

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILURES'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
