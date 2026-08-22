"""Proportional taint, a.k.a. the haircut method.

If an address holding 40% of the victim's money sends out 1000 USD in total and
250 USD of that goes to one counterparty, that counterparty receives
0.40 x 0.25 = 10% of the victim's money. Taint is therefore conserved across a
hop (minus whatever is pruned), which is what makes a statement like "87% of the
traced value reached this exchange" defensible in a report.

Counterparties are aggregated before ranking: ten small payments to one address
are one relationship, not ten, and should compete for the fan-out budget as one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.chains.base import Transfer


@dataclass(slots=True)
class Outflow:
    """Everything one address sent to one counterparty, treated as a single edge."""

    dst: str
    transfers: list[Transfer] = field(default_factory=list)
    value_usd: float = 0.0
    amount: Decimal = Decimal("0")

    @property
    def tx_count(self) -> int:
        return len(self.transfers)


@dataclass(slots=True)
class OutflowSet:
    flows: list[Outflow]
    total_usd: float
    weighted_by: str

    @property
    def counterparties(self) -> int:
        return len(self.flows)

    def share_of(self, flow: Outflow) -> float:
        if self.total_usd <= 0:
            return 0.0
        return flow.value_usd / self.total_usd


def transfer_weight(transfer: Transfer) -> float:
    """Best available value for a transfer, in USD where we know it."""
    if transfer.amount_usd is not None and transfer.amount_usd > 0:
        return float(transfer.amount_usd)
    return 0.0


def aggregate_outflows(transfers: list[Transfer], source: str) -> OutflowSet:
    """Group a node's outbound transfers by counterparty and value them.

    When no USD pricing is available at all - which happens for exotic tokens on
    free-tier explorers - fall back to native amounts so that relative shares are
    still meaningful, and record which basis was used.
    """
    outbound = [t for t in transfers if t.src == source and t.dst and t.dst != source]
    if not outbound:
        return OutflowSet(flows=[], total_usd=0.0, weighted_by="usd")

    priced_total = sum(transfer_weight(t) for t in outbound)
    weighted_by = "usd" if priced_total > 0 else "native_amount"

    grouped: dict[str, Outflow] = {}
    for transfer in outbound:
        flow = grouped.get(transfer.dst)
        if flow is None:
            flow = Outflow(dst=transfer.dst)
            grouped[transfer.dst] = flow
        flow.transfers.append(transfer)
        flow.amount += transfer.amount
        flow.value_usd += (
            transfer_weight(transfer)
            if weighted_by == "usd"
            else float(transfer.amount)
        )

    flows = sorted(grouped.values(), key=lambda f: f.value_usd, reverse=True)
    for flow in flows:
        flow.transfers.sort(key=lambda t: transfer_weight(t), reverse=True)
    return OutflowSet(
        flows=flows,
        total_usd=sum(f.value_usd for f in flows),
        weighted_by=weighted_by,
    )


def haircut(parent_taint: float, flow: Outflow, outflows: OutflowSet) -> float:
    """Fraction of the victim's funds carried by one counterparty edge."""
    return parent_taint * outflows.share_of(flow)


def split_transfer_taint(flow: Outflow, flow_taint: float) -> dict[str, float]:
    """Divide an aggregated edge's taint back across its individual transactions."""
    if not flow.transfers:
        return {}
    total = sum(max(transfer_weight(t), 0.0) for t in flow.transfers)
    if total <= 0:
        even = flow_taint / len(flow.transfers)
        return {t.tx_hash: even for t in flow.transfers}
    return {
        t.tx_hash: flow_taint * (transfer_weight(t) / total) for t in flow.transfers
    }
