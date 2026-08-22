"""Loads reference data and the synthetic dataset into the database."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SEEDS_DIR
from app.core.enums import (
    ILLICIT_ENTITY_TYPES,
    CasePriority,
    CaseStatus,
    Chain,
    EntityType,
)
from app.db.models import (
    Address,
    AddressLabel,
    Attribution,
    Case,
    CaseAddress,
    ContractRegistryEntry,
    SyntheticGroundTruth,
    SyntheticTransfer,
    Vasp,
)
from app.logging_config import get_logger
from app.seeds.synthetic import Scenario, build_scenarios

logger = get_logger(__name__)

CURATED_SOURCE = "curated_seed"
SYNTHETIC_SOURCE = "synthetic_seed"
REGISTRY_SOURCE = "contract_registry"


def _load_json(name: str) -> dict[str, Any]:
    path = Path(SEEDS_DIR) / name
    if not path.exists():
        raise FileNotFoundError(f"Seed file missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------


async def seed_vasps(session: AsyncSession) -> int:
    payload = _load_json("vasp_directory.json")
    existing = {
        slug for (slug,) in (await session.execute(select(Vasp.slug))).all()
    }
    added = 0
    for entry in payload["vasps"]:
        if entry["slug"] in existing:
            continue
        session.add(
            Vasp(
                slug=entry["slug"],
                name=entry["name"],
                legal_name=entry.get("legal_name"),
                jurisdiction=entry.get("jurisdiction", "Unknown"),
                country_code=entry.get("country_code"),
                is_indian_entity=entry.get("is_indian_entity", False),
                fiu_ind_registered=entry.get("fiu_ind_registered", False),
                fiu_ind_reg_no=entry.get("fiu_ind_reg_no"),
                compliance_email=entry.get("compliance_email"),
                grievance_officer=entry.get("grievance_officer"),
                law_enforcement_portal=entry.get("law_enforcement_portal"),
                response_sla_hours=entry.get("response_sla_hours", 72),
                accepts_emergency_freeze=entry.get("accepts_emergency_freeze", False),
                notes=entry.get("notes"),
                aliases=entry.get("aliases"),
            )
        )
        added += 1
    await session.flush()
    return added


async def _existing_label_keys(session: AsyncSession, source: str) -> set[tuple[str, str, str]]:
    rows = await session.execute(
        select(AddressLabel.chain, AddressLabel.address, AddressLabel.label).where(
            AddressLabel.source == source
        )
    )
    return {(str(c), a, lbl) for c, a, lbl in rows.all()}


async def seed_curated_labels(session: AsyncSession) -> int:
    payload = _load_json("curated_labels.json")
    seen = await _existing_label_keys(session, CURATED_SOURCE)
    added = 0
    for entry in payload["labels"]:
        chain = Chain(entry["chain"])
        address = entry["address"].lower() if chain.is_evm else entry["address"]
        key = (chain.value, address, entry["label"])
        if key in seen:
            continue
        seen.add(key)
        session.add(
            AddressLabel(
                chain=chain,
                address=address,
                label=entry["label"],
                entity_name=entry.get("entity_name"),
                entity_type=EntityType(entry.get("entity_type", "unknown")),
                category=entry.get("category"),
                source=CURATED_SOURCE,
                confidence=entry.get("confidence", 0.97),
                is_sanctioned=entry.get("is_sanctioned", False),
                vasp_slug=entry.get("vasp_slug"),
            )
        )
        added += 1
    await session.flush()
    return added


async def seed_contract_registry(session: AsyncSession) -> int:
    payload = _load_json("contract_registry.json")
    existing = {
        (str(c), a)
        for c, a in (
            await session.execute(
                select(ContractRegistryEntry.chain, ContractRegistryEntry.address)
            )
        ).all()
    }
    label_keys = await _existing_label_keys(session, REGISTRY_SOURCE)
    added = 0
    for entry in payload["contracts"]:
        chain = Chain(entry["chain"])
        address = entry["address"].lower() if chain.is_evm else entry["address"]
        entity_type = EntityType(entry["entity_type"])

        if (chain.value, address) not in existing:
            session.add(
                ContractRegistryEntry(
                    chain=chain,
                    address=address,
                    name=entry["name"],
                    protocol=entry["protocol"],
                    entity_type=entity_type,
                    supported_chains=entry.get("supported_chains"),
                    is_sanctioned=entry.get("is_sanctioned", False),
                    details=entry.get("details"),
                )
            )
            added += 1

        # Mirror into the label store so tier-1 attribution sees contracts too.
        key = (chain.value, address, entry["name"])
        if key not in label_keys:
            label_keys.add(key)
            session.add(
                AddressLabel(
                    chain=chain,
                    address=address,
                    label=entry["name"],
                    entity_name=entry["protocol"],
                    entity_type=entity_type,
                    category=entity_type.value,
                    source=REGISTRY_SOURCE,
                    confidence=0.99,
                    is_sanctioned=entry.get("is_sanctioned", False),
                )
            )
    await session.flush()
    return added


# ---------------------------------------------------------------------------
# Synthetic dataset
# ---------------------------------------------------------------------------


async def clear_synthetic(session: AsyncSession) -> None:
    await session.execute(delete(SyntheticTransfer))
    await session.execute(delete(SyntheticGroundTruth))
    await session.execute(delete(AddressLabel).where(AddressLabel.source == SYNTHETIC_SOURCE))
    await session.flush()


async def seed_synthetic(session: AsyncSession, scenarios: list[Scenario]) -> dict[str, int]:
    transfers = 0
    labels = 0
    seen_tx: set[tuple[str, str, int]] = set()
    seen_labels: set[tuple[str, str, str]] = set()

    for scenario in scenarios:
        for t in scenario.transfers:
            key = (t.chain.value, t.tx_hash, t.trace_index)
            if key in seen_tx:
                continue
            seen_tx.add(key)
            session.add(
                SyntheticTransfer(
                    scenario=t.scenario,
                    chain=t.chain,
                    tx_hash=t.tx_hash,
                    trace_index=t.trace_index,
                    block_number=t.block_number,
                    ts=t.ts,
                    src_address=t.src_address,
                    dst_address=t.dst_address,
                    asset=t.asset,
                    amount=t.amount,
                    amount_usd=t.amount_usd,
                    is_token=t.is_token,
                    token_contract=t.token_contract,
                    input_group=t.input_group,
                )
            )
            transfers += 1

        for label in scenario.labels:
            key = (label.chain.value, label.address, label.label)
            if key in seen_labels:
                continue
            seen_labels.add(key)
            session.add(
                AddressLabel(
                    chain=label.chain,
                    address=label.address,
                    label=label.label,
                    entity_name=label.entity_name,
                    entity_type=label.entity_type,
                    category=label.entity_type.value,
                    source=SYNTHETIC_SOURCE,
                    confidence=label.confidence,
                    is_sanctioned=label.is_sanctioned,
                    vasp_slug=label.vasp_slug,
                )
            )
            labels += 1

        session.add(
            SyntheticGroundTruth(
                scenario=scenario.key,
                chain=scenario.chain,
                seed_address=scenario.seed_address,
                expected_vasp_slug=scenario.expected_vasp_slug,
                expected_deposit_address=scenario.expected_deposit_address,
                expected_hops=scenario.expected_hops,
                fraud_category=scenario.fraud_category,
                expected_typologies=[t.value for t in scenario.expected_typologies],
                notes=scenario.notes,
            )
        )

    await session.flush()
    return {"transfers": transfers, "labels": labels, "scenarios": len(scenarios)}


async def seed_demo_cases(session: AsyncSession, scenarios: list[Scenario]) -> int:
    existing = {
        num for (num,) in (await session.execute(select(Case.case_number))).all()
    }
    created = 0
    for index, scenario in enumerate(scenarios, start=1):
        case_number = f"CW/2026/{index:04d}"
        if case_number in existing:
            continue
        case = Case(
            case_number=case_number,
            ncrp_ack_no=f"NCRP{31_000_000 + index * 7_331}",
            title=scenario.title,
            description=scenario.narrative,
            fraud_category=scenario.fraud_category,
            status=CaseStatus.OPEN,
            priority=(
                CasePriority.URGENT
                if scenario.loss_amount_inr >= 4_000_000
                else CasePriority.HIGH
                if scenario.loss_amount_inr >= 1_000_000
                else CasePriority.NORMAL
            ),
            victim_name=scenario.victim_name,
            loss_amount_inr=scenario.loss_amount_inr,
            incident_date=scenario.incident_at,
            reported_date=scenario.incident_at + timedelta(days=2),
            police_station=scenario.police_station,
            state=scenario.victim_state,
            intake_source="seed",
            details={"scenario": scenario.key, "synthetic": True},
        )
        case.addresses.append(
            CaseAddress(
                chain=scenario.chain,
                address=scenario.seed_address,
                role="reported",
                amount_sent_inr=scenario.loss_amount_inr,
                note="Wallet address reported by the complainant",
            )
        )
        session.add(case)
        created += 1
    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Address profile priming
# ---------------------------------------------------------------------------


async def prime_known_addresses(session: AsyncSession) -> int:
    """Give labelled addresses a baseline profile row and attribution.

    Without this the wallet-lookup page would show nothing for a known
    exchange hot wallet until someone happened to trace through it.
    """
    from app.core.enums import AttributionMethod, RiskLevel

    rows = (
        await session.execute(
            select(AddressLabel).where(AddressLabel.entity_type != EntityType.UNKNOWN)
        )
    ).scalars().all()

    existing_addr = {
        (str(c), a) for c, a in (await session.execute(select(Address.chain, Address.address))).all()
    }
    existing_attr = {
        (str(c), a, str(m))
        for c, a, m in (
            await session.execute(
                select(Attribution.chain, Attribution.address, Attribution.method)
            )
        ).all()
    }

    added = 0
    for label in rows:
        key = (str(label.chain), label.address)
        if key not in existing_addr:
            existing_addr.add(key)
            risk = 95.0 if label.is_sanctioned else (
                75.0 if label.entity_type in ILLICIT_ENTITY_TYPES else 10.0
            )
            session.add(
                Address(
                    chain=label.chain,
                    address=label.address,
                    entity_type=label.entity_type,
                    entity_name=label.entity_name,
                    risk_score=risk,
                    risk_level=RiskLevel.from_score(risk),
                )
            )
            added += 1

        method = (
            AttributionMethod.SANCTIONS_LIST
            if label.is_sanctioned
            else AttributionMethod.DIRECT_LABEL
        )
        attr_key = (str(label.chain), label.address, method.value)
        if attr_key not in existing_attr:
            existing_attr.add(attr_key)
            session.add(
                Attribution(
                    chain=label.chain,
                    address=label.address,
                    entity_name=label.entity_name,
                    entity_type=label.entity_type,
                    vasp_slug=label.vasp_slug,
                    method=method,
                    confidence=label.confidence,
                    is_sanctioned=label.is_sanctioned,
                    evidence={"source": label.source, "label": label.label},
                )
            )
    await session.flush()
    return added


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


async def is_seeded(session: AsyncSession) -> bool:
    count = await session.scalar(select(func.count()).select_from(SyntheticGroundTruth))
    return bool(count)


async def seed_all(
    session: AsyncSession, *, reset_synthetic: bool = False, with_cases: bool = True
) -> dict[str, int]:
    stats: dict[str, int] = {}
    stats["vasps"] = await seed_vasps(session)
    stats["curated_labels"] = await seed_curated_labels(session)
    stats["contracts"] = await seed_contract_registry(session)

    if reset_synthetic:
        await clear_synthetic(session)

    scenarios = build_scenarios()
    if reset_synthetic or not await is_seeded(session):
        stats.update(await seed_synthetic(session, scenarios))
    else:
        stats["transfers"] = 0
        stats["labels"] = 0
        stats["scenarios"] = 0

    if with_cases:
        stats["cases"] = await seed_demo_cases(session, scenarios)

    stats["primed_addresses"] = await prime_known_addresses(session)
    logger.info("seed_complete", **stats)
    return stats
