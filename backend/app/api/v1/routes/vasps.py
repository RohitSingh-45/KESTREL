"""VASP directory - who to serve the request on, and how to reach them."""

from __future__ import annotations

from statistics import median

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.schemas.common import Page
from app.api.schemas.vasps import VaspOut, VaspStats
from app.core.enums import AlertStatus
from app.core.errors import NotFoundError
from app.db.models import Alert, Attribution, Vasp

router = APIRouter(prefix="/vasps", tags=["vasps"])


def _vasp_out(vasp: Vasp, *, attributed: int, alerts: int) -> VaspOut:
    return VaspOut(
        id=vasp.id,
        slug=vasp.slug,
        name=vasp.name,
        legal_name=vasp.legal_name,
        jurisdiction=vasp.jurisdiction,
        country_code=vasp.country_code,
        is_indian_entity=vasp.is_indian_entity,
        fiu_ind_registered=vasp.fiu_ind_registered,
        fiu_ind_reg_no=vasp.fiu_ind_reg_no,
        compliance_email=vasp.compliance_email,
        grievance_officer=vasp.grievance_officer,
        law_enforcement_portal=vasp.law_enforcement_portal,
        response_sla_hours=vasp.response_sla_hours,
        accepts_emergency_freeze=vasp.accepts_emergency_freeze,
        notes=vasp.notes,
        aliases=list(vasp.aliases or []),
        attributed_addresses=attributed,
        open_alerts=alerts,
    )


async def _rollups(session: SessionDep) -> tuple[dict[str, int], dict[str, int]]:
    attributed = dict(
        (
            await session.execute(
                select(Attribution.vasp_slug, func.count())
                .where(Attribution.vasp_slug.is_not(None))
                .group_by(Attribution.vasp_slug)
            )
        ).all()
    )
    alerts = dict(
        (
            await session.execute(
                select(Alert.vasp_slug, func.count())
                .where(
                    Alert.vasp_slug.is_not(None),
                    Alert.status.in_((AlertStatus.NEW, AlertStatus.ACKNOWLEDGED)),
                )
                .group_by(Alert.vasp_slug)
            )
        ).all()
    )
    return attributed, alerts


@router.get("", response_model=Page[VaspOut])
async def list_vasps(
    session: SessionDep,
    user: CurrentUser,
    q: str | None = Query(default=None, description="Match name, slug or jurisdiction"),
    indian_only: bool = False,
    fiu_registered_only: bool = False,
    emergency_freeze_only: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[VaspOut]:
    query = select(Vasp)
    if q:
        term = f"%{q.strip()}%"
        query = query.where(
            Vasp.name.ilike(term) | Vasp.slug.ilike(term) | Vasp.jurisdiction.ilike(term)
        )
    if indian_only:
        query = query.where(Vasp.is_indian_entity.is_(True))
    if fiu_registered_only:
        query = query.where(Vasp.fiu_ind_registered.is_(True))
    if emergency_freeze_only:
        query = query.where(Vasp.accepts_emergency_freeze.is_(True))

    total = (
        await session.execute(select(func.count()).select_from(query.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            # Indian, FIU-registered entities first: those are the ones an
            # Indian investigator can actually compel quickly.
            query.order_by(
                Vasp.is_indian_entity.desc(), Vasp.fiu_ind_registered.desc(), Vasp.name
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    attributed, alerts = await _rollups(session)
    items = [
        _vasp_out(
            row,
            attributed=attributed.get(row.slug, 0),
            alerts=alerts.get(row.slug, 0),
        )
        for row in rows
    ]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/stats", response_model=VaspStats)
async def vasp_stats(session: SessionDep, user: CurrentUser) -> VaspStats:
    rows = (await session.execute(select(Vasp))).scalars().all()
    if not rows:
        return VaspStats()
    return VaspStats(
        total=len(rows),
        indian_entities=sum(1 for row in rows if row.is_indian_entity),
        fiu_registered=sum(1 for row in rows if row.fiu_ind_registered),
        accepts_emergency_freeze=sum(1 for row in rows if row.accepts_emergency_freeze),
        median_sla_hours=float(median(row.response_sla_hours for row in rows)),
    )


@router.get("/{slug}", response_model=VaspOut)
async def get_vasp(slug: str, session: SessionDep, user: CurrentUser) -> VaspOut:
    vasp = (
        await session.execute(select(Vasp).where(Vasp.slug == slug))
    ).scalar_one_or_none()
    if vasp is None:
        raise NotFoundError(f"VASP {slug} not found")
    attributed, alerts = await _rollups(session)
    return _vasp_out(
        vasp, attributed=attributed.get(slug, 0), alerts=alerts.get(slug, 0)
    )
