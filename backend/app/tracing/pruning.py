"""Decisions about where a trace stops.

Without pruning, a forward traversal of a busy address explodes: five hops at a
few hundred counterparties each is millions of nodes and far past any free-tier
API quota. Three limits keep it bounded - hops, fan-out per address, and the
minimum share of victim funds worth following - and a node budget backstops all
three.

Stopping is also a finding in its own right. An address that never moved the
money is the best possible outcome, and a branch that ended at the hop limit is
an explicit "keep looking here" for the investigator, so every stop records why.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import EntityType
from app.tracing.models import TerminalReason, TraceNode, TraceParams
from app.tracing.taint import Outflow, OutflowSet

# Entity types the money can enter but a forward trace cannot usefully leave.
OPAQUE_ENTITY_TYPES: dict[EntityType, str] = {
    EntityType.MIXER: TerminalReason.MIXER,
    EntityType.BRIDGE: TerminalReason.BRIDGE,
}


@dataclass(slots=True)
class ExpansionDecision:
    expand: bool
    terminal_reason: str | None = None
    note: str | None = None


def decide(node: TraceNode, params: TraceParams, nodes_used: int) -> ExpansionDecision:
    """Should this node be expanded, and if not, why not?

    Order matters: the most informative reason wins. A mixer at the hop limit is
    reported as a mixer, because that is what an investigator needs to read.

    Note that a sanctions hit deliberately does *not* stop the walk. It is a
    critical finding recorded on the node, but the money usually keeps moving
    after it and following it to an exchange is the entire point of the trace.
    """
    attribution = node.attribution

    if attribution and attribution.is_terminal(params.terminal_confidence):
        return ExpansionDecision(False, TerminalReason.VASP)

    opaque = OPAQUE_ENTITY_TYPES.get(node.entity_type)
    if opaque and node.attribution_confidence >= 0.6:
        return ExpansionDecision(False, opaque)

    if node.hop >= params.max_hops:
        return ExpansionDecision(False, TerminalReason.MAX_HOPS)

    if node.taint < params.min_taint:
        return ExpansionDecision(
            False,
            TerminalReason.MIN_TAINT,
            note=f"carries {node.taint:.2%} of traced value, below the "
            f"{params.min_taint:.2%} threshold",
        )

    if nodes_used >= params.max_nodes:
        return ExpansionDecision(False, TerminalReason.NODE_BUDGET)

    return ExpansionDecision(True)


@dataclass(slots=True)
class FanoutSelection:
    """Which counterparties made the cut, and what was left behind."""

    kept: list[Outflow]
    dropped_count: int = 0
    dropped_usd: float = 0.0
    dust_count: int = 0

    @property
    def truncated(self) -> bool:
        return self.dropped_count > 0


def select_fanout(outflows: OutflowSet, params: TraceParams) -> FanoutSelection:
    """Keep the highest-value counterparties, discarding dust first.

    Dust is dropped before ranking so that airdrop spam - endemic on EVM chains
    and TRON - cannot consume the fan-out budget. Dropped value still counts in
    the haircut denominator, so the taint of the kept edges stays honest.
    """
    ranked = [f for f in outflows.flows if f.value_usd >= params.min_edge_usd]
    dust_count = len(outflows.flows) - len(ranked)

    kept = ranked[: params.max_fanout]
    dropped = ranked[params.max_fanout :]
    return FanoutSelection(
        kept=kept,
        dropped_count=len(dropped),
        dropped_usd=sum(f.value_usd for f in dropped),
        dust_count=dust_count,
    )
