"""Who controls this address? — the attribution engine's public surface."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.attribution import (
    AttributionResponse,
    CandidateOut,
    ResolveResponse,
    VaspContact,
)
from app.attribution.models import Attribution
from app.attribution.service import AttributionService
from app.chains.resolver import DataRouter, detect_active_chain, resolve
from app.core.enums import Chain
from app.db.models import Vasp

router = APIRouter(prefix="/attribution", tags=["attribution"])


def _vasp_contact(vasp: Vasp | None) -> VaspContact | None:
    if vasp is None:
        return None
    return VaspContact(
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
    )


async def _to_response(
    service: AttributionService,
    router_: DataRouter,
    attribution: Attribution,
    *,
    cached: bool,
) -> AttributionResponse:
    best = attribution.best
    return AttributionResponse(
        chain=attribution.chain,
        address=attribution.address,
        entity_name=attribution.entity_name,
        entity_type=attribution.entity_type,
        vasp_slug=attribution.vasp_slug,
        method=best.method if best else None,
        confidence=attribution.confidence,
        is_vasp=attribution.is_vasp,
        is_sanctioned=attribution.is_sanctioned,
        candidates=[CandidateOut(**c.to_dict()) for c in attribution.candidates],
        vasp=_vasp_contact(await service.vasp_for(attribution.vasp_slug)),
        data_source=router_.effective_source,
        notes=router_.stats()["notes"],
        cached=cached,
    )


@router.get("/resolve", response_model=ResolveResponse)
async def resolve_address(
    session: SessionDep,
    address: str = Query(min_length=8, max_length=128),
    chain: Chain | None = None,
    probe: bool = Query(
        default=False,
        description="For ambiguous EVM addresses, check which chain has activity",
    ),
) -> ResolveResponse:
    """Detect the chain from an address format, for the intake form."""
    resolved = resolve(address, chain)
    activity: dict[str, int] = {}
    detected = resolved.chain

    if probe and resolved.ambiguous and chain is None:
        result = await detect_active_chain(DataRouter(session), resolved)
        activity = result.activity
        detected = result.chain

    return ResolveResponse(
        address=resolved.address,
        chain=detected,
        ambiguous=resolved.ambiguous,
        candidates=resolved.candidates,
        format=resolved.format,
        activity=activity,
    )


@router.get("/{chain}/{address}", response_model=AttributionResponse)
async def attribute_address(
    chain: Chain,
    address: str,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    refresh: bool = Query(
        default=False,
        description="Re-run all tiers against upstream data instead of reusing the stored conclusion",
    ),
) -> AttributionResponse:
    data_router = DataRouter(session)
    service = AttributionService(session, data_router)

    if not refresh:
        stored = await service.load_stored(chain, address)
        if stored is not None:
            return await _to_response(service, data_router, stored, cached=True)

    attribution = await service.identify(chain, address)
    await service.persist(attribution)
    await audit_request(
        session,
        request,
        user,
        action="attribution.lookup",
        resource_type="address",
        resource_id=f"{chain.value}:{attribution.address}",
        details={
            "entity_type": attribution.entity_type.value,
            "confidence": attribution.confidence,
            "method": attribution.best.method.value if attribution.best else None,
        },
    )
    await session.commit()
    return await _to_response(service, data_router, attribution, cached=False)
