"""Turns a finished trace into a list of next actions.

An investigator should not have to interpret taint percentages to know what to
do. Each recommendation names a concrete action, says why the trace produced it,
and carries an urgency so the queue sorts itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.tracing.models import TerminalReason, TraceGraph, TraceParams, TracePath
from app.tracing.paths import humanise_duration

URGENT = "urgent"
HIGH = "high"
NORMAL = "normal"

_URGENCY_RANK = {URGENT: 3, HIGH: 2, NORMAL: 1}


@dataclass(slots=True)
class Recommendation:
    action: str
    detail: str
    urgency: str = NORMAL
    kind: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "detail": self.detail,
            "urgency": self.urgency,
            "kind": self.kind,
        }


def build_recommendations(
    graph: TraceGraph, paths: list[TracePath], params: TraceParams
) -> list[dict[str, Any]]:
    out: list[Recommendation] = []

    seed_node = graph.seed_node
    if seed_node is not None and seed_node.terminal_reason == TerminalReason.NO_OUTFLOW:
        out.append(
            Recommendation(
                action="Monitor the reported address; funds have not moved",
                detail=(
                    "No outgoing transactions were found from the reported address. "
                    "Set up monitoring so a freeze request can be issued the moment "
                    "the funds are forwarded."
                ),
                urgency=HIGH,
                kind="monitor",
            )
        )
        return [r.to_dict() for r in out]

    vasp_paths = [p for p in paths if p.endpoint.terminal_reason == TerminalReason.VASP]
    for path in vasp_paths[:3]:
        node = path.endpoint
        elapsed = humanise_duration(path.elapsed_seconds)
        age = f" Funds arrived {elapsed} after the victim payment." if elapsed else ""
        out.append(
            Recommendation(
                action=(
                    f"Serve a Section 94 BNSS information request on "
                    f"{node.entity_name or node.vasp_slug} for deposit address "
                    f"{node.address}"
                ),
                detail=(
                    f"{node.taint * 100:.0f}% of the traced value reached this address at "
                    f"hop {node.hop}, attributed with {node.attribution_confidence * 100:.0f}% "
                    f"confidence.{age} Request KYC, account statement and an immediate "
                    "hold on the receiving account."
                ),
                urgency=URGENT if node.taint >= 0.25 else HIGH,
                kind="lea_request",
            )
        )

    sanctioned = [n for n in graph.nodes.values() if n.is_sanctioned]
    if sanctioned:
        names = ", ".join(sorted({n.entity_name or n.address for n in sanctioned})[:3])
        out.append(
            Recommendation(
                action="Escalate: sanctioned entity in the fund flow",
                detail=(
                    f"The traced funds passed through {len(sanctioned)} sanctioned "
                    f"address(es) ({names}). This engages sanctions-reporting "
                    "obligations in addition to the fraud investigation."
                ),
                urgency=URGENT,
                kind="sanctions",
            )
        )

    obscured = [
        p
        for p in paths
        if p.endpoint.terminal_reason in (TerminalReason.MIXER, TerminalReason.BRIDGE)
    ]
    for path in obscured[:2]:
        node = path.endpoint
        is_mixer = node.terminal_reason == TerminalReason.MIXER
        out.append(
            Recommendation(
                action=(
                    "Chain of custody breaks at a mixer; treat downstream links as inference"
                    if is_mixer
                    else "Run cross-chain correlation from the bridge entry"
                ),
                detail=(
                    f"{node.taint * 100:.0f}% of the traced value entered "
                    f"{node.entity_name or node.address} at hop {node.hop}. "
                    + (
                        "On-chain linkage cannot be proven past this point; any "
                        "post-mix attribution must be presented as a timing and value "
                        "inference, not as proof."
                        if is_mixer
                        else "Funds left this chain. Correlate the destination chain on "
                        "time window and value to recover the trail."
                    )
                ),
                urgency=HIGH,
                kind="mixer" if is_mixer else "bridge",
            )
        )

    at_rest = [
        p for p in paths if p.endpoint.terminal_reason == TerminalReason.NO_OUTFLOW
    ]
    resting_taint = sum(p.endpoint.taint for p in at_rest)
    if at_rest and resting_taint >= 0.01:
        out.append(
            Recommendation(
                action=f"Monitor {len(at_rest)} address(es) still holding funds",
                detail=(
                    f"{resting_taint * 100:.0f}% of the traced value has not moved on. "
                    "These balances are the most recoverable part of the flow if a "
                    "receiving service can be identified when they move."
                ),
                urgency=HIGH if resting_taint >= 0.2 else NORMAL,
                kind="monitor",
            )
        )

    hop_limited = [
        n
        for n in graph.nodes.values()
        if n.terminal_reason == TerminalReason.MAX_HOPS and n.taint >= 0.05
    ]
    if hop_limited:
        best = max(hop_limited, key=lambda n: n.taint)
        out.append(
            Recommendation(
                action=f"Re-run the trace with more than {params.max_hops} hops",
                detail=(
                    f"{len(hop_limited)} branch(es) were still carrying funds when the "
                    f"hop limit was reached, the largest holding {best.taint * 100:.0f}% "
                    f"of traced value at {best.address}. The money had not reached an "
                    "identifiable service yet."
                ),
                urgency=HIGH if best.taint >= 0.3 else NORMAL,
                kind="rerun",
            )
        )

    budget_limited = [
        n for n in graph.nodes.values() if n.terminal_reason == TerminalReason.NODE_BUDGET
    ]
    if budget_limited:
        out.append(
            Recommendation(
                action="Trace hit its node budget; narrow the scope or raise the limit",
                detail=(
                    f"{len(budget_limited)} branch(es) were abandoned at the "
                    f"{params.max_nodes}-address budget. Consider raising the minimum "
                    "taint threshold to focus on the branches carrying real value."
                ),
                kind="rerun",
            )
        )

    if not vasp_paths and not obscured:
        out.append(
            Recommendation(
                action="No custodial endpoint identified; widen the trace",
                detail=(
                    "None of the traced branches reached an address attributable to an "
                    "exchange or VASP. Try more hops, a lower taint threshold, or check "
                    "whether the reported address belongs to a chain not yet covered."
                ),
                urgency=HIGH,
                kind="rerun",
            )
        )

    out.sort(key=lambda r: _URGENCY_RANK.get(r.urgency, 0), reverse=True)
    return [r.to_dict() for r in out]
