"""The alert inbox - what needs an investigator's attention, worst first."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from sqlalchemy import case as sql_case
from sqlalchemy import desc, func, select

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.alerts import AlertCounts, AlertOut, AlertUpdate
from app.api.schemas.common import Page
from app.core.enums import AlertStatus, AlertType, Chain, RiskLevel
from app.core.errors import NotFoundError
from app.db.models import Alert, Case, User, Vasp

router = APIRouter(prefix="/alerts", tags=["alerts"])

# Critical first, then High, and so on - the inbox is a queue, not a list.
SEVERITY_ORDER = sql_case(
    {
        RiskLevel.CRITICAL.value: 0,
        RiskLevel.HIGH.value: 1,
        RiskLevel.MEDIUM.value: 2,
        RiskLevel.LOW.value: 3,
    },
    value=Alert.severity,
    else_=4,
)

OPEN_STATUSES = (AlertStatus.NEW, AlertStatus.ACKNOWLEDGED)


def _alert_out(
    alert: Alert,
    *,
    case_number: str | None,
    vasp_name: str | None,
    actor_name: str | None,
    now: datetime,
) -> AlertOut:
    remaining: int | None = None
    overdue = False
    if alert.sla_due_at is not None:
        due = alert.sla_due_at
        if due.tzinfo is None:
            due = due.replace(tzinfo=UTC)
        remaining = int((due - now).total_seconds())
        overdue = remaining < 0 and alert.status in OPEN_STATUSES

    return AlertOut(
        id=alert.id,
        case_id=alert.case_id,
        case_number=case_number,
        job_id=alert.job_id,
        alert_type=AlertType(alert.alert_type),
        severity=RiskLevel(alert.severity),
        status=AlertStatus(alert.status),
        title=alert.title,
        message=alert.message,
        chain=Chain(alert.chain) if alert.chain else None,
        address=alert.address,
        vasp_slug=alert.vasp_slug,
        vasp_name=vasp_name,
        recommended_action=alert.recommended_action,
        sla_due_at=alert.sla_due_at,
        sla_seconds_remaining=remaining,
        is_overdue=overdue,
        acknowledged_at=alert.acknowledged_at,
        acknowledged_by_name=actor_name,
        created_at=alert.created_at,
    )


async def _decorate(session: SessionDep, rows: list[Alert]) -> list[AlertOut]:
    """One extra query per lookup table rather than one per alert."""
    now = datetime.now(UTC)
    case_ids = {row.case_id for row in rows if row.case_id}
    slugs = {row.vasp_slug for row in rows if row.vasp_slug}
    actor_ids = {row.acknowledged_by_id for row in rows if row.acknowledged_by_id}

    case_numbers: dict[str, str] = {}
    if case_ids:
        case_numbers = dict(
            (
                await session.execute(
                    select(Case.id, Case.case_number).where(Case.id.in_(case_ids))
                )
            ).all()
        )
    vasp_names: dict[str, str] = {}
    if slugs:
        vasp_names = dict(
            (
                await session.execute(
                    select(Vasp.slug, Vasp.name).where(Vasp.slug.in_(slugs))
                )
            ).all()
        )
    actors: dict[str, str] = {}
    if actor_ids:
        actors = dict(
            (
                await session.execute(
                    select(User.id, User.full_name).where(User.id.in_(actor_ids))
                )
            ).all()
        )

    return [
        _alert_out(
            row,
            case_number=case_numbers.get(row.case_id or ""),
            vasp_name=vasp_names.get(row.vasp_slug or ""),
            actor_name=actors.get(row.acknowledged_by_id or ""),
            now=now,
        )
        for row in rows
    ]


@router.get("", response_model=Page[AlertOut])
async def list_alerts(
    session: SessionDep,
    user: CurrentUser,
    alert_status: AlertStatus | None = Query(default=None, alias="status"),
    severity: RiskLevel | None = None,
    alert_type: AlertType | None = Query(default=None, alias="type"),
    case_id: str | None = None,
    open_only: bool = Query(default=False, description="New and acknowledged only"),
    overdue_only: bool = Query(default=False, description="Past their SLA deadline"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AlertOut]:
    query = select(Alert)
    if alert_status:
        query = query.where(Alert.status == alert_status)
    if open_only:
        query = query.where(Alert.status.in_(OPEN_STATUSES))
    if severity:
        query = query.where(Alert.severity == severity)
    if alert_type:
        query = query.where(Alert.alert_type == alert_type)
    if case_id:
        query = query.where(Alert.case_id == case_id)
    if overdue_only:
        query = query.where(
            Alert.sla_due_at.is_not(None),
            Alert.sla_due_at < datetime.now(UTC),
            Alert.status.in_(OPEN_STATUSES),
        )

    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            query.order_by(SEVERITY_ORDER, Alert.sla_due_at.asc(), desc(Alert.created_at))
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return Page(items=await _decorate(session, list(rows)), total=total, limit=limit, offset=offset)


@router.get("/counts", response_model=AlertCounts)
async def alert_counts(session: SessionDep, user: CurrentUser) -> AlertCounts:
    """Badge numbers for the sidebar."""
    by_status = dict(
        (
            await session.execute(select(Alert.status, func.count()).group_by(Alert.status))
        ).all()
    )
    by_type = dict(
        (
            await session.execute(
                select(Alert.alert_type, func.count()).group_by(Alert.alert_type)
            )
        ).all()
    )
    critical = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.severity == RiskLevel.CRITICAL, Alert.status.in_(OPEN_STATUSES))
        )
    ).scalar_one()
    overdue = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.sla_due_at.is_not(None),
                Alert.sla_due_at < datetime.now(UTC),
                Alert.status.in_(OPEN_STATUSES),
            )
        )
    ).scalar_one()

    return AlertCounts(
        total=sum(by_status.values()),
        new=by_status.get(AlertStatus.NEW, 0),
        acknowledged=by_status.get(AlertStatus.ACKNOWLEDGED, 0),
        actioned=by_status.get(AlertStatus.ACTIONED, 0),
        dismissed=by_status.get(AlertStatus.DISMISSED, 0),
        overdue=overdue,
        critical=critical,
        by_type={str(key): value for key, value in by_type.items()},
    )


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: str, session: SessionDep, user: CurrentUser) -> AlertOut:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} not found")
    return (await _decorate(session, [alert]))[0]


@router.patch("/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: str,
    payload: AlertUpdate,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> AlertOut:
    """Acknowledge, action or dismiss - each transition is audited."""
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise NotFoundError(f"Alert {alert_id} not found")

    previous = str(alert.status)
    alert.status = payload.status
    if payload.status != AlertStatus.NEW and alert.acknowledged_at is None:
        alert.acknowledged_at = datetime.now(UTC)
        alert.acknowledged_by_id = user.id
    if payload.note:
        history = list((alert.payload or {}).get("notes", []))
        history.append(
            {
                "note": payload.note,
                "by": user.full_name,
                "at": datetime.now(UTC).isoformat(),
            }
        )
        alert.payload = {**(alert.payload or {}), "notes": history}

    await audit_request(
        session,
        request,
        user,
        action="alert.update",
        resource_type="alert",
        resource_id=alert.id,
        details={"from": previous, "to": payload.status.value},
    )
    await session.commit()
    await session.refresh(alert)
    return (await _decorate(session, [alert]))[0]
