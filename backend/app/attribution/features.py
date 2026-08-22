"""Behavioural features for the entity classifier and risk scorer.

Everything here is derived from transfer timing, value and counterparty
structure. No labels are used, so the same vector can be computed for a wallet
nobody has ever seen before.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass

from app.chains.base import Transfer

FEATURE_ORDER = [
    "in_degree",
    "out_degree",
    "degree_ratio",
    "tx_count",
    "total_in_usd_log",
    "total_out_usd_log",
    "balance_retention",
    "sweep_ratio",
    "max_out_share",
    "mean_in_usd_log",
    "mean_out_usd_log",
    "value_dispersion",
    "lifetime_hours_log",
    "mean_hold_seconds_log",
    "timing_regularity",
    "burstiness",
    "asset_diversity",
    "reuse_rate",
    "round_amount_share",
    "is_pass_through",
]


@dataclass(slots=True)
class AddressFeatures:
    in_degree: float = 0.0
    out_degree: float = 0.0
    degree_ratio: float = 0.0
    tx_count: float = 0.0
    total_in_usd_log: float = 0.0
    total_out_usd_log: float = 0.0
    balance_retention: float = 0.0
    sweep_ratio: float = 0.0
    max_out_share: float = 0.0
    mean_in_usd_log: float = 0.0
    mean_out_usd_log: float = 0.0
    value_dispersion: float = 0.0
    lifetime_hours_log: float = 0.0
    mean_hold_seconds_log: float = 0.0
    timing_regularity: float = 0.0
    burstiness: float = 0.0
    asset_diversity: float = 0.0
    reuse_rate: float = 0.0
    round_amount_share: float = 0.0
    is_pass_through: float = 0.0

    def to_vector(self) -> list[float]:
        data = asdict(self)
        return [float(data[name]) for name in FEATURE_ORDER]

    def to_dict(self) -> dict[str, float]:
        return {k: round(float(v), 4) for k, v in asdict(self).items()}


def _log1p(value: float) -> float:
    return math.log1p(max(0.0, value))


def _is_round(value: float) -> bool:
    if value <= 0:
        return False
    for magnitude in (1000, 100, 10):
        if value >= magnitude and abs(value % magnitude) < 1e-9:
            return True
    return False


def extract_features(
    address: str, inbound: list[Transfer], outbound: list[Transfer]
) -> AddressFeatures:
    features = AddressFeatures()
    if not inbound and not outbound:
        return features

    in_usd = [t.usd for t in inbound]
    out_usd = [t.usd for t in outbound]
    total_in = sum(in_usd)
    total_out = sum(out_usd)

    features.in_degree = len({t.src for t in inbound})
    features.out_degree = len({t.dst for t in outbound})
    features.degree_ratio = features.in_degree / max(1.0, features.out_degree)
    features.tx_count = len(inbound) + len(outbound)
    features.total_in_usd_log = _log1p(total_in)
    features.total_out_usd_log = _log1p(total_out)

    # How much of what arrived is still here. Deposit addresses and mules keep
    # nothing; savings wallets keep everything.
    features.balance_retention = max(0.0, (total_in - total_out) / total_in) if total_in else 0.0
    features.sweep_ratio = min(1.0, total_out / total_in) if total_in else 0.0

    if outbound and total_out:
        by_destination: dict[str, float] = {}
        for transfer in outbound:
            by_destination[transfer.dst] = by_destination.get(transfer.dst, 0.0) + transfer.usd
        features.max_out_share = max(by_destination.values()) / total_out

    features.mean_in_usd_log = _log1p(statistics.fmean(in_usd)) if in_usd else 0.0
    features.mean_out_usd_log = _log1p(statistics.fmean(out_usd)) if out_usd else 0.0

    all_usd = [v for v in in_usd + out_usd if v > 0]
    if len(all_usd) > 1:
        mean = statistics.fmean(all_usd)
        features.value_dispersion = statistics.pstdev(all_usd) / mean if mean else 0.0

    timestamps = sorted(t.ts for t in inbound + outbound)
    if len(timestamps) > 1:
        lifetime_hours = (timestamps[-1] - timestamps[0]).total_seconds() / 3600
        features.lifetime_hours_log = _log1p(lifetime_hours)

        gaps = [
            (b - a).total_seconds()
            for a, b in zip(timestamps, timestamps[1:], strict=False)
        ]
        positive = [g for g in gaps if g > 0]
        if len(positive) > 1:
            mean_gap = statistics.fmean(positive)
            cv = statistics.pstdev(positive) / mean_gap if mean_gap else 0.0
            # Low variance between transactions suggests automation.
            features.timing_regularity = 1.0 / (1.0 + cv)
            features.burstiness = (cv - 1) / (cv + 1) if (cv + 1) else 0.0

    if inbound and outbound:
        holds: list[float] = []
        inbound_times = sorted(t.ts for t in inbound)
        for transfer in outbound:
            prior = [ts for ts in inbound_times if ts <= transfer.ts]
            if prior:
                holds.append((transfer.ts - max(prior)).total_seconds())
        if holds:
            features.mean_hold_seconds_log = _log1p(statistics.fmean(holds))

    features.asset_diversity = len({t.asset for t in inbound + outbound})

    counterparties = [t.src for t in inbound] + [t.dst for t in outbound]
    if counterparties:
        unique = len(set(counterparties))
        features.reuse_rate = 1.0 - (unique / len(counterparties))

    if all_usd:
        features.round_amount_share = sum(1 for v in all_usd if _is_round(v)) / len(all_usd)

    # Pass-through: money in, money straight back out, nothing retained.
    features.is_pass_through = (
        1.0 if features.sweep_ratio >= 0.9 and features.in_degree >= 1 and outbound else 0.0
    )
    return features
