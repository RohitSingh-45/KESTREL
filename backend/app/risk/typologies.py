"""Subgraph motif matchers for known laundering patterns.

Each detector answers one question about the traced graph in the language an
investigator already uses - "is this a peel chain?", "is this structuring?" -
and returns the addresses that triggered it so the finding can be pointed at in
a report rather than asserted.

Detectors are deliberately conservative. A false "mixer detected" in a court
filing is far more damaging than a missed pattern, so each one carries an
explicit confidence and the thresholds err towards silence.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from app.core.enums import (
    ILLICIT_ENTITY_TYPES,
    TYPOLOGY_LABELS,
    EntityType,
    Typology,
)
from app.tracing.models import TraceEdge, TraceGraph, TraceNode

# --- thresholds -------------------------------------------------------------
# Peel chain: the "remainder" leg must dominate, and the peel must be small.
PEEL_REMAINDER_MIN_SHARE = 0.70
PEEL_MAX_SHARE = 0.30
PEEL_MIN_LENGTH = 3

FAN_OUT_MIN_COUNTERPARTIES = 5
FAN_IN_MIN_COUNTERPARTIES = 5

RAPID_RELAY_MAX_SECONDS = 30 * 60
# A single address forwarding within minutes is already the signal: a mule
# wallet that flips straight to a VASP deposit address is the most common
# real shape, and it only has one address capable of exhibiting hold-then-
# forward behaviour (the deposit address itself is terminal).
RAPID_RELAY_MIN_HOPS = 1

STRUCTURING_MIN_TRANSFERS = 3
STRUCTURING_MAX_SPREAD = 0.15

DORMANT_MIN_IDLE_DAYS = 30

HIGH_VALUE_USD = 100_000


@dataclass(slots=True)
class TypologyHit:
    typology: Typology
    confidence: float
    addresses: list[str] = field(default_factory=list)
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return TYPOLOGY_LABELS[self.typology]

    def to_dict(self) -> dict[str, Any]:
        return {
            "typology": self.typology.value,
            "label": self.label,
            "confidence": round(self.confidence, 3),
            "addresses": self.addresses[:25],
            "address_count": len(self.addresses),
            "description": self.description,
            "evidence": self.evidence,
        }


def _out_by_counterparty(
    graph: TraceGraph, address: str
) -> dict[str, list[TraceEdge]]:
    grouped: dict[str, list[TraceEdge]] = {}
    for edge in graph.out_edges.get(address, []):
        grouped.setdefault(edge.dst, []).append(edge)
    return grouped


def _counterparty_values(graph: TraceGraph, address: str) -> dict[str, float]:
    return {
        dst: sum(e.amount_usd for e in edges)
        for dst, edges in _out_by_counterparty(graph, address).items()
    }


def _hold_seconds(graph: TraceGraph, node: TraceNode) -> float | None:
    """Time between funds arriving at an address and leaving it again."""
    inbound = graph.in_edges.get(node.address) or []
    outbound = graph.out_edges.get(node.address) or []
    if not inbound or not outbound:
        return None
    return (min(e.ts for e in outbound) - min(e.ts for e in inbound)).total_seconds()


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_peel_chain(graph: TraceGraph) -> TypologyHit | None:
    """A shrinking principal forwarded hop to hop, shedding a slice each time.

    The classic Bitcoin laundering shape: each address sends most of its balance
    onward and peels a small amount off to a cash-out address, so the trail is
    long, thin and tedious to follow by hand.
    """
    peel_links: dict[str, str] = {}
    peel_amounts: list[float] = []

    for address in graph.nodes:
        values = _counterparty_values(graph, address)
        if len(values) < 2:
            continue
        total = sum(values.values())
        if total <= 0:
            continue
        ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=True)
        remainder_dst, remainder_value = ranked[0]
        peel_value = sum(v for _, v in ranked[1:])
        if (
            remainder_value / total >= PEEL_REMAINDER_MIN_SHARE
            and peel_value / total <= PEEL_MAX_SHARE
            and peel_value > 0
        ):
            peel_links[address] = remainder_dst
            peel_amounts.append(peel_value / total)

    # Walk the links to find the longest consecutive run.
    best: list[str] = []
    for start in peel_links:
        if any(start == nxt for nxt in peel_links.values()):
            continue  # not the head of a chain
        run = [start]
        cursor = peel_links.get(start)
        while cursor in peel_links and cursor not in run:
            run.append(cursor)
            cursor = peel_links[cursor]
        if len(run) > len(best):
            best = run

    if len(best) < PEEL_MIN_LENGTH:
        return None

    mean_peel = statistics.mean(peel_amounts) if peel_amounts else 0.0
    confidence = min(0.95, 0.55 + 0.08 * (len(best) - PEEL_MIN_LENGTH))
    return TypologyHit(
        typology=Typology.PEEL_CHAIN,
        confidence=confidence,
        addresses=best,
        description=(
            f"{len(best)} consecutive addresses each forwarded most of their balance "
            f"onward while shedding about {mean_peel:.0%} to a side address."
        ),
        evidence={"chain_length": len(best), "mean_peel_share": round(mean_peel, 4)},
    )


def detect_fan_out(graph: TraceGraph) -> TypologyHit | None:
    """One address splitting funds across many counterparties to break the trail."""
    offenders: list[tuple[str, int, float]] = []
    for node in graph.nodes.values():
        values = _counterparty_values(graph, node.address)
        if len(values) < FAN_OUT_MIN_COUNTERPARTIES:
            continue
        total = sum(values.values())
        if total <= 0:
            continue
        # Evenly split output is the giveaway; one dominant leg is just a payment.
        largest_share = max(values.values()) / total
        if largest_share <= 0.6:
            offenders.append((node.address, len(values), largest_share))

    if not offenders:
        return None
    offenders.sort(key=lambda item: item[1], reverse=True)
    widest = offenders[0]
    return TypologyHit(
        typology=Typology.FAN_OUT_LAYERING,
        confidence=min(0.9, 0.5 + 0.05 * widest[1]),
        addresses=[address for address, _, _ in offenders],
        description=(
            f"{len(offenders)} address(es) split funds across many counterparties; "
            f"the widest sent to {widest[1]} different addresses with no dominant leg."
        ),
        evidence={"max_fan_out": widest[1], "addresses_involved": len(offenders)},
    )


def detect_fan_in(graph: TraceGraph) -> TypologyHit | None:
    """Many sources consolidating into one address - a collection or mule wallet."""
    offenders = [
        (node.address, node.observed_in_degree)
        for node in graph.nodes.values()
        if node.observed_in_degree >= FAN_IN_MIN_COUNTERPARTIES
        and node.entity_type is not EntityType.CEX_HOT_WALLET
    ]
    if not offenders:
        return None
    offenders.sort(key=lambda item: item[1], reverse=True)
    return TypologyHit(
        typology=Typology.FAN_IN_AGGREGATION,
        confidence=min(0.85, 0.5 + 0.04 * offenders[0][1]),
        addresses=[address for address, _ in offenders],
        description=(
            f"{len(offenders)} address(es) aggregated funds from multiple sources; "
            f"the largest collected from {offenders[0][1]} counterparties."
        ),
        evidence={"max_fan_in": offenders[0][1]},
    )


def detect_rapid_relay(graph: TraceGraph) -> TypologyHit | None:
    """Funds forwarded almost immediately on arrival - automated layering."""
    fast: list[tuple[str, float]] = []
    for node in graph.nodes.values():
        if node.is_seed:
            continue
        held = _hold_seconds(graph, node)
        if held is not None and 0 <= held <= RAPID_RELAY_MAX_SECONDS:
            fast.append((node.address, held))

    if len(fast) < RAPID_RELAY_MIN_HOPS:
        return None
    median_hold = statistics.median(held for _, held in fast)
    return TypologyHit(
        typology=Typology.RAPID_RELAY,
        confidence=min(0.9, 0.55 + 0.06 * len(fast)),
        addresses=[address for address, _ in fast],
        description=(
            f"{len(fast)} addresses forwarded funds within minutes of receiving them "
            f"(median hold {median_hold / 60:.0f} minutes), which indicates scripted "
            "rather than manual movement."
        ),
        evidence={
            "addresses": len(fast),
            "median_hold_seconds": round(median_hold, 1),
        },
    )


def detect_structuring(graph: TraceGraph) -> TypologyHit | None:
    """Value broken into several near-identical amounts to stay under thresholds.

    Checked per (sender, counterparty) pair, on the *individual* transfer
    amounts rather than their sum: `_counterparty_values` collapses each
    counterparty to one summed total, which can never show a repeated-amount
    pattern at all. A structurer splitting across two deposit addresses (three
    ~9.6k payments to one, two ~3k to another) has each pair's own transfers
    near-uniform - pooling both pairs together would average away exactly the
    signal being looked for.
    """
    offenders: list[tuple[str, int, float]] = []
    for address in graph.nodes:
        for edges in _out_by_counterparty(graph, address).values():
            values = [e.amount_usd for e in edges if e.amount_usd > 0]
            if len(values) < STRUCTURING_MIN_TRANSFERS:
                continue
            mean = statistics.mean(values)
            if mean <= 0:
                continue
            spread = statistics.pstdev(values) / mean
            if spread <= STRUCTURING_MAX_SPREAD:
                offenders.append((address, len(values), mean))
                break

    if not offenders:
        return None
    offenders.sort(key=lambda item: item[1], reverse=True)
    worst = offenders[0]
    return TypologyHit(
        typology=Typology.STRUCTURING,
        confidence=0.75,
        addresses=[address for address, _, _ in offenders],
        description=(
            f"{worst[1]} outgoing transfers of near-identical value "
            f"(about {worst[2]:,.0f} USD each) from {worst[0]}, consistent with "
            "deliberate structuring below reporting thresholds."
        ),
        evidence={
            "transfers": worst[1],
            "mean_value_usd": round(worst[2], 2),
            "addresses_involved": len(offenders),
        },
    )


def detect_dormant_burst(graph: TraceGraph) -> TypologyHit | None:
    """An address that sat idle for a long time and then moved everything.

    Uses `node.first_seen` rather than `_hold_seconds`: the forward trace
    graph only holds the edge that carried the victim's money in, but tier 2
    attribution samples an address's real inbound history, so `first_seen`
    can reach back to activity months before the traced flow ever touched it
    - exactly the idle period this typology is trying to measure.
    """
    offenders: list[tuple[str, float]] = []
    for node in graph.nodes.values():
        outbound = graph.out_edges.get(node.address) or []
        if not outbound or node.first_seen is None:
            continue
        held = (min(e.ts for e in outbound) - node.first_seen).total_seconds()
        idle_days = held / 86_400
        if idle_days >= DORMANT_MIN_IDLE_DAYS:
            offenders.append((node.address, idle_days))

    if not offenders:
        return None
    offenders.sort(key=lambda item: item[1], reverse=True)
    return TypologyHit(
        typology=Typology.DORMANT_BURST,
        confidence=0.7,
        addresses=[address for address, _ in offenders],
        description=(
            f"{offenders[0][0]} held funds for {offenders[0][1]:.0f} days before "
            "moving them, a cooling-off pattern used to outlast the reporting window."
        ),
        evidence={"max_idle_days": round(offenders[0][1], 1)},
    )


def _entity_hit(
    graph: TraceGraph, entity_type: EntityType, typology: Typology, wording: str
) -> TypologyHit | None:
    matches = [
        n
        for n in graph.nodes.values()
        if n.entity_type is entity_type and n.attribution_confidence >= 0.6
    ]
    if not matches:
        return None
    names = sorted({n.entity_name or n.address for n in matches})
    exposed = sum(n.taint for n in matches)
    return TypologyHit(
        typology=typology,
        confidence=max(n.attribution_confidence for n in matches),
        addresses=[n.address for n in matches],
        description=(
            f"{exposed:.0%} of the traced value {wording} ({', '.join(names[:3])})."
        ),
        evidence={"entities": names[:5], "taint_exposed": round(exposed, 4)},
    )


def detect_mixer(graph: TraceGraph) -> TypologyHit | None:
    return _entity_hit(
        graph, EntityType.MIXER, Typology.MIXER_INTERACTION, "entered a mixer"
    )


def detect_bridge(graph: TraceGraph) -> TypologyHit | None:
    return _entity_hit(
        graph, EntityType.BRIDGE, Typology.BRIDGE_INTERACTION, "entered a cross-chain bridge"
    )


def detect_sanctions(graph: TraceGraph) -> TypologyHit | None:
    matches = [n for n in graph.nodes.values() if n.is_sanctioned]
    if not matches:
        return None
    names = sorted({n.entity_name or n.address for n in matches})
    return TypologyHit(
        typology=Typology.SANCTIONS_EXPOSURE,
        confidence=1.0,
        addresses=[n.address for n in matches],
        description=(
            f"Traced funds touched {len(matches)} sanctioned address(es): "
            f"{', '.join(names[:3])}."
        ),
        evidence={"entities": names[:5]},
    )


def detect_direct_to_exchange(graph: TraceGraph) -> TypologyHit | None:
    """Money sent straight from the victim to a deposit address - no layering."""
    direct = [
        n
        for n in graph.nodes.values()
        if n.hop <= 2 and n.vasp_slug and n.attribution_confidence >= 0.7
    ]
    if not direct:
        return None
    node = max(direct, key=lambda n: n.taint)
    return TypologyHit(
        typology=Typology.DIRECT_TO_EXCHANGE,
        confidence=node.attribution_confidence,
        addresses=[n.address for n in direct],
        description=(
            f"Funds went straight from the reported address to a "
            f"{node.entity_name or node.vasp_slug} deposit address with no "
            "intermediate layering, so the exchange holds the first-hop KYC record."
        ),
        evidence={"vasp_slug": node.vasp_slug, "taint": round(node.taint, 4)},
    )


DETECTORS = (
    detect_sanctions,
    detect_mixer,
    detect_bridge,
    detect_peel_chain,
    detect_fan_out,
    detect_fan_in,
    detect_rapid_relay,
    detect_structuring,
    detect_dormant_burst,
    detect_direct_to_exchange,
)


def detect_all(graph: TraceGraph) -> list[TypologyHit]:
    """Run every detector, strongest signal first."""
    hits = [hit for detector in DETECTORS if (hit := detector(graph)) is not None]
    hits.sort(key=lambda h: h.confidence, reverse=True)
    return hits


def annotate_nodes(graph: TraceGraph, hits: list[TypologyHit]) -> None:
    """Tag each node with the typologies it participates in, for the UI chips."""
    for hit in hits:
        for address in hit.addresses:
            node = graph.nodes.get(address)
            if node is not None and hit.typology.value not in node.typologies:
                node.typologies.append(hit.typology.value)


def illicit_exposure(graph: TraceGraph) -> float:
    """Share of traced value that touched a known-criminal entity type."""
    return sum(
        node.taint
        for node in graph.nodes.values()
        if node.entity_type in ILLICIT_ENTITY_TYPES
    )
