"""In-memory representation of a trace, before it is persisted or serialised."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.attribution.models import Attribution
from app.config import settings
from app.core.enums import Chain, DataSource, EntityType, RiskLevel


class TerminalReason:
    """Why a branch stopped. Each one means something different to an investigator."""

    VASP = "vasp_endpoint"
    MIXER = "mixer_entry"
    BRIDGE = "bridge_entry"
    NO_OUTFLOW = "funds_at_rest"
    MAX_HOPS = "hop_limit"
    MIN_TAINT = "taint_below_threshold"
    NODE_BUDGET = "node_budget_exhausted"
    FETCH_FAILED = "data_unavailable"


TERMINAL_EXPLANATION: dict[str, str] = {
    TerminalReason.VASP: "Funds entered a custodial service; the trail continues in the exchange's internal ledger",
    TerminalReason.MIXER: "Funds entered a mixer, so on-chain linkage is broken here",
    TerminalReason.BRIDGE: "Funds entered a cross-chain bridge; see the cross-chain links",
    TerminalReason.NO_OUTFLOW: "Funds have not moved on and may still be recoverable at this address",
    TerminalReason.MAX_HOPS: "Reached the configured hop limit",
    TerminalReason.MIN_TAINT: "Share of victim funds fell below the tracing threshold",
    TerminalReason.NODE_BUDGET: "Trace stopped at the node budget",
    TerminalReason.FETCH_FAILED: "Transaction history could not be retrieved",
}


@dataclass(slots=True)
class TraceParams:
    """Tuning knobs. Defaults come from settings so one env var retunes every trace."""

    max_hops: int = field(default_factory=lambda: settings.trace_max_hops)
    max_fanout: int = field(default_factory=lambda: settings.trace_max_fanout)
    min_taint: float = field(default_factory=lambda: settings.trace_min_taint)
    max_nodes: int = field(default_factory=lambda: settings.trace_max_nodes)
    terminal_confidence: float = field(
        default_factory=lambda: settings.vasp_terminal_confidence
    )
    # Ignore counterparties below this value; dust and airdrop spam otherwise
    # dominate the fan-out budget on EVM chains.
    min_edge_usd: float = 1.0
    # Transfers pulled per address per hop.
    page_size: int = 200
    # At most this many individual transactions are drawn per counterparty, so a
    # noisy pair does not swamp the graph.
    max_tx_per_edge: int = 8

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_hops": self.max_hops,
            "max_fanout": self.max_fanout,
            "min_taint": self.min_taint,
            "max_nodes": self.max_nodes,
            "terminal_confidence": self.terminal_confidence,
            "min_edge_usd": self.min_edge_usd,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TraceParams:
        data = data or {}
        known = {
            key: data[key]
            for key in (
                "max_hops",
                "max_fanout",
                "min_taint",
                "max_nodes",
                "terminal_confidence",
                "min_edge_usd",
                "page_size",
                "max_tx_per_edge",
            )
            if data.get(key) is not None
        }
        return cls(**known)


@dataclass(slots=True)
class TraceEdge:
    """One observed transfer that carries a share of the victim's funds."""

    chain: Chain
    src: str
    dst: str
    tx_hash: str
    ts: datetime
    block_number: int
    asset: str
    amount: Decimal
    amount_usd: float
    hop: int
    taint_share: float = 0.0
    is_on_primary_path: bool = False
    edge_kind: str = "transfer"

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.src, self.dst, self.tx_hash, self.asset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": self.chain.value,
            "src": self.src,
            "dst": self.dst,
            "tx_hash": self.tx_hash,
            "ts": self.ts.isoformat(),
            "block_number": self.block_number,
            "asset": self.asset,
            "amount": str(self.amount),
            "amount_usd": round(self.amount_usd, 2),
            "hop": self.hop,
            "taint_share": round(self.taint_share, 6),
            "is_on_primary_path": self.is_on_primary_path,
            "edge_kind": self.edge_kind,
        }


@dataclass(slots=True)
class TraceNode:
    """An address reached by the trace, plus what we concluded about it."""

    chain: Chain
    address: str
    hop: int
    taint: float = 0.0
    is_seed: bool = False
    expanded: bool = False
    is_terminal: bool = False
    terminal_reason: str | None = None
    attribution: Attribution | None = None
    value_in_usd: float = 0.0
    value_out_usd: float = 0.0
    observed_in_degree: int = 0
    observed_out_degree: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    typologies: list[str] = field(default_factory=list)
    # Highest-taint inbound edge, used to reconstruct the primary path.
    best_parent: TraceEdge | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def entity_type(self) -> EntityType:
        return self.attribution.entity_type if self.attribution else EntityType.UNKNOWN

    @property
    def entity_name(self) -> str | None:
        return self.attribution.entity_name if self.attribution else None

    @property
    def vasp_slug(self) -> str | None:
        return self.attribution.vasp_slug if self.attribution else None

    @property
    def attribution_confidence(self) -> float:
        return self.attribution.confidence if self.attribution else 0.0

    @property
    def is_sanctioned(self) -> bool:
        return bool(self.attribution and self.attribution.is_sanctioned)

    def observe(self, ts: datetime) -> None:
        if self.first_seen is None or ts < self.first_seen:
            self.first_seen = ts
        if self.last_seen is None or ts > self.last_seen:
            self.last_seen = ts

    def to_dict(self) -> dict[str, Any]:
        best = self.attribution.best if self.attribution else None
        return {
            "chain": self.chain.value,
            "address": self.address,
            "hop": self.hop,
            "taint": round(self.taint, 6),
            "is_seed": self.is_seed,
            "is_terminal": self.is_terminal,
            "terminal_reason": self.terminal_reason,
            "terminal_explanation": (
                TERMINAL_EXPLANATION.get(self.terminal_reason)
                if self.terminal_reason
                else None
            ),
            "entity_type": self.entity_type.value,
            "entity_name": self.entity_name,
            "vasp_slug": self.vasp_slug,
            "attribution_method": best.method.value if best else None,
            "attribution_confidence": round(self.attribution_confidence, 4),
            "is_sanctioned": self.is_sanctioned,
            "value_in_usd": round(self.value_in_usd, 2),
            "value_out_usd": round(self.value_out_usd, 2),
            "in_degree": self.observed_in_degree,
            "out_degree": self.observed_out_degree,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "risk_score": round(self.risk_score, 1),
            "risk_level": self.risk_level.value,
            "typologies": list(self.typologies),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class TracePath:
    """A seed-to-endpoint route through the graph, as it would read in a report."""

    edges: list[TraceEdge]
    endpoint: TraceNode
    taint: float
    value_usd: float

    @property
    def hops(self) -> int:
        return len(self.edges)

    @property
    def elapsed_seconds(self) -> float | None:
        if not self.edges:
            return None
        return (self.edges[-1].ts - self.edges[0].ts).total_seconds()

    @property
    def addresses(self) -> list[str]:
        if not self.edges:
            return [self.endpoint.address]
        return [self.edges[0].src, *(edge.dst for edge in self.edges)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hops": self.hops,
            "taint": round(self.taint, 6),
            "value_usd": round(self.value_usd, 2),
            "elapsed_seconds": self.elapsed_seconds,
            "addresses": self.addresses,
            "endpoint": self.endpoint.to_dict(),
            "edges": [edge.to_dict() for edge in self.edges],
        }


class TraceGraph:
    """Adjacency structure built during traversal."""

    def __init__(self, chain: Chain, seed: str) -> None:
        self.chain = chain
        self.seed = seed
        self.nodes: dict[str, TraceNode] = {}
        self.edges: dict[tuple[str, str, str, str], TraceEdge] = {}
        self.out_edges: dict[str, list[TraceEdge]] = {}
        self.in_edges: dict[str, list[TraceEdge]] = {}

    def node(self, address: str, hop: int, chain: Chain | None = None) -> TraceNode:
        existing = self.nodes.get(address)
        if existing is not None:
            existing.hop = min(existing.hop, hop)
            return existing
        created = TraceNode(chain=chain or self.chain, address=address, hop=hop)
        self.nodes[address] = created
        return created

    def add_edge(self, edge: TraceEdge) -> TraceEdge:
        existing = self.edges.get(edge.key)
        if existing is not None:
            existing.taint_share = max(existing.taint_share, edge.taint_share)
            return existing
        self.edges[edge.key] = edge
        self.out_edges.setdefault(edge.src, []).append(edge)
        self.in_edges.setdefault(edge.dst, []).append(edge)
        return edge

    @property
    def seed_node(self) -> TraceNode | None:
        return self.nodes.get(self.seed)

    def terminals(self) -> list[TraceNode]:
        return [n for n in self.nodes.values() if n.is_terminal]

    def vasp_endpoints(self) -> list[TraceNode]:
        return [
            n
            for n in self.nodes.values()
            if n.terminal_reason == TerminalReason.VASP and n.vasp_slug
        ]

    def merge(
        self,
        other: TraceGraph,
        *,
        via: TraceEdge,
        hop_offset: int,
        taint_scale: float,
    ) -> None:
        """Graft a destination-chain continuation on after a bridge hop.

        Hops are renumbered so they read continuously from the victim, and taint
        is scaled by the confidence of the bridge inference - money that crossed
        a bridge on a 60%-confidence correlation cannot claim the same weight as
        money followed through a proven transaction.
        """
        self.add_edge(via)
        for node in other.nodes.values():
            node.hop += hop_offset
            node.taint *= taint_scale
            existing = self.nodes.get(node.address)
            if existing is not None:
                existing.taint += node.taint
                continue
            if node.is_seed:
                # The continuation seed is a normal node in the merged graph;
                # its parent is the inferred bridge edge.
                node.is_seed = False
                node.best_parent = via
                node.notes.append(
                    "Reached by inferred cross-chain correlation, not a proven transfer"
                )
            self.nodes[node.address] = node

        for edge in other.edges.values():
            edge.hop += hop_offset
            edge.taint_share *= taint_scale
            self.add_edge(edge)

    def path_to(self, address: str) -> TracePath | None:
        """Walk `best_parent` links back to the seed."""
        node = self.nodes.get(address)
        if node is None:
            return None
        edges: list[TraceEdge] = []
        cursor: TraceNode | None = node
        guard = 0
        while cursor is not None and not cursor.is_seed and guard < 64:
            edge = cursor.best_parent
            if edge is None:
                break
            edges.append(edge)
            cursor = self.nodes.get(edge.src)
            guard += 1
        edges.reverse()
        value = min((e.amount_usd for e in edges), default=node.value_in_usd)
        return TracePath(edges=edges, endpoint=node, taint=node.taint, value_usd=value)


@dataclass(slots=True)
class TraceStats:
    addresses_examined: int = 0
    addresses_expanded: int = 0
    transfers_examined: int = 0
    edges_kept: int = 0
    hops_completed: int = 0
    upstream_calls: int = 0
    duration_ms: int = 0
    data_source: DataSource = DataSource.SYNTHETIC
    notes: list[str] = field(default_factory=list)
    attribution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "addresses_examined": self.addresses_examined,
            "addresses_expanded": self.addresses_expanded,
            "transfers_examined": self.transfers_examined,
            "edges_kept": self.edges_kept,
            "hops_completed": self.hops_completed,
            "upstream_calls": self.upstream_calls,
            "duration_ms": self.duration_ms,
            "data_source": self.data_source.value,
            "notes": list(self.notes),
            "attribution": dict(self.attribution),
        }


@dataclass(slots=True)
class TraceResult:
    graph: TraceGraph
    params: TraceParams
    stats: TraceStats
    paths: list[TracePath] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def primary_path(self) -> TracePath | None:
        return self.paths[0] if self.paths else None
