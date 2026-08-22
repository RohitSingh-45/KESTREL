"""Train the tier-4 behavioural entity classifier.

    python -m ml.train_entity_classifier
    python -m ml.train_entity_classifier --augment 4000 --report

Training set:

  * Real labelled addresses from the database - exchange hot wallets, deposit
    addresses named in the synthetic ground truth, mixers, bridges, P2P desks
    and ordinary victim/payer wallets - with features computed from their
    actual transfer history.
  * Parametric augmentation. The labelled pool is only a few dozen addresses,
    far too few to fit a gradient-boosted model, so additional profiles are
    sampled from per-class behavioural distributions.

The augmentation is why this model is treated as a hint and capped at 0.75
confidence: it learns the shape of each behaviour class, not a population of
real-world wallets. Ingest a large labelled corpus and retrain to do better.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import Counter
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.attribution.classifier import CLASS_LABELS, MODEL_PATH
from app.attribution.features import FEATURE_ORDER, AddressFeatures, extract_features
from app.chains.base import Transfer
from app.core.enums import Chain, EntityType
from app.db.models import AddressLabel, SyntheticGroundTruth, SyntheticTransfer
from app.db.session import dispose_engine, session_scope
from app.logging_config import configure_logging

ENTITY_TO_CLASS: dict[EntityType, str] = {
    EntityType.CEX_DEPOSIT: "cex_deposit",
    EntityType.MIXER: "mixer",
    EntityType.BRIDGE: "bridge",
    EntityType.P2P_MERCHANT: "p2p_merchant",
    EntityType.PERSONAL_WALLET: "personal",
    EntityType.VICTIM: "personal",
    EntityType.TOKEN_CONTRACT: "contract",
    EntityType.DEFI_CONTRACT: "contract",
}


def _to_transfer(row: SyntheticTransfer) -> Transfer:
    from decimal import Decimal

    return Transfer(
        chain=Chain(row.chain),
        tx_hash=row.tx_hash,
        trace_index=row.trace_index,
        block_number=row.block_number,
        ts=row.ts,
        src=row.src_address,
        dst=row.dst_address,
        asset=row.asset,
        amount=Decimal(str(row.amount)),
        amount_usd=row.amount_usd,
        is_token=row.is_token,
    )


async def collect_real_examples() -> list[tuple[list[float], str]]:
    """Feature vectors for every address whose class we actually know."""
    examples: list[tuple[list[float], str]] = []

    async with session_scope() as session:
        transfers = list((await session.execute(select(SyntheticTransfer))).scalars().all())
        labels = list((await session.execute(select(AddressLabel))).scalars().all())
        truths = list((await session.execute(select(SyntheticGroundTruth))).scalars().all())

    by_address: dict[tuple[str, str], dict[str, list[Transfer]]] = {}
    for row in transfers:
        record = _to_transfer(row)
        for address, direction in ((row.src_address, "out"), (row.dst_address, "in")):
            bucket = by_address.setdefault(
                (row.chain, address), {"in": [], "out": []}
            )
            bucket[direction].append(record)

    assigned: dict[tuple[str, str], str] = {}
    for truth in truths:
        assigned[(str(truth.chain), truth.expected_deposit_address)] = "cex_deposit"
    for label in labels:
        klass = ENTITY_TO_CLASS.get(EntityType(label.entity_type))
        if klass:
            assigned.setdefault((str(label.chain), label.address), klass)

    labelled_addresses = set(assigned)
    for key, klass in assigned.items():
        bucket = by_address.get(key)
        if not bucket:
            continue
        features = extract_features(key[1], bucket["in"], bucket["out"])
        examples.append((features.to_vector(), klass))

    # Everything unlabelled in the seeded graph is an ordinary wallet: victims,
    # co-victims and background payers.
    ordinary = 0
    for key, bucket in by_address.items():
        if key in labelled_addresses or ordinary >= 220:
            continue
        if len(bucket["in"]) + len(bucket["out"]) < 1:
            continue
        features = extract_features(key[1], bucket["in"], bucket["out"])
        examples.append((features.to_vector(), "personal"))
        ordinary += 1

    return examples


# Per-class behavioural ranges used to sample augmentation examples.
PROFILES: dict[str, dict[str, tuple[float, float]]] = {
    "cex_deposit": {
        "in_degree": (2, 25),
        "out_degree": (1, 2),
        "sweep_ratio": (0.9, 1.0),
        "balance_retention": (0.0, 0.05),
        "max_out_share": (0.85, 1.0),
        "value_dispersion": (0.3, 1.6),
        "timing_regularity": (0.2, 0.7),
        "reuse_rate": (0.0, 0.25),
        "asset_diversity": (1, 2),
        "round_amount_share": (0.0, 0.4),
    },
    "mixer": {
        "in_degree": (12, 200),
        "out_degree": (12, 200),
        "sweep_ratio": (0.7, 1.0),
        "balance_retention": (0.0, 0.3),
        "max_out_share": (0.02, 0.25),
        "value_dispersion": (0.0, 0.3),
        "timing_regularity": (0.3, 0.8),
        "reuse_rate": (0.0, 0.2),
        "asset_diversity": (1, 2),
        "round_amount_share": (0.6, 1.0),
    },
    "bridge": {
        "in_degree": (20, 400),
        "out_degree": (20, 400),
        "sweep_ratio": (0.85, 1.0),
        "balance_retention": (0.0, 0.15),
        "max_out_share": (0.05, 0.4),
        "value_dispersion": (0.8, 3.0),
        "timing_regularity": (0.5, 0.95),
        "reuse_rate": (0.1, 0.5),
        "asset_diversity": (3, 9),
        "round_amount_share": (0.0, 0.25),
    },
    "p2p_merchant": {
        "in_degree": (8, 120),
        "out_degree": (8, 120),
        "sweep_ratio": (0.5, 0.9),
        "balance_retention": (0.15, 0.55),
        "max_out_share": (0.05, 0.3),
        "value_dispersion": (0.6, 2.2),
        "timing_regularity": (0.2, 0.6),
        "reuse_rate": (0.05, 0.35),
        "asset_diversity": (1, 3),
        "round_amount_share": (0.2, 0.7),
    },
    "personal": {
        "in_degree": (1, 6),
        "out_degree": (1, 6),
        "sweep_ratio": (0.1, 0.85),
        "balance_retention": (0.1, 0.9),
        "max_out_share": (0.4, 1.0),
        "value_dispersion": (0.2, 1.5),
        "timing_regularity": (0.0, 0.45),
        "reuse_rate": (0.0, 0.6),
        "asset_diversity": (1, 3),
        "round_amount_share": (0.1, 0.7),
    },
    "contract": {
        "in_degree": (50, 2000),
        "out_degree": (1, 40),
        "sweep_ratio": (0.0, 0.5),
        "balance_retention": (0.4, 1.0),
        "max_out_share": (0.2, 0.9),
        "value_dispersion": (1.0, 4.0),
        "timing_regularity": (0.4, 0.9),
        "reuse_rate": (0.4, 0.9),
        "asset_diversity": (1, 12),
        "round_amount_share": (0.0, 0.3),
    },
}


def sample_profile(klass: str, rng: random.Random) -> list[float]:
    spec = PROFILES[klass]

    def pick(name: str, default: tuple[float, float]) -> float:
        low, high = spec.get(name, default)
        return rng.uniform(low, high)

    in_degree = pick("in_degree", (1, 10))
    out_degree = pick("out_degree", (1, 10))
    tx_count = (in_degree + out_degree) * rng.uniform(1.0, 2.5)
    total_in = rng.uniform(500, 400_000)
    sweep_ratio = pick("sweep_ratio", (0.2, 1.0))
    total_out = total_in * sweep_ratio

    features = AddressFeatures(
        in_degree=in_degree,
        out_degree=out_degree,
        degree_ratio=in_degree / max(1.0, out_degree),
        tx_count=tx_count,
        total_in_usd_log=np.log1p(total_in),
        total_out_usd_log=np.log1p(total_out),
        balance_retention=pick("balance_retention", (0.0, 1.0)),
        sweep_ratio=sweep_ratio,
        max_out_share=pick("max_out_share", (0.1, 1.0)),
        mean_in_usd_log=np.log1p(total_in / max(1.0, in_degree)),
        mean_out_usd_log=np.log1p(total_out / max(1.0, out_degree)),
        value_dispersion=pick("value_dispersion", (0.1, 2.0)),
        lifetime_hours_log=np.log1p(rng.uniform(0.2, 20_000)),
        mean_hold_seconds_log=np.log1p(rng.uniform(30, 900_000)),
        timing_regularity=pick("timing_regularity", (0.0, 1.0)),
        burstiness=rng.uniform(-0.8, 0.9),
        asset_diversity=pick("asset_diversity", (1, 4)),
        reuse_rate=pick("reuse_rate", (0.0, 0.8)),
        round_amount_share=pick("round_amount_share", (0.0, 1.0)),
        is_pass_through=1.0 if sweep_ratio >= 0.9 else 0.0,
    )
    return features.to_vector()


def build_augmented(count: int, seed: int = 20260614) -> list[tuple[list[float], str]]:
    rng = random.Random(seed)
    per_class = max(1, count // len(CLASS_LABELS))
    return [
        (sample_profile(klass, rng), klass)
        for klass in CLASS_LABELS
        for _ in range(per_class)
    ]


async def main(args: argparse.Namespace) -> int:
    configure_logging()
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import classification_report
    from sklearn.model_selection import train_test_split

    try:
        real = await collect_real_examples()
    finally:
        await dispose_engine()

    augmented = build_augmented(args.augment)
    dataset = real + augmented
    if not dataset:
        print("No training data available. Run `python -m scripts.seed_all` first.")
        return 1

    X = np.array([vector for vector, _ in dataset], dtype=float)
    y = np.array([label for _, label in dataset])

    print(f"Real labelled addresses : {len(real)}")
    print(f"Augmented profiles      : {len(augmented)}")
    print(f"Class balance           : {dict(Counter(y))}")

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = HistGradientBoostingClassifier(
        max_iter=args.max_iter,
        learning_rate=0.08,
        max_depth=6,
        l2_regularization=1.0,
        random_state=42,
    )
    model.fit(X_train, y_train)

    accuracy = model.score(X_test, y_test)
    print(f"\nHold-out accuracy       : {accuracy:.3f}")
    if args.report:
        print("\n" + classification_report(y_test, model.predict(X_test), zero_division=0))

    # Accuracy on the real addresses alone is the number that actually matters.
    if real:
        X_real = np.array([v for v, _ in real], dtype=float)
        y_real = np.array([label for _, label in real])
        print(f"Accuracy on real labels : {model.score(X_real, y_real):.3f}")

    output = Path(args.output or MODEL_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    import joblib

    joblib.dump(model, output)
    print(f"\nSaved model to {output}")
    print(f"Features ({len(FEATURE_ORDER)}): {', '.join(FEATURE_ORDER)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train the entity classifier")
    parser.add_argument("--augment", type=int, default=3600, help="parametric samples to add")
    parser.add_argument("--max-iter", type=int, default=250)
    parser.add_argument("--output", help="model output path")
    parser.add_argument("--report", action="store_true", help="print a per-class report")
    sys.exit(asyncio.run(main(parser.parse_args())))
