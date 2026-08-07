"""Dashboard API schemas (operator UI)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_id: str = Field(alias="agentId")
    name: str
    domain: str
    status: str
    cycle_count: int = Field(alias="cycleCount")
    last_cycle_at: str | None = Field(default=None, alias="lastCycleAt")


class AgentListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agents: list[AgentSummary]


class DashboardStateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    state: dict[str, Any]
