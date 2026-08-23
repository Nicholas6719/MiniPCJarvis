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
        self.confirm_hook = None       # async (tool, args) -> None : ask out loud
        self.confirm_done_hook = None  # async () -> None            : question answered
        # confirmation_id -> future resolved by the UI's answer
        self._pending: dict[str, asyncio.Future] = {}
        self._audit_db = None

    def _audit(self, tool: str, args: Any, risk: str, status: str,
               confirmed: bool | None = None) -> None:
        """Persistent audit trail of every tool execution attempt."""
        try:
            if self._audit_db is None:
                import sqlite3
                from config import DB_PATH
                self._audit_db = sqlite3.connect(DB_PATH, check_same_thread=False)
                self._audit_db.execute(
                    "CREATE TABLE IF NOT EXISTS audit_log ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tool TEXT, "
                    "args TEXT, risk TEXT, status TEXT, confirmed INTEGER)")
            self._audit_db.execute(
                "INSERT INTO audit_log (ts, tool, args, risk, status, confirmed) "
                "VALUES (?,?,?,?,?,?)",
                (time.time(), tool, json.dumps(args, default=str)[:1000], risk,
                 status, None if confirmed is None else int(confirmed)))
            self._audit_db.commit()
        except Exception:
            log.exception("audit write failed")

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.openai_schema() for t in self._tools.values()]

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def resolve_latest(self, approved: bool) -> bool:
        """Answer the newest pending confirmation (used by the voice yes/no path)."""
        if not self._pending:
            return False
        return self.resolve_confirmation(next(reversed(self._pending)), approved)

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
                if self.confirm_hook:      # speak the question; listen for a spoken yes/no
                    await self.confirm_hook(name, args)
                approved = await asyncio.wait_for(fut, timeout=30)
            except asyncio.TimeoutError:
                self._pending.pop(confirm_id, None)
                await bus.emit("tool_call", call_id=call_id, tool=name,
                               status="denied", detail="confirmation timed out")
                return {"ok": False, "error": "user did not confirm in time",
                        "unconfirmed": True}
            finally:
                if self.confirm_done_hook:
                    await self.confirm_done_hook()
            if not approved:
                await bus.emit("tool_call", call_id=call_id, tool=name, status="denied")
                self._audit(name, args, tool.risk.value, "denied", confirmed=False)
                return {"ok": False, "error": "user declined the action", "declined": True}
            self._audit(name, args, tool.risk.value, "confirmed", confirmed=True)

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
            self._audit(name, args, tool.risk.value, "success")
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
