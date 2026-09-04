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

# Sync tool handlers get their OWN threads, separate from everything else.
#
# `asyncio.to_thread` uses the interpreter's default executor, which on this
# machine is 20 threads — and 61 call sites share it, including speech-to-text,
# text-to-speech, the embedding calls on the turn path, and the brain. Tool
# handlers are the dangerous tenants: Windows UI Automation blocks on an
# unresponsive app, a subprocess can hang, a browser call can sit forever. And
# `wait_for` cancels the FUTURE, never the thread — a timed-out tool keeps its
# thread for the life of the process.
#
# Sharing one pool meant enough wedged tools would starve the turn path itself,
# leaving JARVIS unable to hear, think or speak. He has already lived through
# "answers nothing" twice; it must not be reachable from a stuck tool.
# Bounded and separate, so a wedged tool costs him tools and nothing else.
_TOOL_THREADS = 8
_tool_pool = None


def _tool_executor():
    global _tool_pool
    if _tool_pool is None:
        import concurrent.futures as cf
        _tool_pool = cf.ThreadPoolExecutor(
            max_workers=_TOOL_THREADS, thread_name_prefix="jarvis-tool")
    return _tool_pool


def _run_in_tool_pool(handler, args: dict):
    """Run a sync handler off the loop, on the tool pool, preserving context.

    `to_thread` copies the current context; `run_in_executor` does not, so it is
    copied explicitly here rather than silently changing what handlers see.
    """
    import contextvars
    import functools
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, functools.partial(handler, **args))
    busy = len(getattr(_tool_executor(), "_threads", ()) or ())
    if busy >= _TOOL_THREADS:
        log.warning("tool pool is saturated (%d/%d threads) — a handler is "
                    "probably wedged; speech and thinking are unaffected",
                    busy, _TOOL_THREADS)
    return asyncio.get_running_loop().run_in_executor(_tool_executor(), call)


def run_in_tool_pool(fn, *args, **kwargs):
    """For ASYNC handlers that still have to do win32/UIA/COM work.

    The pool above exists because those calls wedge and `wait_for` cannot kill
    a thread — but only sync handlers were routed through it. The async input
    and UIA tools hopped through `asyncio.to_thread`, which is the DEFAULT
    executor shared with STT, TTS and the embeddings: one UIA walk stuck on a
    modal ate a shared thread for the life of the process, and a few of those
    had speech queueing behind the mouse. Same pool, same context copy.
    """
    import contextvars
    import functools
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, functools.partial(fn, *args, **kwargs))
    return asyncio.get_running_loop().run_in_executor(_tool_executor(), call)


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
        # widened to 120 s during remote (Telegram) turns — phones answer slower
        self.confirm_timeout = 30
        # ONE thread for the audit connection, so the sqlite handle is only
        # ever touched from one place, and the INSERT+commit is never on the
        # event loop: with WAL a writer still serialises, and busy_timeout is
        # 15 s — so a tool call landing while night school held the lock held
        # the whole assistant for up to 15 s, per call, in silence.
        import concurrent.futures as _cf
        self._audit_pool = _cf.ThreadPoolExecutor(max_workers=1,
                                                  thread_name_prefix="jarvis-audit")

    async def _audit_async(self, tool: str, args: Any, risk: str, status: str,
                           confirmed: bool | None = None) -> None:
        """`_audit`, off the loop. Never raises; an audit failure is logged
        inside `_audit` and must not turn a finished tool call into an error."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._audit_pool, self._audit, tool, args,
                                       risk, status, confirmed)
        except Exception:
            log.exception("audit dispatch failed")

    def _audit(self, tool: str, args: Any, risk: str, status: str,
               confirmed: bool | None = None) -> None:
        """Persistent audit trail of every tool execution attempt."""
        try:
            if self._audit_db is None:
                from config import open_db
                self._audit_db = open_db()
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
            # Roll back, or this connection keeps the implicit transaction that
            # the failed INSERT opened - and with it the database's single write
            # lock. That is how a corrupt `audit_log` becomes "database is
            # locked" for the transcript, the facts and the brain: the audit
            # trail swallows its own error, looks fine, and silently holds the
            # door shut against every other writer in the process.
            try:
                self._audit_db.rollback()
            except Exception:
                log.warning('audit rollback failed - the write lock may be held',
                            exc_info=True)

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

    def awaiting_confirmation(self) -> bool:
        """Is a tool sitting here waiting to be told yes or no?"""
        return any(not f.done() for f in self._pending.values())

    def answer_pending_confirmation(self, approved: bool) -> bool:
        """Answer the question that is open, without needing to know its id.

        FOR A TYPED YES. The inline buttons carry the confirm_id and were the
        only thing that could answer; a reply of "Do it!" started a whole new
        turn instead, the original question timed out, and he was told "I didn't
        get a yes, so I left it alone" — a minute after saying yes. From his side
        that is JARVIS ignoring him, which is worse than the action not running.

        Only ever one question is open at a time (the turn is blocked on it), so
        "the pending one" is unambiguous.
        """
        for cid, fut in list(self._pending.items()):
            if not fut.done():
                return self.resolve_confirmation(cid, approved)
        return False

    async def _second_signal(self, tool_name: str) -> tuple[bool, str]:
        """The face check for HIGH-risk tools. Imported late and never allowed to
        raise: a webcam problem must not become a wall between him and his own
        machine. Any failure here falls back to the spoken gate, which is the
        security level that already existed."""
        try:
            from tools.biometric import second_signal
            return await second_signal(tool_name)
        except Exception:
            log.exception("second signal unavailable for %s", tool_name)
            return True, "second signal errored"

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
            hook_task: asyncio.Task | None = None
            try:
                # THE QUESTION AND THE ANSWER RACE. The hook speaks the question
                # and listens for a spoken yes/no for up to two 8-second tries;
                # it used to be awaited to completion BEFORE the future was
                # looked at. So a tap on the HUD's YES button, or on DO IT from
                # the phone, resolved the future instantly — and then sat for up
                # to eight seconds while the microphone finished not hearing
                # anything. Now the first answer wins, whichever door it came by.
                # And a hook that raises (the recogniser, a mic that has gone)
                # no longer takes the whole turn down with it: it is logged and
                # the answer is awaited through the other doors.
                if self.confirm_hook:
                    async def _hook() -> None:
                        try:
                            await self.confirm_hook(name, args)
                        except asyncio.CancelledError:
                            raise
                        except Exception:
                            log.exception("confirmation hook failed for %s", name)
                    hook_task = asyncio.create_task(_hook())
                approved = await asyncio.wait_for(fut, timeout=self.confirm_timeout)
            except asyncio.TimeoutError:
                self._pending.pop(confirm_id, None)
                await bus.emit("tool_call", call_id=call_id, tool=name,
                               status="denied", detail="confirmation timed out")
                await self._audit_async(name, args, tool.risk.value, "confirm_timeout",
                                        confirmed=False)
                return {"ok": False, "error": "user did not confirm in time",
                        "unconfirmed": True}
            finally:
                # always drop the pending future — otherwise a CancelledError (turn
                # interrupted while waiting) leaves it in _pending forever, and every
                # later utterance is misrouted as an answer to a dead question.
                self._pending.pop(confirm_id, None)
                if hook_task is not None and not hook_task.done():
                    hook_task.cancel()
                    try:
                        await hook_task
                    except (asyncio.CancelledError, Exception):
                        pass
                if self.confirm_done_hook:
                    await self.confirm_done_hook()
            if not approved:
                await bus.emit("tool_call", call_id=call_id, tool=name, status="denied")
                await self._audit_async(name, args, tool.risk.value, "denied", confirmed=False)
                return {"ok": False, "error": "user declined the action", "declined": True}

            # A SECOND signal for HIGH risk only, and only ever a refusal.
            #
            # Its position here is the security property: it runs AFTER the
            # spoken yes has already been given, so there is no path by which a
            # face grants anything. A bare webcam match has no liveness
            # guarantee — a photograph held to the lens would pass it — so it
            # may raise confidence and must never confer permission. Moving this
            # above the `approved` check, or letting it set `approved`, would
            # turn an additive signal into a replaceable one.
            if tool.risk is Risk.HIGH:
                allow, why = await self._second_signal(name)
                if not allow:
                    await bus.emit("tool_call", call_id=call_id, tool=name,
                                   status="denied", detail=why)
                    await self._audit_async(name, args, tool.risk.value, "denied",
                                            confirmed=False)
                    return {"ok": False, "error": f"face check failed: {why}",
                            "declined": True, "face_failed": True}

            await self._audit_async(name, args, tool.risk.value, "confirmed", confirmed=True)

        # A TypeError that comes from BINDING the arguments is the model's fault;
        # one raised INSIDE the handler (an int(None) on a bad payload, a COM
        # property that came back None) is ours. Both used to be reported as
        # "bad arguments", which sent the model off to "fix" arguments that were
        # fine. Bind first, so the two can be told apart.
        try:
            inspect.signature(tool.handler).bind(**args)
        except TypeError as e:
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail=str(e))
            return {"ok": False, "error": f"bad arguments: {e}"}

        t0 = time.time()
        try:
            if inspect.iscoroutinefunction(tool.handler):
                result = await asyncio.wait_for(tool.handler(**args), timeout=tool.timeout)
            else:
                result = await asyncio.wait_for(
                    _run_in_tool_pool(tool.handler, args), timeout=tool.timeout)
            ms = int((time.time() - t0) * 1000)
            await bus.emit("tool_call", call_id=call_id, tool=name,
                           status="success", latency_ms=ms, result=_truncate(result))
            await self._audit_async(name, args, tool.risk.value, "success")
            return {"ok": True, "result": result}
        except asyncio.TimeoutError:
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail="timeout")
            # THE TRAIL SAYS WHAT HAPPENED AFTER "confirmed". A HIGH action that
            # was approved and then timed out or blew up left a "confirmed" row
            # and nothing after it — the log could not say whether the thing
            # ran. Now every ending is written down.
            await self._audit_async(name, args, tool.risk.value, "timeout")
            return {"ok": False, "error": f"{name} timed out"}
        except Exception as e:
            log.exception("tool %s failed", name)
            await bus.emit("tool_call", call_id=call_id, tool=name, status="error",
                           detail=str(e))
            await self._audit_async(name, args, tool.risk.value, "error")
            return {"ok": False, "error": str(e)}


def _truncate(obj: Any, limit: int = 800) -> Any:
    s = json.dumps(obj, default=str)
    return obj if len(s) <= limit else s[:limit] + "…"


registry = ToolRegistry()
