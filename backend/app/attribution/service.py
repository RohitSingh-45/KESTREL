"""Runs the four attribution tiers and persists the conclusion.

Tiers run cheapest-first and short-circuit: if an exact label already answers
the question at 0.95 confidence, there is no reason to spend API calls probing
behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.btc_cluster import attribute_via_cluster
from app.attribution.classifier import classify_address
from app.attribution.deposit_heuristic import detect_deposit_address
from app.attribution.features import AddressFeatures, extract_features
from app.attribution.labels import LabelStore, match_labels
from app.attribution.models import Attribution, Candidate
from app.chains.addresses import normalise_address
from app.chains.base import Transfer
from app.chains.resolver import DataRouter
from app.core.enums import AttributionMethod, Chain, Direction, EntityType
from app.db.models import Attribution as AttributionRow
from app.db.models import Vasp
from app.logging_config import get_logger

logger = get_logger(__name__)

# Above this, later tiers cannot add anything worth the API calls.
SHORT_CIRCUIT_CONFIDENCE = 0.95
# A stored conclusion this recent needs no live re-check: the upstream HTTP
# cache (chains/http.py) shares the same TTL, so a fresh re-run of tiers 2-4
# would just replay cached responses and reach the same answer at API cost.
ATTRIBUTION_FRESHNESS_SECONDS = 900
# Sampling caps for behavioural tiers, chosen to stay inside free-tier quotas.
INBOUND_SAMPLE = 60
OUTBOUND_SAMPLE = 60


@dataclass
class AttributionContext:
    """Transfers already fetched by the caller, so tiers do not refetch them."""

    outbound: list[Transfer] | None = None
    inbound: list[Transfer] | None = None
    allow_fetch: bool = True


@dataclass
class AddressObservation:
    """What the inbound sample says about an address, beyond who owns it.

    A forward trace only walks the edges the victim's money took, so it sees a
    fraction of an address's real activity. Tier 2 already pulls the full
    inbound history to test the consolidation pattern, so the traversal reuses
    that sample rather than paying for the same calls twice - without it, a
    collection wallet funded by two hundred victims looks like it has one payer.
    """

    distinct_payers: int = 0
    inbound_count: int = 0
    first_inbound: datetime | None = None
    last_inbound: datetime | None = None
    sampled: bool = False


class AttributionService:
    def __init__(self, session: AsyncSession, router: DataRouter | None = None) -> None:
        self.session = session
        self.router = router or DataRouter(session)
        self.labels = LabelStore(session)
        self._cache: dict[tuple[str, str], Attribution] = {}
        self._observed: dict[tuple[str, str], AddressObservation] = {}
        self.tier_hits: dict[str, int] = {}

    def _record_tier(self, method: AttributionMethod) -> None:
        self.tier_hits[method.value] = self.tier_hits.get(method.value, 0) + 1

    async def prefetch_labels(self, chain: Chain, addresses: list[str]) -> None:
        await self.labels.prefetch(chain, addresses)

    async def _fetch(
        self, chain: Chain, address: str, direction: Direction, limit: int
    ) -> list[Transfer]:
        page = await self.router.get_transfers(chain, address, direction, limit=limit)
        return page.transfers

    async def identify(
        self,
        chain: Chain,
        address: str,
        context: AttributionContext | None = None,
    ) -> Attribution:
        address = normalise_address(address, chain)
        key = (chain.value, address)
        if key in self._cache:
            return self._cache[key]

        context = context or AttributionContext()

        if context.allow_fetch:
            fresh = await self._load_fresh(chain, address)
            if fresh is not None:
                self._cache[key] = fresh
                self.tier_hits["index_reuse"] = self.tier_hits.get("index_reuse", 0) + 1
                await self._ensure_observed(chain, address, key, context)
                return fresh

        result = Attribution(chain=chain, address=address)

        # -- tier 1: exact label match ----------------------------------
        for candidate in await match_labels(self.labels, chain, address):
            result.add(candidate)
            self._record_tier(candidate.method)

        if result.finalise().confidence >= SHORT_CIRCUIT_CONFIDENCE:
            self._cache[key] = result
            # A confident label still short-circuits identification, but
            # typology detection (fan-in, dormant-burst) needs the inbound
            # history regardless of how sure we are who owns the address.
            await self._ensure_observed(chain, address, key, context)
            return result

        outbound = context.outbound
        if outbound is None and context.allow_fetch:
            outbound = await self._fetch(chain, address, Direction.OUT, OUTBOUND_SAMPLE)
        outbound = outbound or []

        # -- tier 2: deposit-address consolidation ----------------------
        inbound = context.inbound
        needs_inbound = bool(outbound)
        if inbound is None and needs_inbound and context.allow_fetch:
            inbound = await self._fetch(chain, address, Direction.IN, INBOUND_SAMPLE)
        inbound = inbound or []
        self._observe(key, inbound, sampled=needs_inbound and context.allow_fetch)

        deposit = await detect_deposit_address(
            self.labels, chain, address, outbound, inbound
        )
        if deposit:
            result.add(deposit)
            self._record_tier(deposit.method)

        # -- tier 3: BTC common-input-ownership -------------------------
        if chain is Chain.BITCOIN:
            cluster = await attribute_via_cluster(
                self.labels, self.session, address, outbound + inbound
            )
            if cluster:
                result.add(cluster)
                self._record_tier(cluster.method)

        if result.finalise().confidence >= SHORT_CIRCUIT_CONFIDENCE:
            self._cache[key] = result
            return result

        # -- tier 4: behavioural classifier -----------------------------
        if inbound or outbound:
            features = extract_features(address, inbound, outbound)
            predicted = classify_address(features)
            if predicted:
                # Do not let the classifier restate what tier 2 already proved.
                already_deposit = any(
                    c.method is AttributionMethod.DEPOSIT_CONSOLIDATION
                    for c in result.candidates
                )
                if not (already_deposit and predicted.entity_type is EntityType.CEX_DEPOSIT):
                    result.add(predicted)
                    self._record_tier(predicted.method)

        result.finalise()
        self._cache[key] = result
        return result

    def _observe(
        self, key: tuple[str, str], inbound: list[Transfer], *, sampled: bool
    ) -> None:
        timestamps = [t.ts for t in inbound]
        self._observed[key] = AddressObservation(
            distinct_payers=len({t.src for t in inbound if t.src != key[1]}),
            inbound_count=len(inbound),
            first_inbound=min(timestamps) if timestamps else None,
            last_inbound=max(timestamps) if timestamps else None,
            sampled=sampled,
        )

    def observation(self, chain: Chain, address: str) -> AddressObservation | None:
        """Inbound facts gathered while attributing, or None if never sampled."""
        return self._observed.get((chain.value, normalise_address(address, chain)))

    async def _ensure_observed(
        self, chain: Chain, address: str, key: tuple[str, str], context: AttributionContext
    ) -> None:
        """Sample inbound history even when identification short-circuited.

        A confident label or a fresh cache hit skips tiers 2-4 entirely, but
        typology detection (fan-in aggregation, dormant-burst) needs an
        address's real inbound picture regardless of how quickly we named it
        - otherwise every labelled or recently-attributed address is a blind
        spot for those patterns.
        """
        if key in self._observed or not context.allow_fetch:
            return
        inbound = context.inbound
        if inbound is None:
            try:
                inbound = await self._fetch(chain, address, Direction.IN, INBOUND_SAMPLE)
            except Exception:
                return
        self._observe(key, inbound, sampled=True)

    async def features_for(
        self, chain: Chain, address: str, inbound: list[Transfer], outbound: list[Transfer]
    ) -> AddressFeatures:
        return extract_features(normalise_address(address, chain), inbound, outbound)

    async def persist(self, attribution: Attribution) -> None:
        """Store each distinct method's conclusion for later lookup and reporting."""
        if not attribution.candidates:
            return
        existing_rows = (
            await self.session.execute(
                select(AttributionRow).where(
                    AttributionRow.chain == attribution.chain,
                    AttributionRow.address == attribution.address,
                )
            )
        ).scalars().all()
        by_method = {str(row.method): row for row in existing_rows}

        for candidate in attribution.candidates:
            row = by_method.get(candidate.method.value)
            if row is None:
                self.session.add(
                    AttributionRow(
                        chain=attribution.chain,
                        address=attribution.address,
                        entity_name=candidate.entity_name,
                        entity_type=candidate.entity_type,
                        vasp_slug=candidate.vasp_slug,
                        method=candidate.method,
                        confidence=candidate.confidence,
                        is_sanctioned=candidate.is_sanctioned,
                        evidence=candidate.evidence,
                    )
                )
            else:
                row.entity_name = candidate.entity_name
                row.entity_type = candidate.entity_type
                row.vasp_slug = candidate.vasp_slug
                row.confidence = candidate.confidence
                row.is_sanctioned = candidate.is_sanctioned
                row.evidence = candidate.evidence

    async def vasp_for(self, slug: str | None) -> Vasp | None:
        if not slug:
            return None
        return (await self.labels.vasps()).get(slug)

    async def _load_fresh(self, chain: Chain, address: str) -> Attribution | None:
        """`load_stored`, but only when the conclusion is within the freshness
        window - otherwise None, so the caller falls through to live tiers."""
        address = normalise_address(address, chain)
        newest = (
            await self.session.execute(
                select(func.max(AttributionRow.updated_at)).where(
                    AttributionRow.chain == chain, AttributionRow.address == address
                )
            )
        ).scalar_one_or_none()
        if newest is None:
            return None
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=UTC)
        if datetime.now(UTC) - newest > timedelta(seconds=ATTRIBUTION_FRESHNESS_SECONDS):
            return None
        return await self.load_stored(chain, address)

    async def load_stored(self, chain: Chain, address: str) -> Attribution | None:
        """Reconstruct a previous conclusion without touching any upstream API."""
        address = normalise_address(address, chain)
        rows = (
            await self.session.execute(
                select(AttributionRow).where(
                    AttributionRow.chain == chain, AttributionRow.address == address
                )
            )
        ).scalars().all()
        if not rows:
            return None
        attribution = Attribution(chain=chain, address=address)
        for row in rows:
            attribution.add(
                Candidate(
                    entity_name=row.entity_name or "Unknown",
                    entity_type=EntityType(row.entity_type),
                    method=AttributionMethod(row.method),
                    confidence=row.confidence,
                    vasp_slug=row.vasp_slug,
                    is_sanctioned=row.is_sanctioned,
                    evidence=row.evidence or {},
                )
            )
        return attribution.finalise()

    def stats(self) -> dict:
        return {
            "label_lookups": self.labels.lookups,
            "label_hits": self.labels.hits,
            "tier_hits": dict(self.tier_hits),
            "addresses_attributed": len(self._cache),
        }
