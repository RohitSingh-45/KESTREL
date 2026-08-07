"""Pipeline run persistence operations."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PipelineRun


class PipelineRunRepository:
    """Data-access layer for pipeline_runs. No worker logic."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        started_at: datetime,
        status: str,
        stage: str | None = None,
        meta_json: str | None = None,
    ) -> PipelineRun:
        run = PipelineRun(
            id=run_id,
            agent_id=agent_id,
            started_at=started_at,
            finished_at=None,
            status=status,
            stage=stage,
            error=None,
            meta_json=meta_json,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        error: str | None = None,
        finished_at: datetime | None = None,
        meta_json: str | None = None,
    ) -> PipelineRun | None:
        run = await self.get_by_id(run_id)
        if run is None:
            return None
        if status is not None:
            run.status = status
        if stage is not None:
            run.stage = stage
        if error is not None:
            run.error = error
        if finished_at is not None:
            run.finished_at = finished_at
        if meta_json is not None:
            run.meta_json = meta_json
        await self._session.flush()
        return run

    async def get_by_id(self, run_id: str) -> PipelineRun | None:
        result = await self._session.execute(
            select(PipelineRun).where(PipelineRun.id == run_id)
        )
        return result.scalar_one_or_none()

    async def list_by_agent(self, agent_id: str) -> list[PipelineRun]:
        result = await self._session.execute(
            select(PipelineRun)
            .where(PipelineRun.agent_id == agent_id)
            .order_by(PipelineRun.started_at.desc())
        )
        return list(result.scalars().all())

    async def mark_success(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        stage: str = "lifecycle_check",
    ) -> PipelineRun | None:
        return await self.update_run(
            run_id,
            status="success",
            stage=stage,
            finished_at=finished_at,
            error=None,
        )

    async def mark_failed(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        error: str,
        stage: str | None = None,
    ) -> PipelineRun | None:
        return await self.update_run(
            run_id,
            status="failed",
            stage=stage,
            finished_at=finished_at,
            error=error,
        )

    async def mark_skipped(
        self,
        run_id: str,
        *,
        finished_at: datetime,
        stage: str = "lifecycle_check",
        error: str | None = None,
    ) -> PipelineRun | None:
        return await self.update_run(
            run_id,
            status="skipped",
            stage=stage,
            finished_at=finished_at,
            error=error,
        )

    async def recover_stale_runs(
        self,
        *,
        finished_at: datetime,
        error: str = "Recovered stale running pipeline after process restart",
    ) -> int:
        """Mark orphaned running rows as failed. Safe on startup before workers begin."""
        result = await self._session.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "running")
            .values(
                status="failed",
                finished_at=finished_at,
                error=error,
                stage="lifecycle_check",
            )
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def count_running_for_agent(self, agent_id: str) -> int:
        result = await self._session.execute(
            select(PipelineRun).where(
                PipelineRun.agent_id == agent_id,
                PipelineRun.status == "running",
            )
        )
        return len(list(result.scalars().all()))
