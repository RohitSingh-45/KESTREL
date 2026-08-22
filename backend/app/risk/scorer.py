"""Composite 0-100 risk scoring for addresses and for a whole trace.

A single number is only useful if you can say what produced it, so every score
carries the individual contributions that built it. The weights below are a
defensible starting point for a prototype, not a calibrated model - they are
stated in one place precisely so they can be argued with and tuned.

One rule overrides everything: an address on a sanctions list is Critical,
regardless of how small the amount or how distant the hop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import EntityType, RiskLevel, Typology
from app.risk.typologies import TypologyHit
from app.tracing.models import TraceGraph, TraceNode

# What each entity type contributes on its own, out of 100.
ENTITY_RISK: dict[EntityType, float] = {
    EntityType.SANCTIONED: 100.0,
    EntityType.RANSOMWARE: 85.0,
    EntityType.DARKNET: 80.0,
    EntityType.MIXER: 75.0,
    EntityType.SCAM: 70.0,
    EntityType.GAMBLING: 40.0,
    EntityType.BRIDGE: 35.0,
    EntityType.P2P_MERCHANT: 30.0,
    EntityType.DEFI_CONTRACT: 20.0,
    EntityType.CEX_DEPOSIT: 15.0,
    EntityType.CEX_HOT_WALLET: 10.0,
    EntityType.CEX_COLD_WALLET: 5.0,
    EntityType.VASP_OTHER: 15.0,
    EntityType.TOKEN_CONTRACT: 5.0,
    EntityType.PERSONAL_WALLET: 10.0,
    EntityType.VICTIM: 0.0,
    EntityType.UNKNOWN: 20.0,
}

# How much each typology adds when an address participates in it.
TYPOLOGY_RISK: dict[Typology, float] = {
    Typology.SANCTIONS_EXPOSURE: 60.0,
    Typology.MIXER_INTERACTION: 40.0,
    Typology.PEEL_CHAIN: 30.0,
    Typology.STRUCTURING: 30.0,
    Typology.FAN_OUT_LAYERING: 25.0,
    Typology.RAPID_RELAY: 20.0,
    Typology.BRIDGE_INTERACTION: 20.0,
    Typology.FAN_IN_AGGREGATION: 15.0,
    Typology.DORMANT_BURST: 15.0,
    Typology.DIRECT_TO_EXCHANGE: 5.0,
}

# Relative weight of each component in the blended address score.
WEIGHTS = {
    "entity": 0.40,
    "typology": 0.30,
    "taint": 0.20,
    "proximity": 0.10,
}


@dataclass(slots=True)
class RiskAssessment:
    score: float
    level: RiskLevel
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    sanctions_override: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "level": self.level.value,
            "components": {k: round(v, 1) for k, v in self.components.items()},
            "reasons": list(self.reasons),
            "sanctions_override": self.sanctions_override,
        }


def _proximity_score(node: TraceNode) -> float:
    """Closer to the victim means more directly implicated."""
    return max(0.0, 100.0 - node.hop * 18.0)


def score_address(node: TraceNode) -> RiskAssessment:
    """Blend entity type, typology participation, taint and hop distance."""
    if node.is_sanctioned:
        return RiskAssessment(
            score=100.0,
            level=RiskLevel.CRITICAL,
            components={"sanctions": 100.0},
            reasons=["Address appears on a sanctions list"],
            sanctions_override=True,
        )

    entity = ENTITY_RISK.get(node.entity_type, 20.0)
    # An entity guess is only worth as much as the confidence behind it; fall
    # back toward the "unknown" baseline when attribution is weak.
    if node.attribution_confidence:
        entity = (
            entity * node.attribution_confidence
            + ENTITY_RISK[EntityType.UNKNOWN] * (1 - node.attribution_confidence)
        )

    typology = min(
        100.0,
        sum(TYPOLOGY_RISK.get(Typology(t), 10.0) for t in node.typologies),
    )
    taint = min(100.0, node.taint * 100.0)
    proximity = _proximity_score(node)

    components = {
        "entity": entity,
        "typology": typology,
        "taint": taint,
        "proximity": proximity,
    }
    score = sum(components[key] * weight for key, weight in WEIGHTS.items())

    reasons: list[str] = []
    if entity >= 60:
        reasons.append(
            f"Controlled by a high-risk entity type ({node.entity_type.value})"
        )
    if typology >= 30:
        reasons.append(f"Participates in {len(node.typologies)} laundering pattern(s)")
    if taint >= 50:
        reasons.append(f"Carries {node.taint:.0%} of the victim's traced funds")
    if node.hop <= 1 and not node.is_seed:
        reasons.append("Received the victim's funds directly")
    if not reasons:
        reasons.append("No specific risk indicators beyond proximity to the case")

    return RiskAssessment(
        score=round(min(100.0, score), 1),
        level=RiskLevel.from_score(score),
        components=components,
        reasons=reasons,
    )


def score_graph(graph: TraceGraph, hits: list[TypologyHit]) -> RiskAssessment:
    """Case-level risk: the worst thing the money touched, weighted by how much.

    A trace is as risky as the most serious thing a meaningful share of the
    funds reached. Taking a plain average would let one sanctioned hop be
    diluted into invisibility by a hundred ordinary addresses.
    """
    if any(node.is_sanctioned for node in graph.nodes.values()):
        return RiskAssessment(
            score=100.0,
            level=RiskLevel.CRITICAL,
            components={"sanctions": 100.0},
            reasons=["Traced funds touched a sanctioned address"],
            sanctions_override=True,
        )

    if not graph.nodes:
        return RiskAssessment(score=0.0, level=RiskLevel.LOW)

    weighted = 0.0
    weight_total = 0.0
    peak = 0.0
    for node in graph.nodes.values():
        if node.is_seed:
            continue
        weight = max(node.taint, 0.01)
        weighted += node.risk_score * weight
        weight_total += weight
        peak = max(peak, node.risk_score)

    average = weighted / weight_total if weight_total else 0.0
    typology_bonus = min(
        30.0, sum(TYPOLOGY_RISK.get(hit.typology, 10.0) * hit.confidence for hit in hits) / 3
    )
    # Blend towards the peak so a single serious hop still shows through.
    score = min(100.0, 0.55 * average + 0.30 * peak + typology_bonus)

    reasons = [hit.description for hit in hits[:4]]
    if not reasons:
        reasons.append("No laundering typologies detected in the traced flow")

    return RiskAssessment(
        score=round(score, 1),
        level=RiskLevel.from_score(score),
        components={
            "weighted_average": round(average, 1),
            "peak_address": round(peak, 1),
            "typologies": round(typology_bonus, 1),
        },
        reasons=reasons,
    )


RECOMMENDED_ACTION: dict[RiskLevel, str] = {
    RiskLevel.CRITICAL: (
        "Escalate immediately. Issue freeze requests to the identified VASP and "
        "notify FIU-IND; sanctions or mixer exposure is present."
    ),
    RiskLevel.HIGH: (
        "Prioritise. Serve a Section 94 BNSS request on the identified VASP within "
        "24 hours while the funds are still traceable."
    ),
    RiskLevel.MEDIUM: (
        "Proceed on the normal timeline. Request KYC from the identified service and "
        "monitor the addresses still holding funds."
    ),
    RiskLevel.LOW: (
        "Low priority. Record the finding and monitor for further movement."
    ),
}


def apply_scores(graph: TraceGraph, hits: list[TypologyHit]) -> RiskAssessment:
    """Score every node, then the trace as a whole."""
    for node in graph.nodes.values():
        assessment = score_address(node)
        node.risk_score = assessment.score
        node.risk_level = assessment.level
    return score_graph(graph, hits)
