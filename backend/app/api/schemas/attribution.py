"""Schemas for attribution lookups and chain resolution."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import AttributionMethod, Chain, DataSource, EntityType


class VaspContact(BaseModel):
    """Everything an investigator needs to actually send a request."""

    slug: str
    name: str
    legal_name: str | None = None
    jurisdiction: str
    country_code: str | None = None
    is_indian_entity: bool = False
    fiu_ind_registered: bool = False
    fiu_ind_reg_no: str | None = None
    compliance_email: str | None = None
    grievance_officer: str | None = None
    law_enforcement_portal: str | None = None
    response_sla_hours: int = 72
    accepts_emergency_freeze: bool = False
    notes: str | None = None


class CandidateOut(BaseModel):
    entity_name: str
    entity_type: EntityType
    vasp_slug: str | None = None
    method: AttributionMethod
    confidence: float = Field(ge=0.0, le=1.0)
    is_sanctioned: bool = False
    description: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)


class AttributionResponse(BaseModel):
    chain: Chain
    address: str
    entity_name: str | None = None
    entity_type: EntityType = EntityType.UNKNOWN
    vasp_slug: str | None = None
    method: AttributionMethod | None = None
    confidence: float = 0.0
    is_vasp: bool = False
    is_sanctioned: bool = False
    candidates: list[CandidateOut] = Field(default_factory=list)
    vasp: VaspContact | None = None
    data_source: DataSource = DataSource.SYNTHETIC
    notes: list[str] = Field(default_factory=list)
    cached: bool = False


class ResolveResponse(BaseModel):
    """Chain auto-detection for the intake form."""

    address: str
    chain: Chain
    ambiguous: bool
    candidates: list[Chain]
    format: str
    activity: dict[str, int] = Field(
        default_factory=dict,
        description="Transaction count per candidate chain, when probing was requested",
    )
