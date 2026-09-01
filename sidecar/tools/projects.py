"""Projects and their progress. STUB — Phase 1 fills these in.

Deliberately NOT the existing `tasks` table: that one is reminders and errands
("remind me at 9 to take my retainers out"), and reusing the name would have two
unrelated features writing the same rows. These get `projects` and
`project_steps` of their own in the same jarvis.db.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.projects")


async def list_projects(status: str = "active") -> dict:
    raise NotImplementedError("projects: Phase 1")


async def log_progress(project: str, note: str = "", percent: int | None = None) -> dict:
    raise NotImplementedError("projects: Phase 1")


async def estimate_completion(project: str) -> dict:
    raise NotImplementedError("projects: Phase 1")


def register_all() -> None:
    registry.register(Tool(
        name="list_projects",
        description="List the user's tracked projects and how far along each one is.",
        parameters={"type": "object", "properties": {
            "status": {"type": "string", "enum": ["active", "done", "all"]}},
            "required": []},
        risk=Risk.SAFE, handler=list_projects, timeout=20))
    registry.register(Tool(
        name="log_progress",
        description="Record progress on a tracked project — a note, a percentage, or both.",
        parameters={"type": "object", "properties": {
            "project": {"type": "string"},
            "note": {"type": "string"},
            "percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["project"]},
        risk=Risk.LOW, handler=log_progress, timeout=20))
    registry.register(Tool(
        name="estimate_completion",
        description="Estimate when a tracked project will finish, reasoning from elapsed "
                    "work versus what is left.",
        parameters={"type": "object", "properties": {
            "project": {"type": "string"}}, "required": ["project"]},
        risk=Risk.SAFE, handler=estimate_completion, timeout=60))
