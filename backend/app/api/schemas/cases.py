"""Schemas for complaint intake and case management."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.enums import CasePriority, CaseStatus, Chain, FraudCategory


class CaseAddressIn(BaseModel):
    address: str = Field(min_length=8, max_length=128)
    chain: Chain | None = Field(
        default=None, description="Omit to auto-detect from the address format"
    )
    role: str = Field(default="reported", max_length=32)
    amount_sent_inr: float | None = Field(default=None, ge=0)
    note: str | None = None

    @field_validator("address")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class CaseAddressOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chain: Chain
    address: str
    role: str
    amount_sent_inr: float | None = None
    note: str | None = None
    # Filled from the most recent trace of this address, when there is one.
    last_job_id: str | None = None
    last_job_status: str | None = None
    attributed_vasp: str | None = None
    risk_level: str | None = None


class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str | None = None
    fraud_category: FraudCategory = FraudCategory.OTHER
    priority: CasePriority = CasePriority.NORMAL
    case_number: str | None = Field(default=None, max_length=64)
    ncrp_ack_no: str | None = Field(default=None, max_length=64)
    victim_name: str | None = None
    victim_contact: str | None = None
    loss_amount_inr: float | None = Field(default=None, ge=0)
    incident_date: datetime | None = None
    police_station: str | None = None
    state: str | None = None
    intake_source: str = "manual"
    addresses: list[CaseAddressIn] = Field(default_factory=list, max_length=200)


class CaseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: CaseStatus | None = None
    priority: CasePriority | None = None
    fraud_category: FraudCategory | None = None
    assigned_to_id: str | None = None
    loss_amount_inr: float | None = None


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_number: str
    ncrp_ack_no: str | None = None
    title: str
    description: str | None = None
    fraud_category: FraudCategory
    status: CaseStatus
    priority: CasePriority
    victim_name: str | None = None
    victim_contact: str | None = None
    loss_amount_inr: float | None = None
    incident_date: datetime | None = None
    reported_date: datetime | None = None
    police_station: str | None = None
    state: str | None = None
    intake_source: str
    assigned_to_name: str | None = None
    created_at: datetime
    updated_at: datetime
    addresses: list[CaseAddressOut] = Field(default_factory=list)
    address_count: int = 0
    alert_count: int = 0
    open_alert_count: int = 0


class CaseSummary(BaseModel):
    """List-view projection - no address expansion, cheap to page through."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    case_number: str
    title: str
    fraud_category: FraudCategory
    status: CaseStatus
    priority: CasePriority
    loss_amount_inr: float | None = None
    state: str | None = None
    created_at: datetime
    address_count: int = 0
    top_vasp: str | None = None
    highest_risk: str | None = None


class BulkIntakeRow(BaseModel):
    """One parsed row of an NCRP-style CSV upload."""

    row: int
    ok: bool
    address: str | None = None
    chain: Chain | None = None
    case_number: str | None = None
    title: str | None = None
    error: str | None = None


class BulkIntakeResult(BaseModel):
    filename: str
    total_rows: int
    accepted: int
    rejected: int
    cases_created: int
    addresses_added: int
    case_ids: list[str] = Field(default_factory=list)
    rows: list[BulkIntakeRow] = Field(default_factory=list)
    dry_run: bool = False
