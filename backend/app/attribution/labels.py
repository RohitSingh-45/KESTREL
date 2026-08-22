"""Tier 1: exact-match lookup against the label store."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.models import Candidate
from app.chains.addresses import normalise_address
from app.core.enums import AttributionMethod, Chain, EntityType
from app.db.models import AddressLabel, Vasp

# Datasets differ in trustworthiness; a curated hot-wallet list is worth more
# than a heuristically derived bulk label.
SOURCE_WEIGHT: dict[str, float] = {
    "ofac-sdn": 1.0,
    "curated_seed": 0.98,
    "synthetic_seed": 0.98,
    "contract_registry": 0.98,
    "eth-labels": 0.93,
}
DEFAULT_SOURCE_WEIGHT = 0.85


class LabelStore:
    """Batched, cached label lookups for one trace run."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._cache: dict[tuple[str, str], list[AddressLabel]] = {}
        self._vasps: dict[str, Vasp] | None = None
        self.lookups = 0
        self.hits = 0

    async def vasps(self) -> dict[str, Vasp]:
        if self._vasps is None:
            rows = (await self.session.execute(select(Vasp))).scalars().all()
            self._vasps = {v.slug: v for v in rows}
        return self._vasps

    async def prefetch(self, chain: Chain, addresses: list[str]) -> None:
        """Load labels for a whole hop in one query instead of N."""
        pending = [
            normalise_address(a, chain)
            for a in addresses
            if (chain.value, normalise_address(a, chain)) not in self._cache
        ]
        if not pending:
            return
        rows = (
            await self.session.execute(
                select(AddressLabel).where(
                    AddressLabel.chain == chain, AddressLabel.address.in_(pending)
                )
            )
        ).scalars().all()

        for address in pending:
            self._cache[(chain.value, address)] = []
        for row in rows:
            self._cache.setdefault((chain.value, row.address), []).append(row)

    async def get(self, chain: Chain, address: str) -> list[AddressLabel]:
        key = (chain.value, normalise_address(address, chain))
        self.lookups += 1
        if key not in self._cache:
            await self.prefetch(chain, [address])
        labels = self._cache.get(key, [])
        if labels:
            self.hits += 1
        return labels

    async def is_hot_wallet(self, chain: Chain, address: str) -> tuple[bool, str | None, str | None]:
        """(is a known exchange hot wallet, vasp slug, entity name)."""
        for label in await self.get(chain, address):
            if label.entity_type in (
                EntityType.CEX_HOT_WALLET,
                EntityType.CEX_COLD_WALLET,
            ) and label.vasp_slug:
                return True, label.vasp_slug, label.entity_name
        return False, None, None


def _candidate_from_label(label: AddressLabel) -> Candidate:
    weight = SOURCE_WEIGHT.get(label.source, DEFAULT_SOURCE_WEIGHT)
    method = (
        AttributionMethod.SANCTIONS_LIST
        if label.is_sanctioned
        else AttributionMethod.CONTRACT_REGISTRY
        if label.source == "contract_registry"
        else AttributionMethod.DIRECT_LABEL
    )
    return Candidate(
        entity_name=label.entity_name or label.label,
        entity_type=EntityType(label.entity_type),
        method=method,
        confidence=label.confidence * weight,
        vasp_slug=label.vasp_slug,
        is_sanctioned=label.is_sanctioned,
        evidence={
            "source": label.source,
            "label": label.label,
            "category": label.category,
            "matched_on": "exact address",
        },
    )


async def match_labels(store: LabelStore, chain: Chain, address: str) -> list[Candidate]:
    """Every label hypothesis for an address, deduplicated by entity."""
    labels = await store.get(chain, address)
    if not labels:
        return []

    best_per_entity: dict[tuple[str, str], Candidate] = {}
    for label in labels:
        candidate = _candidate_from_label(label)
        key = (candidate.entity_name.lower(), candidate.entity_type.value)
        existing = best_per_entity.get(key)
        if existing is None or candidate.confidence > existing.confidence:
            best_per_entity[key] = candidate
    return list(best_per_entity.values())
