"""MCP client: connect to configured MCP servers and expose their tools.

Security posture per spec §30: external servers are untrusted. Their tools
register at MEDIUM risk by default (confirmation-gated) unless the user's
config explicitly lowers a specific server to "low"/"safe". Tool results are
data, never instructions — same rule as every other external content source.
"""
from __future__ import annotations

import logging
from contextlib import AsyncExitStack

from config import config
from events import bus
from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.mcp")

_RISK = {"safe": Risk.SAFE, "low": Risk.LOW, "medium": Risk.MEDIUM, "high": Risk.HIGH}


class MCPManager:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, object] = {}
        self.tool_names: dict[str, list[str]] = {}

    async def start(self) -> None:
        servers: dict = config.get("mcp", "servers", default={}) or {}
        if not servers:
            return
        self._stack = AsyncExitStack()
        for name, scfg in servers.items():
            try:
                await self._connect(name, scfg)
            except Exception as e:
                log.error("mcp server '%s' failed to connect: %s", name, e)
                await bus.emit("error", summary=f"MCP server '{name}' unavailable")

    async def _connect(self, name: str, scfg: dict) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=scfg["command"], args=scfg.get("args", []),
            env=scfg.get("env"))
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        result = await session.list_tools()
        risk = _RISK.get(str(scfg.get("risk", "medium")).lower(), Risk.MEDIUM)
        registered = []
        for t in result.tools:
            tool_name = f"mcp_{name}_{t.name}"[:64]
            registered.append(tool_name)
            schema = (getattr(t, "input_schema", None)
                      or getattr(t, "inputSchema", None)
                      or {"type": "object", "properties": {}})
            registry.register(Tool(
                name=tool_name,
                description=f"[{name} plugin] {t.description or t.name}",
                parameters=schema,
                risk=risk,
                handler=self._make_handler(session, t.name),
                timeout=float(scfg.get("timeout", 30)),
            ))
        self.sessions[name] = session
        self.tool_names[name] = registered
        log.info("mcp '%s': %d tools registered (%s risk)", name, len(registered),
                 risk.value)
        await bus.emit("boot", summary=f"plugin '{name}' connected: {len(registered)} tools")

    def _make_handler(self, session, remote_name: str):
        async def handler(**kwargs):
            result = await session.call_tool(remote_name, arguments=kwargs)
            out = []
            for item in result.content:
                if getattr(item, "type", "") == "text":
                    out.append(item.text)
            text = "\n".join(out)[:4000]
            if getattr(result, "isError", False):
                return {"error": text or "plugin tool failed"}
            return {"result": text}
        return handler

    async def stop(self) -> None:
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:
                pass
        self._stack = None
        self.sessions = {}


mcp_manager = MCPManager()
