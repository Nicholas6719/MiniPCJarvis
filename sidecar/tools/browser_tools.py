"""Interactive browser tools. Every action returns fresh page observations —
the model must verify outcomes, never assume them. Form submission is
MEDIUM-risk (confirmation-gated); reading and navigation are LOW."""
from __future__ import annotations

from browser.session import browser
from tools.registry import Risk, Tool, registry


async def browser_open(url: str) -> dict:
    return await browser.goto(url)


async def browser_read() -> dict:
    return await browser.observe()


async def browser_click(target: str) -> dict:
    return await browser.click(target)


async def browser_type(field: str, text: str) -> dict:
    return await browser.type_text(field, text)


async def browser_submit() -> dict:
    return await browser.press_enter()


async def browser_back() -> dict:
    return await browser.back()


def register_all() -> None:
    registry.register(Tool(
        name="browser_open",
        description="Open a URL in JARVIS's own visible browser window. Returns "
                    "the page title and text so you can verify where you landed.",
        parameters={"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
        risk=Risk.LOW, handler=browser_open, timeout=30))
    registry.register(Tool(
        name="browser_read",
        description="Read the current page in JARVIS's browser (url, title, text).",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=browser_read, timeout=15))
    registry.register(Tool(
        name="browser_click",
        description="Click a button or link on the current page by its visible "
                    "text. Verify the result from the returned page state.",
        parameters={"type": "object", "properties": {
            "target": {"type": "string",
                       "description": "Visible text of the button/link"}},
            "required": ["target"]},
        risk=Risk.LOW, handler=browser_click, timeout=20))
    registry.register(Tool(
        name="browser_type",
        description="Type text into a form field located by its label, "
                    "placeholder, or name. Does not submit.",
        parameters={"type": "object", "properties": {
            "field": {"type": "string"},
            "text": {"type": "string"}}, "required": ["field", "text"]},
        risk=Risk.LOW, handler=browser_type, timeout=15))
    registry.register(Tool(
        name="browser_submit",
        description="Press Enter to submit the current form or search. "
                    "Requires user confirmation.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.MEDIUM, handler=browser_submit, timeout=25))
    registry.register(Tool(
        name="browser_back",
        description="Go back one page in JARVIS's browser.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.LOW, handler=browser_back, timeout=20))
