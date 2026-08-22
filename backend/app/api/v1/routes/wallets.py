"""Wallet profile - everything known about one address on one page."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Query, Request
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.attribution import CandidateOut
from app.api.schemas.wallets import (
    CounterpartyOut,
    TransferOut,
    WalletLabel,
    WalletProfile,
    WalletTypology,
)
from app.attribution.service import AttributionService
from app.chains.base import Transfer
from app.chains.resolver import DataRouter, resolve
from app.core.enums import (
    TYPOLOGY_LABELS,
    Chain,
    DataSource,
    Direction,
    EntityType,
    Typology,
)
from app.db.models import Address, AddressLabel, TraceNode
from app.risk.scorer import score_address
from app.tracing.models import TraceNode as TracedNode

router = APIRouter(prefix="/wallets", tags=["wallets"])

MAX_TRANSFERS = 50
MAX_COUNTERPARTIES = 25


def _counterparties(
    transfers: list[Transfer], address: str
) -> list[CounterpartyOut]:
    """Aggregate raw transfers into who this wallet actually deals with."""
    buckets: dict[tuple[str, str], dict] = {}
    for transfer in transfers:
        outgoing = transfer.src.lower() == address.lower()
        other = transfer.dst if outgoing else transfer.src
        key = (other, "out" if outgoing else "in")
        bucket = buckets.setdefault(
            key,
            {
                "address": other,
                "chain": transfer.chain,
                "direction": "out" if outgoing else "in",
                "transfer_count": 0,
                "total_usd": 0.0,
                "first_seen": transfer.ts,
                "last_seen": transfer.ts,
            },
        )
        bucket["transfer_count"] += 1
        bucket["total_usd"] += float(transfer.amount_usd or 0.0)
        bucket["first_seen"] = min(bucket["first_seen"], transfer.ts)
        bucket["last_seen"] = max(bucket["last_seen"], transfer.ts)

    ranked = sorted(buckets.values(), key=lambda b: b["total_usd"], reverse=True)
    return [CounterpartyOut(**bucket) for bucket in ranked[:MAX_COUNTERPARTIES]]


@router.get("/{chain}/{address}", response_model=WalletProfile)
async def get_wallet(
    chain: Chain,
    address: str,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
    refresh: bool = Query(default=False, description="Ignore the cached profile"),
) -> WalletProfile:
    """Risk, labels, typologies, counterparties and recent transactions."""
    resolved = resolve(address, chain)
    address = resolved.address

    data_router = DataRouter(session)
    service = AttributionService(session, data_router)

    attribution = None
    if not refresh:
        attribution = await service.load_stored(chain, address)
    if attribution is None:
        attribution = await service.identify(chain, address)
        await service.persist(attribution)

    summary = await data_router.get_address_summary(chain, address)
    incoming = await data_router.get_transfers(chain, address, Direction.IN)
    outgoing = await data_router.get_transfers(chain, address, Direction.OUT)
    transfers = sorted(
        [*incoming.transfers, *outgoing.transfers], key=lambda t: t.ts, reverse=True
    )

    label_rows = (
        await session.execute(
            select(AddressLabel)
            .where(AddressLabel.chain == chain, AddressLabel.address == address)
            .order_by(desc(AddressLabel.confidence))
        )
    ).scalars().all()

    # Typologies are recorded against the address whenever a trace crosses it.
    node_rows = (
        await session.execute(
            select(TraceNode)
            .where(TraceNode.chain == chain, TraceNode.address == address)
            .order_by(desc(TraceNode.id))
        )
    ).scalars().all()

    typology_conf: dict[str, float] = defaultdict(float)
    for node in node_rows:
        for entry in node.typologies or []:
            if isinstance(entry, dict):
                name = entry.get("typology") or entry.get("name")
                confidence = float(entry.get("confidence", 0.6))
            else:
                name, confidence = str(entry), 0.6
            if name:
                typology_conf[name] = max(typology_conf[name], confidence)

    typologies = []
    for name, confidence in sorted(typology_conf.items(), key=lambda kv: -kv[1]):
        try:
            typology = Typology(name)
        except ValueError:
            continue
        typologies.append(
            WalletTypology(
                typology=typology.value,
                label=TYPOLOGY_LABELS[typology],
                confidence=confidence,
            )
        )

    # Reuse the trace scorer so a wallet's risk reads the same here as it does
    # on the graph, taking the worst position this address held in any trace.
    scored = score_address(
        TracedNode(
            chain=chain,
            address=address,
            hop=min((node.hop for node in node_rows), default=0),
            taint=max((node.taint for node in node_rows), default=0.0),
            attribution=attribution,
            typologies=[t.typology for t in typologies],
        )
    )
    cached_profile = (
        await session.execute(
            select(Address).where(Address.chain == chain, Address.address == address)
        )
    ).scalar_one_or_none()

    profile = WalletProfile(
        chain=chain,
        address=address,
        entity_type=attribution.entity_type,
        entity_name=attribution.entity_name,
        is_contract=summary.is_contract,
        is_sanctioned=attribution.is_sanctioned,
        risk_score=scored.score,
        risk_level=scored.level,
        risk_reasons=scored.reasons,
        balance_native=(
            str(summary.balance_native) if summary.balance_native is not None else None
        ),
        native_asset=chain.native_asset,
        tx_count=summary.tx_count,
        in_degree=summary.in_degree or len({t.src for t in incoming.transfers}),
        out_degree=summary.out_degree or len({t.dst for t in outgoing.transfers}),
        total_received_usd=summary.total_received_usd
        or sum(float(t.amount_usd or 0) for t in incoming.transfers),
        total_sent_usd=summary.total_sent_usd
        or sum(float(t.amount_usd or 0) for t in outgoing.transfers),
        first_seen=summary.first_seen or (transfers[-1].ts if transfers else None),
        last_seen=summary.last_seen or (transfers[0].ts if transfers else None),
        labels=[
            WalletLabel(
                label=row.label,
                source=row.source,
                category=row.category,
                entity_name=row.entity_name,
                confidence=row.confidence,
                is_sanctioned=row.is_sanctioned,
            )
            for row in label_rows
        ],
        typologies=typologies,
        attribution=(
            CandidateOut(**attribution.best.to_dict()) if attribution.best else None
        ),
        counterparties=_counterparties(transfers, address),
        recent_transfers=[
            TransferOut(
                tx_hash=t.tx_hash,
                chain=t.chain,
                ts=t.ts,
                block_number=t.block_number,
                direction="out" if t.src.lower() == address.lower() else "in",
                counterparty=t.dst if t.src.lower() == address.lower() else t.src,
                asset=t.asset,
                amount=str(t.amount),
                amount_usd=float(t.amount_usd) if t.amount_usd is not None else None,
                is_token=t.is_token,
            )
            for t in transfers[:MAX_TRANSFERS]
        ],
        seen_in_jobs=list({node.job_id for node in node_rows})[:20],
        data_source=data_router.effective_source or DataSource.SYNTHETIC,
        profiled_at=cached_profile.profiled_at if cached_profile else None,
    )

    await audit_request(
        session,
        request,
        user,
        action="wallet.read",
        resource_type="address",
        resource_id=f"{chain.value}:{address}",
        details={"risk_level": profile.risk_level.value},
    )
    await session.commit()
    return profile


@router.get("/{chain}/{address}/counterparties", response_model=list[CounterpartyOut])
async def get_counterparties(
    chain: Chain,
    address: str,
    session: SessionDep,
    user: CurrentUser,
    direction: Direction | None = None,
) -> list[CounterpartyOut]:
    resolved = resolve(address, chain)
    data_router = DataRouter(session)
    transfers: list[Transfer] = []
    if direction in (None, Direction.IN):
        transfers += (
            await data_router.get_transfers(chain, resolved.address, Direction.IN)
        ).transfers
    if direction in (None, Direction.OUT):
        transfers += (
            await data_router.get_transfers(chain, resolved.address, Direction.OUT)
        ).transfers
    return _counterparties(transfers, resolved.address)


@router.get("/entity-types", response_model=dict[str, str])
async def list_entity_types(user: CurrentUser) -> dict[str, str]:
    """Vocabulary for the UI's filter dropdowns and legends."""
    return {member.value: member.value.replace("_", " ").title() for member in EntityType}
