"""Send the model the tools this turn could plausibly need, not all sixty.

Every extra tool schema is prompt tokens the model reads before it says a word,
and one more wrong thing it can pick. We already embed every utterance for brain
routing, so the same vector can rank tools for free.

Deliberately generous: this is a SPEED optimisation, not a gate. It keeps a wide
shortlist, always includes the tools a turn is already using, and falls back to
the full set whenever anything is uncertain. A tool wrongly withheld is a
capability that silently disappears — far worse than a slightly longer prompt.
"""
from __future__ import annotations

import logging

import numpy as np

from config import config

log = logging.getLogger("jarvis.shortlist")

# Always offered: cheap, universally useful, or the natural next step of a turn.
ALWAYS = {
    "web_search", "research", "fetch_page",
    "get_system_stats", "take_screenshot", "recall", "remember_fact",
    "list_folder", "find_files", "preview_file",
    # ACTING ON A FILE, not just finding one. This omission produced the exact
    # failure the docstring above warns about, in one Telegram exchange:
    #
    #   "remove the screenshot from my desktop"      -> "File removed, sir."
    #   "now remove Wispr Flow from the desktop too" -> "I don't have a tool to
    #                                                   delete files directly."
    #
    # It was not lying. `delete_file` ranked inside the top 30 for the first
    # sentence and outside it for the second, because "Wispr Flow" pulls the
    # embedding away from anything about deleting — so the model really was
    # handed no way to delete, one message after doing it. Being able to FIND a
    # file always and ACT on it only sometimes is not a coherent capability.
    "delete_file", "move_file", "rename_file",
    # Same argument for the desktop itself. "Minimise this", "close that",
    # "bring my windows back" are among the most ordinary things he asks, and
    # they lose to any sentence with a proper noun in it.
    "list_windows", "focus_window", "minimize_window", "maximize_window",
    "close_window", "show_desktop", "restore_windows",
    "open_application", "close_application",
}

MIN_TOOLS = 16          # never send fewer than this many
MAX_TOOLS = 30          # ...nor more. 60 -> ~34 sent: most of the win, with headroom
                        # (the worst measured case, "switch to discord" -> focus_window,
                        #  ranks 23, so a 24-wide cut sat one place from dropping it)


class ToolShortlist:
    # THE BLOCK MUST BE A PREFIX OF ITSELF, TURN TO TURN. llama.cpp caches the
    # prompt as a prefix and the tools sit before the history, so one tool
    # swapped or reordered re-processes everything after it: ~800 tokens and
    # ~3.3 s before the first token on an ordinary turn in the real log, against
    # ~100 tokens when the prefix holds. So a tool once offered stays offered,
    # in first-seen order, and new ones are APPENDED — the previous block is a
    # literal prefix of the next and the model only reads what is new. Past
    # MAX_STICKY the least recently wanted go, down to LOW_WATER in one cut,
    # and that one turn pays.
    #
    # WITH HYSTERESIS, OR IT NEVER HOLDS. The first cap was 48 with no low
    # water: each new question brings up to thirty tools, so the block went
    # over the cap on the second distinct question and evicted on nearly every
    # turn after — and an eviction breaks the prefix at the first missing
    # tool. Measured on release 17: "who wrote hamlet" twice hit the cache
    # (733 then 128 tokens), "who painted the mona lisa" right after re-read
    # 4,506. Seventy-two tools is ~5k tokens, paid once and cached.
    #
    # AND THEN NO CAP AT ALL. Release 18 (72 / 48) still trimmed on nearly
    # every distinct question — the log shows "tool block trimmed" once a
    # minute — because a question brings up to thirty tools and the gap from
    # 48 to 72 is one question wide. Every trim was a 15-20 s re-read. The
    # whole registry is ~8.5k tokens, paid ONCE per session and then a pure
    # prefix; an eviction is never worth it. The cap stays as a mechanism (the
    # gate exercises it) but is set where it never fires.
    MAX_STICKY = 10_000
    LOW_WATER = 10_000

    def warm_block(self, registry) -> list[dict]:
        """The block a fresh session starts with, for the boot-time prompt warm:
        the always-offered tools in their sticky order, so the first real turn
        extends a cached prefix instead of paying for the whole prompt."""
        names = {n for n in ALWAYS if n in registry._tools}
        return [registry._tools[n].openai_schema() for n in self.stable_order(names)
                if n in registry._tools]

    def __init__(self) -> None:
        self._names: list[str] = []
        self._matrix: np.ndarray | None = None
        self._sticky: list[str] = []          # first-seen order
        self._last_wanted: dict[str, int] = {}
        self._tick = 0
        # Bumped whenever the block changes shape. The orchestrator re-warms
        # slot 0 in the background when it sees a version it has not warmed:
        # a changed block means the next tools-shape turn re-reads everything
        # after the change (2,185 of 7,370 tokens, 9 s, measured on release
        # 28), and that is a cost to pay while he is not waiting.
        self.block_version = 0

    def current_block(self, registry) -> list[dict]:
        """The block as it stands, for a re-warm: the sticky order, nothing added."""
        return [registry._tools[n].openai_schema() for n in self._sticky
                if n in registry._tools]

    def stable_order(self, wanted: set[str]) -> list[str]:
        """`wanted` merged into the session's sticky block, order preserved."""
        self._tick += 1
        for n in wanted:
            self._last_wanted[n] = self._tick
        before = len(self._sticky)
        for n in sorted(wanted):
            if n not in self._sticky:
                self._sticky.append(n)
        if len(self._sticky) > self.MAX_STICKY:
            low = min(self.LOW_WATER, self.MAX_STICKY)
            keep = set(sorted(self._sticky, key=lambda n: -self._last_wanted.get(n, 0))[:low])
            keep |= wanted                     # never drop what this turn asked for
            self._sticky = [n for n in self._sticky if n in keep]
            log.info("tool block trimmed to %d (cache re-read this turn)", len(self._sticky))
            self.block_version += 1
        elif len(self._sticky) != before:
            self.block_version += 1
        return list(self._sticky)

    async def build(self, registry) -> None:
        """Embed each tool once, from its name and description."""
        try:
            from memory.store import memory
            names, texts = [], []
            for name, tool in registry._tools.items():
                if name.startswith("_"):
                    continue
                names.append(name)
                texts.append(f"{name.replace('_', ' ')}. {tool.description}")
            if not names:
                return
            mat = await memory.embed_texts(texts)
            mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
            self._names, self._matrix = names, mat
            log.info("tool shortlist ready (%d tools embedded)", len(names))
        except Exception:
            log.exception("could not build the tool shortlist — sending all tools")
            self._names, self._matrix = [], None

    async def pick(self, registry, utterance: str, keep: set[str] | None = None) -> list[dict]:
        """Schemas for the tools worth offering this turn (all of them on any doubt)."""
        if not config.get("brain", "tool_shortlist", default=True):
            return registry.schemas()
        if self._matrix is None or not utterance.strip():
            return registry.schemas()
        try:
            from memory.store import memory
            q = (await memory.embed_texts([utterance]))[0]
            q = q / (np.linalg.norm(q) + 1e-9)
            sims = self._matrix @ q
            order = np.argsort(-sims)
            chosen: list[str] = []
            for i in order[:MAX_TOOLS]:
                if len(chosen) >= MAX_TOOLS:
                    break
                chosen.append(self._names[i])
            wanted = set(chosen) | ALWAYS | (keep or set())
            # top up to MIN_TOOLS by rank so a vague utterance still has room
            for i in order:
                if len(wanted) >= MIN_TOOLS:
                    break
                wanted.add(self._names[i])
            wanted = {n for n in wanted if n in registry._tools and not n.startswith("_")}
            out = [registry._tools[n].openai_schema() for n in self.stable_order(wanted)
                   if n in registry._tools]
            return out or registry.schemas()
        except Exception:
            log.exception("shortlist failed — sending all tools")
            return registry.schemas()


shortlist = ToolShortlist()
