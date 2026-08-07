"""Autonomous generation worker boundary.

Module 3 runs a lifecycle check only — never creates posts.
"""

from __future__ import annotations

from app.logging import get_logger
from app.services.generation_cycle import GenerationCycleService

logger = get_logger(__name__)


class GenerationWorker:
    """Scheduler-facing worker that isolates cycle failures from the web server."""

    def __init__(self, cycle_service: GenerationCycleService) -> None:
        self._cycle_service = cycle_service

    async def run_cycle(self, agent_id: str) -> None:
        """Entry point invoked by the scheduler. Never raises to the scheduler loop."""
        try:
            await self._cycle_service.execute(agent_id)
        except Exception:
            # Absolute boundary: keep FastAPI/Uvicorn alive.
            logger.exception(
                "cycle_unhandled_exception",
                agent_id=agent_id,
                status="failed",
            )
