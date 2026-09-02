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
    def __init__(self) -> None:
        self._names: list[str] = []
        self._matrix: np.ndarray | None = None

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
            out = [t.openai_schema() for n, t in registry._tools.items()
                   if n in wanted and not n.startswith("_")]
            return out or registry.schemas()
        except Exception:
            log.exception("shortlist failed — sending all tools")
            return registry.schemas()


shortlist = ToolShortlist()
