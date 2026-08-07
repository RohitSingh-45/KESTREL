"""Evaluator-facing agent endpoints.

This module stays thin: schemas + injected services only.
It must not import scheduler, worker, LLM, or research modules directly.
Lifecycle registration happens inside AgentService via an injected protocol.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_agent_service, get_feed_service
from app.api.schemas.agent import InitRequest, InitResponse
from app.api.schemas.post import FeedResponse
from app.logging import get_logger
from app.services.agent_service import AgentService, FeedService

router = APIRouter(prefix="/api/agent", tags=["agent"])
logger = get_logger(__name__)


@router.post(
    "/init",
    response_model=InitResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
)
async def init_agent(
    body: InitRequest,
    service: AgentService = Depends(get_agent_service),
) -> InitResponse:
    """Persist agent and register autonomous lifecycle. Does not await a cycle."""
    logger.info(
        "agent_init_request",
        name=body.persona.name,
        domain=body.persona.domain,
    )
    return await service.initialize_agent(body.persona)


@router.get(
    "/feed",
    response_model=FeedResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
)
async def get_agent_feed(
    agent_id: str = Query(..., alias="agentId", min_length=1),
    service: FeedService = Depends(get_feed_service),
) -> FeedResponse:
    """Pure read of persisted posts. Never triggers generation."""
    logger.info("agent_feed_request", agent_id=agent_id)
    return await service.get_feed(agent_id)
