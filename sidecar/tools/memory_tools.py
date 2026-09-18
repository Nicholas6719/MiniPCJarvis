"""Memory tools the LLM can call: remember and recall."""
from __future__ import annotations

from memory.store import memory
from tools.registry import Risk, Tool, registry


async def remember_fact(content: str, category: str = "fact") -> dict:
    mid = await memory.remember(content, category=category)
    return {"remembered": content, "id": mid}


async def recall(query: str) -> dict:
    hits = await memory.search(query, top_k=5)
    # ONLY WHAT IS ABOUT THE QUESTION. Asked the wifi password, he recited his
    # desk lamp, his car and his own name (2026-09-17): the nearest memories,
    # however far. Below this the answer is "I don't have that".
    hits = [h for h in hits if float(h.get("score") or 0) >= 0.30]
    if not hits:
        return {"memories": [], "note": "nothing stored about that - say so plainly, do not list other memories"}
    out = {"memories": [{"content": h["content"], "category": h["category"],
                         "score": h.get("score")} for h in hits]}
    # One clearly-best memory can be spoken as-is — no LLM round needed. Recall was
    # the slowest thing JARVIS did (11 s to say a sentence he already had on disk).
    top = hits[0]
    if top.get("score", 0) >= 0.62 and (len(hits) == 1 or top["score"] - hits[1].get("score", 0) >= 0.05):
        out["direct"] = top["content"]
    return out


def register_all() -> None:
    registry.register(Tool(
        name="remember_fact",
        description="Store a fact, preference, or piece of information about the user "
                    "for future conversations. Use when the user asks you to remember "
                    "something, or states a lasting preference.",
        parameters={"type": "object", "properties": {
            "content": {"type": "string", "description": "The fact to remember, phrased as a standalone sentence"},
            "category": {"type": "string", "enum": ["identity", "preference", "person", "project", "goal", "routine", "fact"]}},
            "required": ["content"]},
        risk=Risk.SAFE, handler=remember_fact))
    registry.register(Tool(
        name="recall",
        description="Search your long-term memory about the user. Use when the user asks "
                    "what you remember, or when past context would help.",
        parameters={"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
        risk=Risk.SAFE, handler=recall))
