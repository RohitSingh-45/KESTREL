"""NCRP webhook, mock pusher, system status, audit log and runtime settings."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select

from app.api.deps import CurrentUser, RequireAdmin, SessionDep, audit_request
from app.api.schemas.common import Page
from app.api.v1.routes.cases import _next_case_number
from app.chains.resolver import provider_status, resolve
from app.config import settings
from app.core.cache import cache
from app.core.errors import AuthError, ValidationError
from app.core.runtime import DataMode, get_data_mode, set_data_mode
from app.db.models import Address, AddressLabel, Attribution, AuditLog, Case, SyntheticGroundTruth
from app.integrations.ncrp import NcrpComplaint, complaint_to_case, sample_complaint

router = APIRouter(prefix="/integrations", tags=["integrations"])


class SystemStatus(BaseModel):
    data_mode: str
    configured_data_mode: str
    cache_backend: str
    job_backend: str
    providers: list[dict[str, Any]]
    label_sources: list[dict[str, Any]]
    synthetic_scenarios: int
    database: str
    indexed_addresses: int
    indexed_attributions: int


class DataModeUpdate(BaseModel):
    data_mode: DataMode


class AuditOut(BaseModel):
    id: str
    ts: object
    actor_email: str | None = None
    actor_role: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    ip_address: str | None = None
    details: dict | None = None


class WebhookResult(BaseModel):
    created: bool
    case_id: str | None = None
    case_number: str | None = None
    addresses: int = 0
    message: str


class MockPushRequest(BaseModel):
    complaint: dict[str, Any] | None = Field(
        default=None, description="Override the canned demo complaint"
    )


def _check_ncrp_token(token: str | None) -> None:
    expected = settings.ncrp_webhook_token
    if not expected or token != expected:
        raise AuthError("Invalid NCRP webhook token")


@router.get("/system-status", response_model=SystemStatus)
async def system_status(session: SessionDep, user: CurrentUser) -> SystemStatus:
    sources = (
        await session.execute(
            select(AddressLabel.source, func.count()).group_by(AddressLabel.source)
        )
    ).all()
    sanctioned_by_source = dict(
        (
            await session.execute(
                select(AddressLabel.source, func.count()).where(
                    AddressLabel.is_sanctioned.is_(True)
                ).group_by(AddressLabel.source)
            )
        ).all()
    )
    scenarios = (
        await session.execute(select(func.count()).select_from(SyntheticGroundTruth))
    ).scalar_one()
    # The durable index behind "scalable indexing": every trace profiles
    # addresses and persists their attribution once, so overlapping or
    # repeat investigations read from here instead of re-deriving from
    # scratch. This is what's actually growing across cases, not a per-
    # trace graph that gets thrown away when the job ends.
    indexed_addresses = (
        await session.execute(select(func.count()).select_from(Address))
    ).scalar_one()
    indexed_attributions = (
        await session.execute(select(func.count()).select_from(Attribution))
    ).scalar_one()
    label_sources = [
        {
            "source": source,
            "labels": int(count),
            "sanctioned": int(sanctioned_by_source.get(source, 0)),
            "last_updated": None,
        }
        for source, count in sources
    ]
    dialect = settings.database_url.split("://", 1)[0]
    return SystemStatus(
        data_mode=get_data_mode(),
        configured_data_mode=settings.data_mode,
        cache_backend=cache.backend,
        job_backend=settings.job_backend,
        providers=provider_status(),
        label_sources=label_sources,
        synthetic_scenarios=int(scenarios),
        database=dialect,
        indexed_addresses=int(indexed_addresses),
        indexed_attributions=int(indexed_attributions),
    )


@router.patch("/settings", response_model=SystemStatus)
async def update_settings(
    payload: DataModeUpdate,
    session: SessionDep,
    admin: RequireAdmin,
    request: Request,
) -> SystemStatus:
    set_data_mode(payload.data_mode)
    await audit_request(
        session,
        request,
        admin,
        action="settings.data_mode",
        resource_type="system",
        details={"data_mode": payload.data_mode},
    )
    await session.commit()
    return await system_status(session, admin)


@router.get("/audit", response_model=Page[AuditOut])
async def list_audit(
    session: SessionDep,
    user: RequireAdmin,
    action: str | None = None,
    resource_type: str | None = None,
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditOut]:
    query = select(AuditLog)
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if q:
        term = f"%{q.strip()}%"
        query = query.where(
            AuditLog.actor_email.ilike(term) | AuditLog.resource_id.ilike(term)
        )
    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(desc(AuditLog.ts)).limit(limit).offset(offset)
        )
    ).scalars().all()
    items = [
        AuditOut(
            id=row.id,
            ts=row.ts,
            actor_email=row.actor_email,
            actor_role=row.actor_role,
            action=row.action,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            ip_address=row.ip_address,
            details=row.details,
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


async def _ingest_complaint(
    session: SessionDep,
    complaint: NcrpComplaint,
    *,
    assigned_to_id: str | None,
    intake_source: str,
) -> WebhookResult:
    resolved: list[tuple[Any, str]] = []
    for raw in complaint.addresses:
        try:
            found = resolve(raw, None)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"Could not resolve address {raw}: {exc}") from exc
        resolved.append((found.chain, found.address))
    if not resolved:
        raise ValidationError("Complaint contains no wallet addresses")

    case_number = complaint.case_number or await _next_case_number(session)
    existing = (
        await session.execute(select(Case).where(Case.case_number == case_number))
    ).scalar_one_or_none()
    if existing is not None:
        return WebhookResult(
            created=False,
            case_id=existing.id,
            case_number=existing.case_number,
            message="Case number already exists; inbound complaint ignored",
        )

    case = complaint_to_case(
        complaint,
        case_number=case_number,
        assigned_to_id=assigned_to_id,
        intake_source=intake_source,
        resolved_addresses=resolved,
    )
    session.add(case)
    await session.flush()
    return WebhookResult(
        created=True,
        case_id=case.id,
        case_number=case.case_number,
        addresses=len(resolved),
        message="Complaint accepted",
    )


@router.post("/ncrp/webhook", response_model=WebhookResult)
async def ncrp_webhook(
    payload: NcrpComplaint,
    request: Request,
    session: SessionDep,
    x_ncrp_token: str | None = Header(default=None),
) -> WebhookResult:
    """Inbound NCRP-schema complaint. Authenticated by shared webhook token."""
    _check_ncrp_token(x_ncrp_token or request.headers.get("authorization", "").removeprefix("Bearer "))
    result = await _ingest_complaint(
        session, payload, assigned_to_id=None, intake_source="ncrp_webhook"
    )
    await audit_request(
        session,
        request,
        None,
        action="integration.ncrp.webhook",
        resource_type="case",
        resource_id=result.case_id,
        details={"ack": payload.acknowledgement_no, "created": result.created},
    )
    await session.commit()
    return result


@router.post("/ncrp/mock-push", response_model=WebhookResult)
async def ncrp_mock_push(
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    payload: MockPushRequest | None = None,
) -> WebhookResult:
    """Simulate NCRP pushing a complaint so live intake is demonstrable."""
    raw = (payload.complaint if payload and payload.complaint else sample_complaint())
    complaint = NcrpComplaint.model_validate(raw)
    result = await _ingest_complaint(
        session, complaint, assigned_to_id=user.id, intake_source="ncrp_mock"
    )
    await audit_request(
        session,
        request,
        user,
        action="integration.ncrp.mock_push",
        resource_type="case",
        resource_id=result.case_id,
        details={"ack": complaint.acknowledgement_no},
    )
    await session.commit()
    return result

