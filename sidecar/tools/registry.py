"""Tool registry: schema, risk classification, confirmation gating, execution."""
from __future__ import annotations

import asyncio
import enum
import inspect
import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from events import bus

log = logging.getLogger("jarvis.tools")


class Risk(str, enum.Enum):
    SAFE = "safe"        # read-only, no side effects
    LOW = "low"          # minor side effects, logged
    MEDIUM = "medium"    # visible side effects — confirm by default
    HIGH = "high"        # destructive/irreversible — always confirm


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON schema
    risk: Risk
    handler: Callable[..., Awaitable[Any]]
    timeout: float = 30.0

    @property
    def requires_confirmation(self) -> bool:
        return self.risk in (Risk.MEDIUM, Risk.HIGH)

    def openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # confirmation_id -> future resolved by the UI's answer
        self._pending: dict[str, asyncio.Future] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.openai_schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def resolve_confirmation(self, confirm_id: str, approved: bool) -> bool:
        fut = self._pending.pop(confirm_id, None)
        if fut and not fut.done():
            fut.set_result(approved)
            return True
        return False

    async def execute(self, name: str, arguments: str | dict) -> dict:
        """Run a tool with risk gating. Returns {ok, result|error, ...}."""
        tool = self._tools.get(name)
        call_id = uuid.uuid4().hex[:10]
        if tool is None:
            return {"ok": False, "error": f"unknown tool: {name}"}
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except json.JSONDecodeError:
            return {"ok": False, "error": "invalid tool arguments (not JSON)"}

        await bus.emit("tool_call", call_id=call_id, tool=name, args=args,
                       risk=tool.risk.value, status="pending")

        if tool.requires_confirmation:
            confirm_id = uuid.uuid4().hex[:10]
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._pending[confirm_id] = fut
            await bus.emit("confirmation_required", confirm_id=confirm_id,
                           call_id=call_id, tool=name, args=args, risk=tool.risk.value)
            try:
                approved = await asyncio.wait_for(fut, timeout=120)
            except asyncio.TimeoutError:
                self._pending.pop(confirm_id, None)
                await bus.emit("tool_call", call_id=call_id, tool=name,
                               status="denied", detail="confirmation timed out")
                return {"ok": False, "error": "user did not confirm in time"}
            if not approved:
                await bus.emit("tool_call", call_id=call_id, tool=name, status="denied")
                return {"ok": False, "error": "user declined the action"}

        t0 = time.time()
        try:
            if inspect.iscoroutinefunction(tool.handler):
                result = await asyncio.wait_for(tool.handler(**args), timeout=tool.timeout)
            else:
                result = await asyncio.wait_for(
                    asyncio.to_thread(tool.handler, **args), timeout=tool.timeout)
            ms = int((time.time() - t0) * 1000)
            await bus.emit("tool_call", call_id=call_id, tool=name,
                           status="success", latency_ms=ms, result=_truncate(result))
            return {"ok": True, "result": result}
        except asyncio.TimeoutError:
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail="timeout")
            return {"ok": False, "error": f"{name} timed out"}
        except TypeError as e:
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail=str(e))
            return {"ok": False, "error": f"bad arguments: {e}"}
        except Exception as e:
            log.exception("tool %s failed", name)
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail=str(e))
            return {"ok": False, "error": str(e)}


def _truncate(obj: Any, limit: int = 800) -> Any:
    s = json.dumps(obj, default=str)
    return obj if len(s) <= limit else s[:limit] + "…"


registry = ToolRegistry()
