"""Application-level autonomous lifecycle manager."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.logging import get_logger
from app.orchestration.locks import AgentLockManager
from app.orchestration.scheduler import AgentScheduler
from app.services.candidates import CandidateGenerationService
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.factory import build_llm_from_settings
from app.services.llm.status import LLMStatus, probe_llm
from app.services.reliability.recovery import recover_stale_runtime_state
from app.services.research.factory import build_research_hub
from app.worker.generation_worker import GenerationWorker

logger = get_logger(__name__)


class AgentLifecycleService:
    """Starts scheduler, recovers state, and registers agents for autonomy."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._locks = AgentLockManager()
        self._cycle_service: GenerationCycleService | None = None
        self._worker: GenerationWorker | None = None
        self._scheduler: AgentScheduler | None = None
        self._llm_available = False
        self._llm_status: LLMStatus = "unavailable"
        self._started = False

    @property
    def scheduler(self) -> AgentScheduler:
        if self._scheduler is None:
            raise RuntimeError("Lifecycle scheduler is not started")
        return self._scheduler

    @property
    def lock_manager(self) -> AgentLockManager:
        return self._locks

    @property
    def cycle_service(self) -> GenerationCycleService:
        if self._cycle_service is None:
            raise RuntimeError("Lifecycle cycle service is not started")
        return self._cycle_service

    @property
    def worker(self) -> GenerationWorker:
        if self._worker is None:
            raise RuntimeError("Lifecycle worker is not started")
        return self._worker

    @property
    def is_scheduler_running(self) -> bool:
        return bool(self._scheduler and self._scheduler.is_running)

    @property
    def llm_available(self) -> bool:
        return self._llm_available

    @property
    def llm_status(self) -> LLMStatus:
        """Probe-backed status: ready only after a successful real request."""
        return self._llm_status

    async def start(self) -> None:
        """Recover stale state, start scheduler, register running agents."""
        if self._started:
            return

        llm = build_llm_from_settings(self._settings)
        client_available = bool(getattr(llm, "available", False))
        # Never claim READY without a real connectivity probe.
        if not client_available:
            self._llm_status = "unavailable"
            self._llm_available = False
        elif self._settings.app_env == "test" or not self._settings.llm_probe_on_startup:
            self._llm_status = "unavailable"
            self._llm_available = client_available
            logger.info(
                "llm_probe_skipped",
                app_env=self._settings.app_env,
                probe_on_startup=self._settings.llm_probe_on_startup,
            )
        else:
            self._llm_status = await probe_llm(llm)
            if self._llm_status == "authentication_failed":
                from app.services.llm.base import UnavailableLLMClient

                llm = UnavailableLLMClient()
                self._llm_available = False
            else:
                # ready → available; unstable → keep client so cycles may retry.
                self._llm_available = self._llm_status == "ready" or (
                    self._llm_status == "unstable" and client_available
                )

        research_hub = build_research_hub(self._settings)
        candidate_service = CandidateGenerationService(llm)

        self._cycle_service = GenerationCycleService(
            session_factory=get_session_factory(),
            lock_manager=self._locks,
            lease_seconds=self._settings.generation_lease_seconds,
            research_hub=research_hub,
            candidate_service=candidate_service,
            llm_client=llm,
        )
        self._worker = GenerationWorker(self._cycle_service)
        self._scheduler = AgentScheduler(
            cycle_seconds=self._settings.agent_cycle_seconds,
            job_func=self._worker.run_cycle,
        )

        await self.recover_stale_state()
        self._scheduler.start()
        await self.register_running_agents()
        self._started = True
        logger.info(
            "lifecycle_started",
            status="running",
            llm=self._llm_status,
            llm_available=self._llm_available,
            provider=(self._settings.llm_provider or "openai"),
        )

    async def stop(self, *, wait: bool = False) -> None:
        """Stop accepting new scheduled work without blocking shutdown indefinitely.

        wait=False (default): scheduler stops immediately; in-flight cycles finish
        or are interrupted, and DB leases expire via TTL / next recovery.
        """
        if self._scheduler is not None and self._scheduler.is_running:
            self._scheduler.stop(wait=wait)
        self._started = False
        logger.info("lifecycle_stopped", status="stopped", wait=wait)

    async def recover_stale_state(self) -> dict[str, int]:
        factory = get_session_factory()
        async with factory() as session:
            result = await recover_stale_runtime_state(
                session,
                lease_seconds=self._settings.generation_lease_seconds,
            )
            await session.commit()
        logger.info(
            "stale_state_recovered",
            stale_pipeline_runs=result["stale_pipeline_runs"],
            expired_leases_cleared=result["expired_leases_cleared"],
        )
        return result

    async def register_running_agents(self) -> None:
        if self._scheduler is None:
            raise RuntimeError("Scheduler not initialized")
        factory = get_session_factory()
        async with factory() as session:
            agents = await AgentRepository(session).list_by_status("running")
        for agent in agents:
            self._scheduler.register_agent(agent.id, run_immediately=True)
        logger.info("running_agents_registered", count=len(agents))

    async def register_agent(self, agent_id: str) -> None:
        """Register a newly initialized agent with the scheduler."""
        if self._scheduler is None or not self._started:
            logger.warning(
                "lifecycle_register_before_start",
                agent_id=agent_id,
            )
            return
        self._scheduler.register_agent(agent_id, run_immediately=True)
        logger.info("lifecycle_agent_registered", agent_id=agent_id)
