"""Naming the site is the instruction to show it (2026-09-22).

"Can you please find me a tower fan and heater on Amazon?" went to a web
search and JARVIS's hidden browser; "can you show me?" opened another hidden
page. Now: the site's own results open in HIS browser at once and he hears
the top of the list. Run: python tests/test_site_find.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "site.db"))
ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail if not cond else ''}")
    if not cond:
        fails.append(name)


AMAZON_TEXT = """Skip to main content
Results
Check each product page for other buying options.
Sponsored
Lasko Ellipse Ceramic Tower Heater & Fan, 24 Inch, Digital Display, Remote Control
4.4 out of 5 stars 2,113
$81.00
List: $99.99
FREE delivery Thu, Sep 25
Dreo Tower Fan and Heater Combo, 2-in-1 Space Heater with Oscillation
4.6 out of 5 stars 812
$129.99
Typical: $149.99
Vornado OSCTH1 Whole Room Tower Heater with Fan
$154.99
"""


def main() -> int:
    from brain import skills as K
    from tools import site_tools as S

    print("\n-- the sentence he actually said --")
    s = K.slots_site_find("Can you please find me a Tower fan and heater on Amazon?")
    check("site and subject", s == {"site": "amazon", "query": "tower fan heater"}, s)
    s = K.slots_site_find("find a mechanical keyboard on best buy")
    check("two-word sites", s and s["site"] == "bestbuy" and s["query"] == "mechanical keyboard", s)
    s = K.slots_site_find("look for a used graphics card on ebay")
    check("ebay", s and s["site"] == "ebay" and "graphics card" in s["query"], s)
    s = K.slots_site_find("what's trending on reddit")
    check("no subject -> the front page", s == {"site": "reddit", "query": ""}, s)
    check("no site -> not this skill", K.slots_site_find("find me a tower fan and heater") is None)

    print("\n-- the tool opens it where he can see it, and reads the top --")
    check("Amazon search url", S.url_for("amazon", "tower fan heater") == "https://www.amazon.com/s?k=tower%20fan%20heater")
    check("Best Buy search url", "bestbuy.com" in S.url_for("bestbuy", "keyboard"))
    check("front page when there is no query", S.url_for("reddit", "") == "https://www.reddit.com/r/all/")
    rows = S.extract_results("amazon", AMAZON_TEXT)
    check("three products with prices, in order", [r["title"][:12] for r in rows] == ["Lasko Ellips", "Dreo Tower F", "Vornado OSCT"], rows)
    check("...with the real prices", [r["price"] for r in rows] == ["81.00", "129.99", "154.99"], rows)
    check("a bot wall yields nothing, not a guess", S.extract_results("amazon", "Sorry, something went wrong. Type the characters you see.") == [])
    opened, gone = [], []

    def fake_open(url):
        opened.append(url); return {"opened": url}

    class _B:
        async def goto(self, url):
            gone.append(url); return {"url": url, "title": "Amazon.com : tower fan heater", "text": AMAZON_TEXT}

    import tools.windows_tools as WT
    import browser.session as BS
    WT.open_url, BS.browser = fake_open, _B()
    res = asyncio.run(S.find_on_site("amazon", "tower fan heater"))
    check("his browser gets the page at once", opened == ["https://www.amazon.com/s?k=tower%20fan%20heater"], opened)
    check("...and the hidden read finds the results", res["count"] == 3 and res["opened"], res)
    first = K.say_site_find({"site": "amazon", "query": "tower fan heater"}, {})
    check("what he hears first, before the read", first == "Amazon's results for tower fan heater are up in your browser, sir.", first)
    said = K.say_site_find({}, res)
    check("...then the top of the list", said.startswith("Top of the list: Lasko Ellipse") and "81.00 dollars" in said, said)

    class _Wall:
        async def goto(self, url):
            return {"url": url, "text": "Type the characters you see in this image."}

    BS.browser = _Wall()
    res = asyncio.run(S.find_on_site("amazon", "tower fan heater"))
    said = K.say_site_find({}, res)
    check("a bot wall: the page is still up and nothing is invented", said == "", said)
    res = asyncio.run(S.find_on_site("reddit", ""))
    check("the front page: 'Reddit is up in your browser'", K.say_site_find({"site": "reddit", "query": ""}, {}) == "Reddit is up in your browser, sir.")

    print("\n-- wired: skill, tool, prompt --")
    from brain.skills import SKILL_BY_NAME
    sk = SKILL_BY_NAME["site_browse"]
    check("the skill runs find_on_site, speaks first, and speaks itself", sk.tool == "find_on_site" and not sk.llm_after and sk.speak_first)
    rows = S.clean_rows([{"title": "Lasko Ellipse Ceramic Tower Heater & Fan", "price": "$81.00"}, {"title": "x", "price": "$1"}, {"title": "Dreo Tower Fan and Heater Combo", "price": "$129.99"}])
    check("DOM rows are cleaned: short titles dropped, prices plain", rows == [{"title": "Lasko Ellipse Ceramic Tower Heater & Fan", "price": "81.00"}, {"title": "Dreo Tower Fan and Heater Combo", "price": "129.99"}], rows)
    check("a category and a price facet are not a product", S.extract_results("amazon", "Tools & Home Improvement\n$10\nHome & Kitchen\n$25") == [])
    check("registered at boot", "site_tools.register_all()" in (ROOT / "main.py").read_text(encoding="utf-8"))
    check("the model is told: never the hidden browser for a product",
          "call find_on_site" in (ROOT / "llm" / "prompts.py").read_text(encoding="utf-8"))

    print("\n-- a model call in flight is not silence; a cold cache is re-warmed --")
    orch = (ROOT / "orchestrator.py").read_text(encoding="utf-8")
    prov = (ROOT / "llm" / "provider.py").read_text(encoding="utf-8")
    check("the provider marks a call in flight", "LocalLLM.working_since = _time.time()" in prov and prov.count("LocalLLM.working_since = 0.0") >= 2)
    check("the deaf watch treats it as work", 'getattr(local_llm, "working_since", 0.0)' in orch and "quiet_for = 0.0" in orch)
    check("both prefixes re-warmed hourly while idle", '_last_prompt_warm", 0.0) >= 3600' in orch and "await self._warm_prompts()" in orch)

    print()
    if fails:
        print(f"FAILED: {len(fails)}: {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
