"""Moving what he was just told somewhere else.

"Send it to my phone." "Give me the article." Both refer to the thing he has just
heard, which is why neither sentence names it. The subject comes from
lastseen.py, filled from the tool results behind the answer.

Agreed with Nicholas 2026-08-30: "send it through Telegram" or "send it to me"
goes to the phone; "give me the article" opens it in the browser he is using.
"""
from __future__ import annotations

import logging

from lastseen import last_seen
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.handoff")


async def send_to_phone(text: str = "") -> dict:
    """Send something to his Telegram — by default, whatever he was just told."""
    from config import config
    if not config.get("remote", "telegram_chat_id", default=None):
        return {"error": "Your phone isn't paired yet, sir — say 'pair my phone' first."}

    body = (text or "").strip()
    if not body:
        if last_seen.stale or not (last_seen.text or last_seen.links):
            return {"error": "I'm not sure what to send, sir — say what you'd like."}
        body = last_seen.text
        # a headline is worth much less without the link behind it
        for link in last_seen.links[:3]:
            title = link.get("title") or "link"
            body += f"\n\n{title}\n{link['url']}"

    from remote_telegram import telegram
    await telegram.send_proactive(body, tier="brief")
    return {"sent": True, "characters": len(body),
            "links": len(last_seen.links[:3]) if not text else 0}


async def open_article(which: int = 1) -> dict:
    """Open the story he was just told about, in the browser he is using."""
    if last_seen.stale or not last_seen.links:
        return {"error": "I don't have an article to open from that, sir."}
    idx = max(1, min(len(last_seen.links), int(which or 1))) - 1
    link = last_seen.links[idx]
    from tools.browser_tools import browser_open
    result = await browser_open(link["url"])
    if isinstance(result, dict) and result.get("error"):
        return result
    return {"opened": link["url"], "title": link.get("title", ""),
            "source": link.get("source", "")}


def register_all() -> None:
    registry.register(Tool(
        name="send_to_phone",
        description="Send something to the user's phone over Telegram. With no text, "
                    "sends whatever he was just told about, with its links. Use for "
                    "'send it to my phone', 'send that to me', 'send it through Telegram'.",
        parameters={"type": "object", "properties": {
            "text": {"type": "string",
                     "description": "what to send; omit to send the last answer"}},
            "required": []},
        risk=Risk.SAFE, handler=send_to_phone, timeout=30))
    registry.register(Tool(
        name="open_article",
        description="Open the article or web page the user was just told about, in his "
                    "browser. Use for 'give me the article', 'open that story', "
                    "'pull it up', 'show me the source'.",
        parameters={"type": "object", "properties": {
            "which": {"type": "integer", "minimum": 1, "maximum": 8,
                      "description": "1 for the first story mentioned, 2 for the next"}},
            "required": []},
        risk=Risk.LOW, handler=open_article, timeout=45))
