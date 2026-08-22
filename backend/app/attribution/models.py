"""Attribution result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.enums import (
    VASP_ENTITY_TYPES,
    AttributionMethod,
    Chain,
    EntityType,
)

# How far each tier of evidence can be trusted, before per-signal adjustment.
METHOD_CEILING: dict[AttributionMethod, float] = {
    AttributionMethod.SANCTIONS_LIST: 1.0,
    AttributionMethod.DIRECT_LABEL: 0.99,
    AttributionMethod.CONTRACT_REGISTRY: 0.99,
    AttributionMethod.DEPOSIT_CONSOLIDATION: 0.90,
    AttributionMethod.COMMON_INPUT_CLUSTER: 0.85,
    AttributionMethod.ML_CLASSIFIER: 0.75,
}

METHOD_RANK: dict[AttributionMethod, int] = {
    AttributionMethod.SANCTIONS_LIST: 6,
    AttributionMethod.DIRECT_LABEL: 5,
    AttributionMethod.CONTRACT_REGISTRY: 4,
    AttributionMethod.DEPOSIT_CONSOLIDATION: 3,
    AttributionMethod.COMMON_INPUT_CLUSTER: 2,
    AttributionMethod.ML_CLASSIFIER: 1,
}

METHOD_DESCRIPTION: dict[AttributionMethod, str] = {
    AttributionMethod.SANCTIONS_LIST: "Address appears on the OFAC SDN list",
    AttributionMethod.DIRECT_LABEL: "Exact match against a known-address dataset",
    AttributionMethod.CONTRACT_REGISTRY: "Known mixer, bridge or protocol contract",
    AttributionMethod.DEPOSIT_CONSOLIDATION: (
        "Behaves as an exchange deposit address: receives from unrelated payers and "
        "sweeps its balance to a known exchange hot wallet"
    ),
    AttributionMethod.COMMON_INPUT_CLUSTER: (
        "Co-spent in a transaction with a labelled address, so both are controlled "
        "by the same wallet"
    ),
    AttributionMethod.ML_CLASSIFIER: "Behavioural classifier inference from on-chain activity",
}


@dataclass(slots=True)
class Candidate:
    """One hypothesis about who controls an address."""

    entity_name: str
    entity_type: EntityType
    method: AttributionMethod
    confidence: float
    vasp_slug: str | None = None
    is_sanctioned: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ceiling = METHOD_CEILING.get(self.method, 0.9)
        self.confidence = round(max(0.0, min(self.confidence, ceiling)), 4)

    @property
    def is_vasp(self) -> bool:
        return self.entity_type in VASP_ENTITY_TYPES

    @property
    def rank_key(self) -> tuple[float, int]:
        return (self.confidence, METHOD_RANK.get(self.method, 0))

    @property
    def description(self) -> str:
        return METHOD_DESCRIPTION.get(self.method, "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_name": self.entity_name,
            "entity_type": self.entity_type.value,
            "vasp_slug": self.vasp_slug,
            "method": self.method.value,
            "confidence": self.confidence,
            "is_sanctioned": self.is_sanctioned,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class Attribution:
    """Everything known about one address, best hypothesis first."""

    chain: Chain
    address: str
    candidates: list[Candidate] = field(default_factory=list)

    def add(self, candidate: Candidate | None) -> None:
        if candidate is not None:
            self.candidates.append(candidate)

    def finalise(self) -> Attribution:
        # A sanctions hit always leads, regardless of how confident other tiers are.
        self.candidates.sort(
            key=lambda c: (c.is_sanctioned, *c.rank_key), reverse=True
        )
        return self

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    @property
    def best_vasp(self) -> Candidate | None:
        return next((c for c in self.candidates if c.is_vasp and c.vasp_slug), None)

    @property
    def entity_type(self) -> EntityType:
        return self.best.entity_type if self.best else EntityType.UNKNOWN

    @property
    def entity_name(self) -> str | None:
        return self.best.entity_name if self.best else None

    @property
    def confidence(self) -> float:
        return self.best.confidence if self.best else 0.0

    @property
    def is_vasp(self) -> bool:
        return bool(self.best and self.best.is_vasp)

    @property
    def is_sanctioned(self) -> bool:
        return any(c.is_sanctioned for c in self.candidates)

    @property
    def vasp_slug(self) -> str | None:
        best_vasp = self.best_vasp
        return best_vasp.vasp_slug if best_vasp else None

    def is_terminal(self, threshold: float) -> bool:
        """Should the trace stop here?

        Only a confident VASP attribution terminates a branch - the money has
        left the open blockchain and continues inside the exchange's books.
        """
        best_vasp = self.best_vasp
        return bool(best_vasp and best_vasp.confidence >= threshold)

    def to_dict(self) -> dict[str, Any]:
        best = self.best
        return {
            "chain": self.chain.value,
            "address": self.address,
            "entity_name": self.entity_name,
            "entity_type": self.entity_type.value,
            "vasp_slug": self.vasp_slug,
            "method": best.method.value if best else None,
            "confidence": self.confidence,
            "is_sanctioned": self.is_sanctioned,
            "is_vasp": self.is_vasp,
            "evidence": best.evidence if best else {},
            "candidates": [c.to_dict() for c in self.candidates],
        }
