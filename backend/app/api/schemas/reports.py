"""Schemas for investigation packets and LEA request exports."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ReportKind


class ReportGenerateRequest(BaseModel):
    kind: ReportKind = ReportKind.INVESTIGATION_PACKET
    job_id: str | None = None
    sections: list[str] | None = Field(
        default=None,
        description="Optional subset of packet sections. Omit to include all.",
    )


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    job_id: str | None = None
    kind: ReportKind
    filename: str
    content_type: str
    sha256: str
    size_bytes: int
    generated_by_name: str | None = None
    sections: list[str] = Field(default_factory=list)
    custody_log: list[dict] = Field(default_factory=list)
    created_at: datetime
    download_url: str | None = None


class CustodyEvent(BaseModel):
    action: str
    actor: str | None = None
    at: datetime
    detail: str | None = None
