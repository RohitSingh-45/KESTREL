"""Turning a traced graph into a ranked answer.

A trace produces a few hundred nodes; an investigator needs one sentence. These
helpers pick the route that carried the most victim money to an identifiable
custodial endpoint and phrase the finding in the terms a case file uses.
"""

from __future__ import annotations

from typing import Any

from app.core.enums import EntityType
from app.tracing.models import (
    TERMINAL_EXPLANATION,
    TerminalReason,
    TraceGraph,
    TraceNode,
    TracePath,
)
from app.tracing.traversal import seed_first_movement

# Endpoints worth surfacing as an answer, best kind first.
ENDPOINT_PRIORITY: dict[str, int] = {
    TerminalReason.VASP: 4,
    TerminalReason.MIXER: 2,
    TerminalReason.BRIDGE: 2,
    TerminalReason.NO_OUTFLOW: 1,
}


def humanise_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    seconds = int(max(seconds, 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def rank_paths(graph: TraceGraph, limit: int = 25) -> list[TracePath]:
    """Order endpoints by how much of the victim's money reached them.

    Kind of endpoint outranks share of funds: a small confirmed deposit to an
    exchange is more actionable than a large flow that merely ran out of hops.
    """
    candidates: list[tuple[tuple[int, int, float, int], TracePath]] = []
    for node in graph.nodes.values():
        if not node.is_terminal or node.is_seed:
            continue
        priority = ENDPOINT_PRIORITY.get(node.terminal_reason or "", 0)
        if priority == 0 and node.taint <= 0:
            continue
        path = graph.path_to(node.address)
        if path is None:
            continue
        # Among endpoints of the same kind, a sanctions hit outranks value.
        candidates.append(
            ((priority, int(node.is_sanctioned), node.taint, -path.hops), path)
        )

    candidates.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in candidates[:limit]]


def mark_primary_path(graph: TraceGraph, path: TracePath | None) -> None:
    for edge in graph.edges.values():
        edge.is_on_primary_path = False
    if path is None:
        return
    for edge in path.edges:
        edge.is_on_primary_path = True


def _endpoint_entry(graph: TraceGraph, path: TracePath) -> dict[str, Any]:
    node = path.endpoint
    return {
        "address": node.address,
        "chain": node.chain.value,
        "hop": node.hop,
        "taint": round(node.taint, 6),
        "taint_pct": round(node.taint * 100, 2),
        "value_usd": round(node.value_in_usd, 2),
        "entity_type": node.entity_type.value,
        "entity_name": node.entity_name,
        "vasp_slug": node.vasp_slug,
        "confidence": round(node.attribution_confidence, 4),
        "method": (
            node.attribution.best.method.value
            if node.attribution and node.attribution.best
            else None
        ),
        "terminal_reason": node.terminal_reason,
        "terminal_explanation": TERMINAL_EXPLANATION.get(node.terminal_reason or ""),
        "is_sanctioned": node.is_sanctioned,
        "elapsed_seconds": path.elapsed_seconds,
        "elapsed_human": humanise_duration(path.elapsed_seconds),
        "hops": path.hops,
    }


def _finding_sentence(entry: dict[str, Any], method_label: str) -> str:
    name = entry.get("entity_name") or "an unidentified service"
    elapsed = entry.get("elapsed_human")
    when = f", {elapsed} after the victim payment" if elapsed else ""
    return (
        f"Funds reached a {name} deposit address ({entry['address']}) at hop "
        f"{entry['hop']}{when}. {entry['taint_pct']:.0f}% of traced value. "
        f"Confidence {entry['confidence'] * 100:.0f}% via {method_label}."
    )


METHOD_LABEL = {
    "direct_label": "a known-address match",
    "sanctions_list": "the OFAC sanctions list",
    "deposit_consolidation": "the deposit consolidation heuristic",
    "common_input_cluster": "common-input-ownership clustering",
    "ml_classifier": "behavioural classification",
    "contract_registry": "the contract registry",
}


def read_attribution(summary: dict[str, Any] | None) -> dict[str, Any]:
    """The headline finding out of a stored summary, or an empty verdict.

    Readers go through here rather than indexing the dict directly, so the
    summary's shape stays a single decision made in `build_summary`.
    """
    return (summary or {}).get("attribution") or {"found": False}


def read_risk(summary: dict[str, Any] | None) -> dict[str, Any]:
    return (summary or {}).get("risk") or {}


def read_stats(summary: dict[str, Any] | None) -> dict[str, Any]:
    return (summary or {}).get("stats") or {}


def build_summary(graph: TraceGraph, paths: list[TracePath]) -> dict[str, Any]:
    """The answer card: what happened to the money, in one object."""
    seed_moved_at = seed_first_movement(graph)
    endpoints = [_endpoint_entry(graph, path) for path in paths]

    vasp_paths = [
        p for p in paths if p.endpoint.terminal_reason == TerminalReason.VASP
    ]
    primary = vasp_paths[0] if vasp_paths else (paths[0] if paths else None)

    terminal_breakdown: dict[str, int] = {}
    for node in graph.nodes.values():
        if node.is_terminal and node.terminal_reason:
            terminal_breakdown[node.terminal_reason] = (
                terminal_breakdown.get(node.terminal_reason, 0) + 1
            )

    taint_to_vasps = sum(
        p.endpoint.taint for p in paths
        if p.endpoint.terminal_reason == TerminalReason.VASP
    )
    taint_at_rest = sum(
        p.endpoint.taint for p in paths
        if p.endpoint.terminal_reason == TerminalReason.NO_OUTFLOW
    )
    taint_obscured = sum(
        p.endpoint.taint for p in paths
        if p.endpoint.terminal_reason in (TerminalReason.MIXER, TerminalReason.BRIDGE)
    )

    attribution: dict[str, Any] = {"found": False}
    if primary is not None:
        entry = _endpoint_entry(graph, primary)
        is_vasp = primary.endpoint.terminal_reason == TerminalReason.VASP
        attribution = {
            **entry,
            "found": is_vasp,
            "statement": (
                _finding_sentence(entry, METHOD_LABEL.get(entry["method"] or "", "analysis"))
                if is_vasp
                else (
                    f"No custodial endpoint identified. The largest traced branch ends at "
                    f"{entry['address']} (hop {entry['hop']}, {entry['taint_pct']:.0f}% of "
                    f"traced value): {entry['terminal_explanation']}."
                )
            ),
        }

    unique_vasps: dict[str, dict[str, Any]] = {}
    for entry in endpoints:
        slug = entry.get("vasp_slug")
        if not slug:
            continue
        current = unique_vasps.get(slug)
        if current is None or entry["taint"] > current["taint"]:
            unique_vasps[slug] = entry

    return {
        "seed": {
            "chain": graph.chain.value,
            "address": graph.seed,
            "first_movement": seed_moved_at.isoformat() if seed_moved_at else None,
            "moved": seed_moved_at is not None,
        },
        "attribution": attribution,
        "endpoints": endpoints,
        "vasps": sorted(
            unique_vasps.values(), key=lambda e: e["taint"], reverse=True
        ),
        "counts": {
            "addresses": len(graph.nodes),
            "edges": len(graph.edges),
            "terminals": len(graph.terminals()),
            "vasp_endpoints": len(graph.vasp_endpoints()),
            "sanctioned": sum(1 for n in graph.nodes.values() if n.is_sanctioned),
            "mixers": sum(
                1 for n in graph.nodes.values() if n.entity_type is EntityType.MIXER
            ),
            "bridges": sum(
                1 for n in graph.nodes.values() if n.entity_type is EntityType.BRIDGE
            ),
        },
        "taint": {
            "reached_vasps": round(taint_to_vasps, 6),
            "at_rest": round(taint_at_rest, 6),
            "obscured": round(taint_obscured, 6),
        },
        "terminal_breakdown": terminal_breakdown,
    }


def hop_annexure(path: TracePath) -> list[dict[str, Any]]:
    """Hop-by-hop table for the investigation report."""
    rows: list[dict[str, Any]] = []
    for index, edge in enumerate(path.edges, start=1):
        rows.append(
            {
                "hop": index,
                "from": edge.src,
                "to": edge.dst,
                "chain": edge.chain.value,
                "tx_hash": edge.tx_hash,
                "block": edge.block_number,
                "timestamp_utc": edge.ts.isoformat(),
                "asset": edge.asset,
                "amount": str(edge.amount),
                "amount_usd": round(edge.amount_usd, 2),
                "share_of_victim_funds_pct": round(edge.taint_share * 100, 2),
            }
        )
    return rows


def describe_node(graph: TraceGraph, node: TraceNode) -> dict[str, Any]:
    """Node detail for the graph drawer, including its route from the seed."""
    path = graph.path_to(node.address)
    return {
        **node.to_dict(),
        "path": path.to_dict() if path else None,
        "counterparties_in": len(graph.in_edges.get(node.address, [])),
        "counterparties_out": len(graph.out_edges.get(node.address, [])),
    }
