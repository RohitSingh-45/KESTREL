"""Read-only operator dashboard API. Never triggers generation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.api.schemas.dashboard import AgentListResponse, AgentSummary, DashboardStateResponse
from app.logging import get_logger
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])
logger = get_logger(__name__)


@router.get(
    "/agents",
    response_model=AgentListResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
)
async def list_dashboard_agents(
    session: AsyncSession = Depends(get_db_session),
) -> AgentListResponse:
    service = DashboardService(session)
    rows = await service.list_agents()
    return AgentListResponse(
        agents=[AgentSummary.model_validate(row) for row in rows]
    )


@router.get(
    "/state",
    response_model=DashboardStateResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
)
async def get_dashboard_state(
    agent_id: str = Query(..., alias="agentId", min_length=1),
    session: AsyncSession = Depends(get_db_session),
) -> DashboardStateResponse:
    """Full operator snapshot. Read-only; does not run the pipeline."""
    logger.info("dashboard_state_request", agent_id=agent_id)
    service = DashboardService(session)
    state = await service.get_state(agent_id.strip())
    if state is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return DashboardStateResponse(state=state)
