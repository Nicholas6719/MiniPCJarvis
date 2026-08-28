"""No two skills may own the same canonical seed.

Seeds are canonicalised before they are embedded, so a seed can quietly turn into a
different sentence than the one written down. Adding "no more for now" to the sleep skill
was enough to break voice corrections: the correction rule rewrites anything starting
with "no" to "no i meant ACTION", so sleep took ownership of that canonical form and
"no, I meant what time is it" started dismissing him at confidence 1.00.

router.load() resolves such a clash silently with setdefault - first skill listed wins -
so nothing fails loudly. This test is the loud failure.

Run: python tests/seed_collisions.py
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os, tempfile  # noqa: E402
# never touch the real jarvis.db: the gates get a throwaway file that
# brain.load() re-seeds from the SKILLS list
os.environ.setdefault("JARVIS_DB", os.path.join(tempfile.mkdtemp(), "gate.db"))

from brain.router import _norm  # noqa: E402
from brain.skills import SKILLS  # noqa: E402

owners: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
for sk in SKILLS:
    for seed in sk.seeds:
        owners[_norm(seed)].append((sk.name, seed))

clashes = {c: v for c, v in owners.items()
           if len({name for name, _ in v}) > 1}

print(f"  {sum(len(sk.seeds) for sk in SKILLS)} seeds across {len(SKILLS)} skills"
      f" -> {len(owners)} canonical forms")
for canon, v in clashes.items():
    print(f"  CLASH  {canon!r}")
    for name, seed in v:
        print(f"           {name:12} <- {seed!r}")

# A seed that canonicalises to the same thing as a DIFFERENT seed of its OWN skill is
# merely redundant, not dangerous, so only cross-skill clashes fail.
print("\n" + ("ALL PASS" if not clashes else f"{len(clashes)} CROSS-SKILL SEED CLASHES"))
sys.exit(1 if clashes else 0)
