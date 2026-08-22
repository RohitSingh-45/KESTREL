"""Schemas for the VASP directory."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.api.schemas.attribution import VaspContact


class VaspOut(VaspContact):
    model_config = ConfigDict(from_attributes=True)

    id: str
    aliases: list[str] = Field(default_factory=list)
    # Rolled up from attributions so the directory doubles as a league table of
    # which exchanges are actually receiving traced funds.
    attributed_addresses: int = 0
    cases_involved: int = 0
    open_alerts: int = 0


class VaspStats(BaseModel):
    total: int = 0
    indian_entities: int = 0
    fiu_registered: int = 0
    accepts_emergency_freeze: int = 0
    median_sla_hours: float = 0.0
