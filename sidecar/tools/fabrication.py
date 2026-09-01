"""Text to a printable part. STUB — Phase 5 fills this in.

Neither OpenSCAD nor PrusaSlicer is installed on this machine (checked, both
absent), and there is no printer. So Phase 5's test will SKIP with a clear
message rather than pass: a green tick for a tool that never ran is worse than an
honest skip, and he would rightly stop trusting the suite.

PrinterBackend exists with only NoPrinterBackend behind it, deliberately. The
abstraction is the point — adding OctoPrint or Moonraker later should be one new
file, not a redesign. Do not build one now.
"""
from __future__ import annotations

import logging

from tools.registry import Risk, Tool, registry

log = logging.getLogger("jarvis.tools.fabrication")


class PrinterBackend:
    """The seam a real printer plugs into later."""

    async def get_status(self) -> dict:
        raise NotImplementedError

    async def start_print(self, gcode_path: str) -> dict:
        raise NotImplementedError

    async def cancel_print(self) -> dict:
        raise NotImplementedError


class NoPrinterBackend(PrinterBackend):
    """The only implementation, on purpose."""

    async def get_status(self) -> dict:
        return {"error": "no printer configured"}

    async def start_print(self, gcode_path: str) -> dict:
        return {"error": "no printer configured"}

    async def cancel_print(self) -> dict:
        return {"error": "no printer configured"}


backend: PrinterBackend = NoPrinterBackend()


async def generate_part(description: str, name: str = "") -> dict:
    raise NotImplementedError("fabrication: Phase 5")


async def slice_part(stl_path: str) -> dict:
    raise NotImplementedError("fabrication: Phase 5")


async def printer_status() -> dict:
    return await backend.get_status()


def register_all() -> None:
    registry.register(Tool(
        name="generate_part",
        description="Turn a description of a simple part into a 3D model (OpenSCAD -> STL). "
                    "Local file creation only; nothing is printed.",
        parameters={"type": "object", "properties": {
            "description": {"type": "string"},
            "name": {"type": "string"}}, "required": ["description"]},
        risk=Risk.LOW, handler=generate_part, timeout=120))
    registry.register(Tool(
        name="slice_part",
        description="Slice an STL into G-code and report the real print time and filament "
                    "estimate from the slicer.",
        parameters={"type": "object", "properties": {
            "stl_path": {"type": "string"}}, "required": ["stl_path"]},
        risk=Risk.LOW, handler=slice_part, timeout=180))
    registry.register(Tool(
        name="printer_status",
        description="Whether a 3D printer is connected and what it is doing.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=Risk.SAFE, handler=printer_status, timeout=20))
