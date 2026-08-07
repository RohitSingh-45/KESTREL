"""APScheduler wrapper for per-agent generation jobs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.logging import get_logger

logger = get_logger(__name__)

JobFunc = Callable[[str], Awaitable[None]]


def job_id_for_agent(agent_id: str) -> str:
    return f"kestrel-generation:{agent_id}"


class AgentScheduler:
    """Owns a single AsyncIOScheduler instance for the application."""

    def __init__(
        self,
        *,
        cycle_seconds: int,
        job_func: JobFunc,
    ) -> None:
        self._cycle_seconds = cycle_seconds
        self._job_func = job_func
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._started = False

    @property
    def is_running(self) -> bool:
        return self._started and self._scheduler.running

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler

    def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        logger.info(
            "scheduler_started",
            cycle_seconds=self._cycle_seconds,
            status="running",
        )

    def stop(self, wait: bool = True) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        logger.info("scheduler_shutdown", status="stopped")

    def register_agent(self, agent_id: str, *, run_immediately: bool = True) -> None:
        """Register or replace the single generation job for an agent."""
        jid = job_id_for_agent(agent_id)
        trigger = IntervalTrigger(seconds=self._cycle_seconds, timezone="UTC")
        next_run = datetime.now(timezone.utc) if run_immediately else None

        self._scheduler.add_job(
            self._job_func,
            trigger=trigger,
            id=jid,
            args=[agent_id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=next_run,
            misfire_grace_time=self._cycle_seconds,
        )
        logger.info(
            "agent_registered",
            agent_id=agent_id,
            job_id=jid,
            cycle_seconds=self._cycle_seconds,
            run_immediately=run_immediately,
        )

    def unregister_agent(self, agent_id: str) -> None:
        jid = job_id_for_agent(agent_id)
        if self._scheduler.get_job(jid):
            self._scheduler.remove_job(jid)
            logger.info("agent_unregistered", agent_id=agent_id, job_id=jid)

    def has_job(self, agent_id: str) -> bool:
        return self._scheduler.get_job(job_id_for_agent(agent_id)) is not None

    def job_count(self) -> int:
        return len(self._scheduler.get_jobs())
