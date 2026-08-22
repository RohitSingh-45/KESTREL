"""Schemas for the alert inbox."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import AlertStatus, AlertType, Chain, RiskLevel


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str | None = None
    case_number: str | None = None
    job_id: str | None = None
    alert_type: AlertType
    severity: RiskLevel
    status: AlertStatus
    title: str
    message: str
    chain: Chain | None = None
    address: str | None = None
    vasp_slug: str | None = None
    vasp_name: str | None = None
    recommended_action: str | None = None
    sla_due_at: datetime | None = None
    # Negative once the SLA has been missed, which is what the inbox sorts on.
    sla_seconds_remaining: int | None = None
    is_overdue: bool = False
    acknowledged_at: datetime | None = None
    acknowledged_by_name: str | None = None
    created_at: datetime


class AlertUpdate(BaseModel):
    status: AlertStatus
    note: str | None = None


class AlertCounts(BaseModel):
    total: int = 0
    new: int = 0
    acknowledged: int = 0
    actioned: int = 0
    dismissed: int = 0
    overdue: int = 0
    critical: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
