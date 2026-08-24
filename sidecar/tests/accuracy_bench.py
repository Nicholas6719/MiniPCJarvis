"""Factual accuracy + run-to-run consistency of the local model.

Why this exists: "how many bones are in the human body" answered 206 on one run and
"fifty-two" on the next. Sampling was never sent to llama-server, so its chat defaults
(temperature 0.8, top_p 0.95) applied -- creative-writing sampling on an assistant whose
job is mostly to state facts.

Run: python tests/accuracy_bench.py PORT TOKEN [REPEATS] [TEMP ...]
With no TEMPs it measures whatever the app is configured to use.
"""
import asyncio
import re
import sys

import httpx

port, tok = sys.argv[1], sys.argv[2]
REPEATS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
TEMPS = [float(x) for x in sys.argv[4:]]
H = {"X-Jarvis-Token": tok}
BASE = f"http://127.0.0.1:{port}"

# Answer patterns accept digits or words, since the model may spell numbers out.
CASES = [
    ("how many bones are in the adult human body", r"\b206\b|two hundred (and )?six"),
    ("what is the capital of australia", r"canberra"),
    ("who wrote the novel nineteen eighty-four", r"orwell"),
    ("how many planets are in the solar system", r"\b8\b|eight"),
    ("what year did the berlin wall fall", r"1989|nineteen eighty[- ]?nine"),
    ("what is the boiling point of water in celsius", r"\b100\b|hundred"),
    ("how many continents are there", r"\b7\b|seven"),
    ("what is the chemical symbol for gold", r"\bau\b"),
    ("who painted the mona lisa", r"leonardo|da vinci"),
    ("what is the largest planet in the solar system", r"jupiter"),
    ("how many sides does a hexagon have", r"\b6\b|six"),
    ("who directed the film jaws", r"spielberg"),
    ("what is the tallest mountain on earth", r"everest"),
    ("how many players are on a soccer team on the field", r"\b11\b|eleven"),
    ("what is the freezing point of water in fahrenheit", r"\b32\b|thirty[- ]?two"),
    ("in what year did world war two end", r"1945|nineteen forty[- ]?five"),
    ("what is the square root of 144", r"\b12\b|twelve"),
    ("how many minutes are in a day", r"1,?440|fourteen hundred forty|one thousand four hundred"),
    ("what is the chemical formula for water", r"h2o|h₂o|h two o"),
    ("how many degrees are in a circle", r"\b360\b|three hundred (and )?sixty"),
]


def norm(s: str) -> str:
    """The model writes non-breaking hyphens and curly quotes; a matcher that trips over
    those reports failures that are its own fault, not the model's."""
    for ch, plain in (("‑", "-"), ("‐", "-"), ("–", "-"), ("—", "-"),
                      ("’", "'"), (" ", " ")):
        s = s.replace(ch, plain)
    return s


async def ask(client, text, sampling):
    body = {"text": text, "max_tokens": 200}
    if sampling:
        body["sampling"] = sampling
    r = await client.post(BASE + "/debug/llm_probe", headers=H, json=body, timeout=180)
    r.raise_for_status()
    return r.json()["reply"]


async def run(sampling, label):
    correct = consistent = 0
    misses = []
    async with httpx.AsyncClient() as client:
        for q, pat in CASES:
            answers = []
            for _ in range(REPEATS):
                try:
                    answers.append(await ask(client, q, sampling))
                except Exception as e:
                    answers.append(f"<error {e}>")
            hits = [bool(re.search(pat, norm(a), re.I)) for a in answers]
            correct += sum(hits)
            keys = {norm(x).lower().strip(" .") for x in answers}
            if len(keys) == 1:
                consistent += 1
            if not all(hits):
                misses.append((q, [a[:60] for a, h in zip(answers, hits) if not h]))
    total = len(CASES) * REPEATS
    print(f"\n== {label}")
    print(f"   accuracy    {correct}/{total} = {100 * correct / total:.0f}%")
    print(f"   consistent  {consistent}/{len(CASES)} questions answered the same way every time")
    for q, bad in misses:
        print(f"   MISS  {q}")
        for b in bad:
            print(f"           {b}")
    return correct / total, consistent / len(CASES)


async def main():
    if not TEMPS:
        await run(None, "as configured")
        return 0
    results = []
    for t in TEMPS:
        acc, con = await run({"temperature": t, "top_p": 0.9, "min_p": 0.05}, f"temperature {t}")
        results.append((t, acc, con))
    print("\n== sweep")
    for t, acc, con in results:
        print(f"   temp {t:<5} accuracy {100 * acc:3.0f}%   consistency {100 * con:3.0f}%")
    return 0

sys.exit(asyncio.run(main()))
