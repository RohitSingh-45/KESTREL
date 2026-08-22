"""Progress channel between a running trace and the browser.

The worker appends events to an ordered log in the cache; the SSE endpoint reads
that log by offset. Reading by offset rather than subscribing means a client that
reconnects mid-trace replays what it missed instead of joining a stream already
in progress, and the same code path works whether the trace runs inline in the
API process or in a separate Celery worker with Redis in between.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from app.core.cache import cache

EVENT_TTL_SECONDS = 3600
MAX_EVENTS = 400

TERMINAL_EVENTS = frozenset({"completed", "failed", "cancelled"})


def _key(job_id: str) -> str:
    return f"trace:events:{job_id}"


class ProgressReporter:
    """Write side. One instance per trace job."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.sequence = 0
        self.started = time.perf_counter()

    async def emit(self, payload: dict[str, Any]) -> None:
        self.sequence += 1
        event = {
            "seq": self.sequence,
            "job_id": self.job_id,
            "ts": datetime.now(UTC).isoformat(),
            "elapsed_ms": int((time.perf_counter() - self.started) * 1000),
            **payload,
        }
        await cache.list_append(
            _key(self.job_id), event, ttl=EVENT_TTL_SECONDS, max_len=MAX_EVENTS
        )

    async def failed(self, error: str) -> None:
        await self.emit({"event": "failed", "error": error[:500]})

    async def completed(self, summary: dict[str, Any]) -> None:
        await self.emit({"event": "completed", "summary": summary})


async def read_events(job_id: str, after: int = 0) -> list[dict[str, Any]]:
    """Events with a sequence number greater than `after`."""
    events = await cache.list_range(_key(job_id))
    return [e for e in events if int(e.get("seq", 0)) > after]


async def clear_events(job_id: str) -> None:
    await cache.delete(_key(job_id))


def is_terminal(event: dict[str, Any]) -> bool:
    return event.get("event") in TERMINAL_EVENTS
