"""The BC125AT Console's local API (`--serve` on http://127.0.0.1:8125).

Every call carries `X-Scanner-Client: 1` (the console refuses /api calls
without it), has a short timeout, and answers None instead of raising: the
console being absent is a normal state, not an error, and the desk asks
again on its own schedule.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("jarvis.scanner")

HEADERS = {"X-Scanner-Client": "1"}


class ConsoleClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8125") -> None:
        self.base_url = base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self.last_error = ""

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            # trust_env=False: a system proxy must never sit between us and loopback
            self._client = httpx.AsyncClient(base_url=self.base_url, headers=HEADERS,
                                             timeout=httpx.Timeout(5.0, connect=1.5), trust_env=False)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    async def _get(self, path: str, timeout: float = 5.0, **params):
        try:
            r = await self._c().get(path, params=params or None, timeout=timeout)
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"[:200]
            return None
        if r.status_code == 404:
            self.last_error = f"{path}: 404"
            return 404
        if r.status_code >= 400:
            self.last_error = f"{path}: HTTP {r.status_code}"
            return None
        return r

    async def health(self) -> dict | None:
        r = await self._get("/api/health", timeout=2.0)
        if r is None or r == 404:
            return None
        try:
            got = r.json()
        except Exception:
            self.last_error = "/api/health: not JSON"
            return None
        return got if isinstance(got, dict) else None

    async def feed(self, after_id: int, limit: int = 200) -> dict | None:
        r = await self._get("/api/hits/feed", after_id=int(after_id), limit=int(limit))
        if r is None or r == 404:
            return None
        try:
            got = r.json()
        except Exception:
            self.last_error = "/api/hits/feed: not JSON"
            return None
        if not isinstance(got, dict) or not isinstance(got.get("hits"), list):
            self.last_error = "/api/hits/feed: unexpected shape"
            return None
        return got

    async def annotate(self, hit_id: int, transcript: str, asr_conf: float | None,
                       tags: list[str], incident: str | None) -> bool:
        body = {"id": int(hit_id), "transcript": transcript, "asr_conf": asr_conf,
                "tags": list(tags or []), "incident": incident}
        try:
            r = await self._c().post("/api/hits/annotate", json=body, timeout=5.0)
            ok = r.status_code < 400 and bool((r.json() or {}).get("ok", True))
        except Exception as e:
            self.last_error = f"annotate: {type(e).__name__}"
            return False
        if not ok:
            self.last_error = f"annotate: HTTP {r.status_code}"
        return ok

    async def doctor_report(self) -> dict | str | None:
        """The scan optimiser's report; "missing" when the console has not built one."""
        r = await self._get("/api/doctor/report", timeout=5.0)
        if r == 404:
            return "missing"
        if r is None:
            return None
        try:
            return r.json()
        except Exception:
            return None

    async def clip(self, hit_id: int) -> bytes | None:
        """A transmission's WAV over HTTP, for when its file cannot be read directly."""
        r = await self._get("/api/clip", timeout=10.0, id=int(hit_id))
        if r is None or r == 404:
            return None
        ctype = r.headers.get("content-type", "")
        return r.content if ("wav" in ctype or r.content[:4] == b"RIFF") else None
