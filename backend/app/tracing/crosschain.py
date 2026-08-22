"""Following funds across a bridge, honestly.

When traced funds enter a bridge contract, the on-chain trail stops: the
destination-chain payout is a separate transaction with no cryptographic link to
the deposit. What can be done is correlation - look on the destination chain for
an outflow from the bridge in the right time window carrying roughly the right
value, minus fees.

That is an inference, not proof, and this module is careful to say so. Every
link it emits is tagged `is_inferred`, carries a confidence derived from how
well the time and value actually matched, and records the alternatives that were
considered. An LEA tool that overstated a bridge link would be worse than one
that never tried.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.base import Transfer
from app.chains.resolver import DataRouter
from app.core.enums import Chain, Direction, EntityType
from app.core.errors import UnsupportedChainError, UpstreamError
from app.db.models import ContractRegistryEntry
from app.logging_config import get_logger
from app.tracing.models import TraceGraph, TraceNode

logger = get_logger(__name__)

# A bridge payout normally lands within half an hour; allow generous slack for
# congested chains, and treat a fast match as stronger evidence.
TIME_WINDOW = timedelta(minutes=30)
IDEAL_TIME = timedelta(minutes=5)
# Bridges take a fee and slippage, so the destination amount is a little smaller.
VALUE_TOLERANCE = 0.03
IDEAL_VALUE_TOLERANCE = 0.005
# Never emit a link we would be embarrassed to explain.
MIN_CONFIDENCE = 0.35
MAX_CANDIDATES_PER_CHAIN = 200


@dataclass(slots=True)
class BridgeDeployment:
    """One bridge contract on one chain."""

    chain: Chain
    address: str
    name: str
    protocol: str


@dataclass(slots=True)
class BridgeEntry:
    """Traced funds arriving at a known bridge contract."""

    node: TraceNode
    bridge_name: str
    protocol: str
    src_chain: Chain
    destinations: list[BridgeDeployment]
    tx_hash: str
    ts: datetime
    value_usd: float
    taint: float


@dataclass(slots=True)
class CrossChainLink:
    """A probable continuation of the trail on another chain."""

    bridge_name: str
    src_chain: Chain
    src_address: str
    src_tx_hash: str
    src_ts: datetime
    dst_chain: Chain
    dst_address: str | None
    dst_tx_hash: str | None
    dst_ts: datetime | None
    value_usd: float
    value_delta_pct: float | None
    time_delta_seconds: int | None
    confidence: float
    taint: float
    is_inferred: bool = True
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def matched(self) -> bool:
        return self.dst_tx_hash is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_name": self.bridge_name,
            "src_chain": self.src_chain.value,
            "src_address": self.src_address,
            "src_tx_hash": self.src_tx_hash,
            "src_ts": self.src_ts.isoformat(),
            "dst_chain": self.dst_chain.value,
            "dst_address": self.dst_address,
            "dst_tx_hash": self.dst_tx_hash,
            "dst_ts": self.dst_ts.isoformat() if self.dst_ts else None,
            "value_usd": round(self.value_usd, 2),
            "value_delta_pct": (
                round(self.value_delta_pct, 4) if self.value_delta_pct is not None else None
            ),
            "time_delta_seconds": self.time_delta_seconds,
            "confidence": round(self.confidence, 3),
            "taint": round(self.taint, 6),
            "matched": self.matched,
            "is_inferred": self.is_inferred,
            "caveat": (
                "Heuristic correlation on time and value. This is an investigative "
                "lead, not a cryptographic link, and must be corroborated before "
                "being relied on as evidence."
            ),
            "evidence": self.evidence,
        }


def protocol_key(name: str | None) -> str:
    """Normalise a protocol name so deployments can be matched across chains.

    The registry calls it "Stargate / LayerZero" while a label might just say
    "Stargate"; both need to resolve to the same bridge.
    """
    return (name or "").split("/")[0].strip().lower()


class BridgeRegistry:
    """Known bridge contracts, indexed by address and by protocol.

    A bridge has a different contract address on every chain it serves, so
    finding the destination-side deployment means looking up the protocol, not
    reusing the source address.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._by_address: dict[tuple[str, str], BridgeDeployment] | None = None
        self._by_protocol: dict[str, list[BridgeDeployment]] = {}

    async def _load(self) -> dict[tuple[str, str], BridgeDeployment]:
        if self._by_address is None:
            rows = (
                await self.session.execute(
                    select(ContractRegistryEntry).where(
                        ContractRegistryEntry.entity_type == EntityType.BRIDGE
                    )
                )
            ).scalars().all()
            by_address: dict[tuple[str, str], BridgeDeployment] = {}
            by_protocol: dict[str, list[BridgeDeployment]] = {}
            for row in rows:
                deployment = BridgeDeployment(
                    chain=Chain(row.chain),
                    address=row.address,
                    name=row.name,
                    protocol=row.protocol,
                )
                by_address[(row.chain, row.address)] = deployment
                by_protocol.setdefault(protocol_key(row.protocol), []).append(deployment)
            self._by_address = by_address
            self._by_protocol = by_protocol
        return self._by_address

    async def lookup(self, chain: Chain, address: str) -> BridgeDeployment | None:
        registry = await self._load()
        return registry.get((chain.value, address))

    async def deployments_for(self, protocol: str) -> list[BridgeDeployment]:
        await self._load()
        return self._by_protocol.get(protocol_key(protocol), [])


def _score_match(
    time_delta: timedelta, value_delta_pct: float, unique_candidates: int
) -> float:
    """How much to trust one candidate payout.

    Three things make a match believable: it happened soon after the deposit,
    the value lines up after fees, and no other payout in the window looks just
    as good. The last matters most - a lone plausible candidate is worth far
    more than one of twenty.
    """
    seconds = abs(time_delta.total_seconds())
    time_score = max(
        0.0, 1.0 - max(0.0, seconds - IDEAL_TIME.total_seconds()) / TIME_WINDOW.total_seconds()
    )

    value_gap = max(0.0, abs(value_delta_pct) - IDEAL_VALUE_TOLERANCE)
    value_score = max(0.0, 1.0 - value_gap / VALUE_TOLERANCE)

    # Ambiguity penalty: confidence collapses as look-alike payouts pile up.
    uniqueness = 1.0 / unique_candidates if unique_candidates else 0.0

    blended = 0.35 * time_score + 0.35 * value_score + 0.30 * uniqueness
    # Cap below the level of a proven on-chain hop, always.
    return round(min(0.80, blended), 4)


class CrossChainCorrelator:
    def __init__(self, session: AsyncSession, router: DataRouter) -> None:
        self.session = session
        self.router = router
        self.registry = BridgeRegistry(session)

    async def find_bridge_entries(self, graph: TraceGraph) -> list[BridgeEntry]:
        """Traced nodes where the money left this chain.

        A bridge is recognised either from the contract registry or from an
        attribution that named the protocol - the TRON-side inbound address of a
        bridge is rarely in a public contract list, but it is usually labelled.
        """
        entries: list[BridgeEntry] = []
        for node in graph.nodes.values():
            if node.entity_type is not EntityType.BRIDGE:
                continue

            deployment = await self.registry.lookup(node.chain, node.address)
            protocol = deployment.protocol if deployment else node.entity_name
            if not protocol:
                continue

            destinations = [
                candidate
                for candidate in await self.registry.deployments_for(protocol)
                if candidate.chain is not node.chain
            ]
            if not destinations:
                logger.info(
                    "bridge_has_no_known_destination",
                    protocol=protocol,
                    address=node.address[:16],
                )
                continue

            inbound = graph.in_edges.get(node.address) or []
            if not inbound:
                continue
            deposit = max(inbound, key=lambda e: e.amount_usd)
            entries.append(
                BridgeEntry(
                    node=node,
                    bridge_name=deployment.name if deployment else (
                        node.entity_name or "Unknown bridge"
                    ),
                    protocol=protocol,
                    src_chain=node.chain,
                    destinations=destinations,
                    tx_hash=deposit.tx_hash,
                    ts=deposit.ts,
                    value_usd=sum(e.amount_usd for e in inbound),
                    taint=node.taint,
                )
            )
        return entries

    async def _payouts(
        self, chain: Chain, bridge_address: str, around: datetime
    ) -> list[Transfer]:
        """Outbound transfers from the bridge on the destination chain."""
        try:
            page = await self.router.get_transfers(
                chain, bridge_address, Direction.OUT, limit=MAX_CANDIDATES_PER_CHAIN
            )
        except (UpstreamError, UnsupportedChainError) as exc:
            logger.info(
                "crosschain_probe_failed", chain=chain.value, error=str(exc)[:120]
            )
            return []
        return [t for t in page.transfers if abs(t.ts - around) <= TIME_WINDOW]

    async def correlate(self, entry: BridgeEntry) -> list[CrossChainLink]:
        """Search every destination chain for a matching payout."""
        links: list[CrossChainLink] = []

        for deployment in entry.destinations:
            dst_chain = deployment.chain
            candidates = await self._payouts(dst_chain, deployment.address, entry.ts)
            plausible = [
                t
                for t in candidates
                if entry.value_usd > 0
                and abs(t.usd - entry.value_usd) / entry.value_usd <= VALUE_TOLERANCE
            ]

            if not plausible:
                links.append(
                    CrossChainLink(
                        bridge_name=entry.bridge_name,
                        src_chain=entry.src_chain,
                        src_address=entry.node.address,
                        src_tx_hash=entry.tx_hash,
                        src_ts=entry.ts,
                        dst_chain=dst_chain,
                        dst_address=None,
                        dst_tx_hash=None,
                        dst_ts=None,
                        value_usd=entry.value_usd,
                        value_delta_pct=None,
                        time_delta_seconds=None,
                        confidence=0.0,
                        taint=entry.taint,
                        evidence={
                            "searched_candidates": len(candidates),
                            "outcome": "no payout matched on time and value",
                        },
                    )
                )
                continue

            best = min(plausible, key=lambda t: abs((t.ts - entry.ts).total_seconds()))
            value_delta = (best.usd - entry.value_usd) / entry.value_usd
            time_delta = best.ts - entry.ts
            confidence = _score_match(time_delta, value_delta, len(plausible))

            if confidence < MIN_CONFIDENCE:
                logger.info(
                    "crosschain_link_too_weak",
                    chain=dst_chain.value,
                    candidates=len(plausible),
                    confidence=confidence,
                )

            links.append(
                CrossChainLink(
                    bridge_name=entry.bridge_name,
                    src_chain=entry.src_chain,
                    src_address=entry.node.address,
                    src_tx_hash=entry.tx_hash,
                    src_ts=entry.ts,
                    dst_chain=dst_chain,
                    dst_address=best.dst,
                    dst_tx_hash=best.tx_hash,
                    dst_ts=best.ts,
                    value_usd=entry.value_usd,
                    value_delta_pct=value_delta,
                    time_delta_seconds=int(time_delta.total_seconds()),
                    confidence=confidence,
                    taint=entry.taint * confidence,
                    evidence={
                        "candidates_in_window": len(plausible),
                        "searched_candidates": len(candidates),
                        "matched_on": "time window and value after fees",
                        "destination_amount_usd": round(best.usd, 2),
                    },
                )
            )

        return links

    async def run(self, graph: TraceGraph) -> list[CrossChainLink]:
        entries = await self.find_bridge_entries(graph)
        if not entries:
            return []

        all_links: list[CrossChainLink] = []
        for entry in entries:
            all_links.extend(await self.correlate(entry))

        matched = [link for link in all_links if link.matched]
        logger.info(
            "crosschain_correlation_complete",
            bridge_entries=len(entries),
            chains_searched=len(all_links),
            matches=len(matched),
        )
        # Strongest inference first; unmatched destinations still get reported so
        # the investigator knows the search happened and came back empty.
        all_links.sort(key=lambda link: link.confidence, reverse=True)
        return all_links
