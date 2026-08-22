"""Tier 2: exchange deposit-address detection by consolidation pattern.

An exchange gives each customer a unique deposit address. Those addresses are
never published, so no label list contains them - yet they are exactly where a
victim's money lands, and exactly what a freeze request must name.

They are recognisable by behaviour rather than identity:

  1. money arrives from several unrelated external addresses,
  2. the balance is then swept out, essentially in full,
  3. the sweep goes to an address already known to be an exchange hot wallet.

That third point is what upgrades a guess into an attribution: the destination
is a labelled entity, so the deposit address inherits that entity's identity.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.attribution.labels import LabelStore
from app.attribution.models import Candidate
from app.chains.base import Transfer
from app.core.enums import AttributionMethod, Chain, EntityType

# Tuning. Deliberately conservative: a false VASP attribution sends an
# investigator to the wrong company and wastes the window for freezing funds.
MIN_SWEEP_RATIO = 0.55
MIN_DISTINCT_PAYERS = 2
STRONG_SWEEP_RATIO = 0.90
STRONG_PAYER_COUNT = 4
BASE_CONFIDENCE = 0.72


@dataclass
class ConsolidationEvidence:
    vasp_slug: str
    entity_name: str
    hot_wallet: str
    sweep_count: int
    sweep_value_usd: float
    total_out_usd: float
    sweep_ratio: float
    distinct_payers: int
    exclusive_destination: bool
    median_hold_seconds: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "hot_wallet": self.hot_wallet,
            "sweep_count": self.sweep_count,
            "sweep_value_usd": round(self.sweep_value_usd, 2),
            "total_outbound_usd": round(self.total_out_usd, 2),
            "sweep_ratio": round(self.sweep_ratio, 4),
            "distinct_inbound_payers": self.distinct_payers,
            "sweeps_exclusively_to_this_hot_wallet": self.exclusive_destination,
            "median_hold_seconds": (
                round(self.median_hold_seconds) if self.median_hold_seconds else None
            ),
            "heuristic": "deposit-address consolidation",
            "notes": self.notes,
        }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _score(evidence: ConsolidationEvidence) -> float:
    confidence = BASE_CONFIDENCE
    if evidence.sweep_ratio >= STRONG_SWEEP_RATIO:
        confidence += 0.07
    elif evidence.sweep_ratio >= 0.75:
        confidence += 0.04
    if evidence.distinct_payers >= STRONG_PAYER_COUNT:
        confidence += 0.06
    elif evidence.distinct_payers >= MIN_DISTINCT_PAYERS:
        confidence += 0.03
    if evidence.exclusive_destination:
        confidence += 0.04
    if evidence.sweep_count >= 3:
        confidence += 0.03
    # A single payer with a single sweep is equally consistent with an ordinary
    # user forwarding their own funds to an exchange.
    if evidence.distinct_payers < MIN_DISTINCT_PAYERS and evidence.sweep_count < 2:
        confidence -= 0.14
    return confidence


async def detect_deposit_address(
    store: LabelStore,
    chain: Chain,
    address: str,
    outbound: list[Transfer],
    inbound: list[Transfer] | None = None,
) -> Candidate | None:
    """Classify `address` as an exchange deposit address, or return None."""
    if not outbound:
        return None

    total_out_usd = sum(t.usd for t in outbound) or 0.0
    if total_out_usd <= 0:
        return None

    # Group outbound value by destination, keeping only known hot wallets.
    by_destination: dict[str, list[Transfer]] = defaultdict(list)
    for transfer in outbound:
        by_destination[transfer.dst].append(transfer)

    hot_wallet_flows: list[tuple[str, str, str, list[Transfer]]] = []
    for destination, transfers in by_destination.items():
        is_hot, slug, entity_name = await store.is_hot_wallet(chain, destination)
        if is_hot and slug:
            hot_wallet_flows.append((destination, slug, entity_name or slug, transfers))

    if not hot_wallet_flows:
        return None

    # If several exchanges appear, attribute to the one taking the most value.
    hot_wallet_flows.sort(key=lambda item: sum(t.usd for t in item[3]), reverse=True)
    hot_wallet, vasp_slug, entity_name, sweeps = hot_wallet_flows[0]

    sweep_value = sum(t.usd for t in sweeps)
    sweep_ratio = sweep_value / total_out_usd if total_out_usd else 0.0
    if sweep_ratio < MIN_SWEEP_RATIO:
        return None

    inbound = inbound or []
    distinct_payers = len({t.src for t in inbound if t.src != address})

    hold_times: list[float] = []
    if inbound:
        for sweep in sweeps:
            prior = [t.ts for t in inbound if t.ts <= sweep.ts]
            if prior:
                hold_times.append((sweep.ts - max(prior)).total_seconds())

    evidence = ConsolidationEvidence(
        vasp_slug=vasp_slug,
        entity_name=entity_name,
        hot_wallet=hot_wallet,
        sweep_count=len(sweeps),
        sweep_value_usd=sweep_value,
        total_out_usd=total_out_usd,
        sweep_ratio=sweep_ratio,
        distinct_payers=distinct_payers,
        exclusive_destination=len(by_destination) == 1,
        median_hold_seconds=_median(hold_times),
    )

    if not inbound:
        evidence.notes.append(
            "Inbound history was not sampled, so the payer count is a lower bound"
        )
    if distinct_payers < MIN_DISTINCT_PAYERS and evidence.sweep_count < 2:
        evidence.notes.append(
            "Only one payer and one sweep observed - consistent with a personal wallet "
            "topping up an exchange account"
        )

    confidence = _score(evidence)
    if confidence < 0.6:
        return None

    return Candidate(
        entity_name=entity_name,
        entity_type=EntityType.CEX_DEPOSIT,
        method=AttributionMethod.DEPOSIT_CONSOLIDATION,
        confidence=confidence,
        vasp_slug=vasp_slug,
        evidence=evidence.to_dict(),
    )
