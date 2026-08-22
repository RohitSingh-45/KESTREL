"""Picks the adapter for a request and implements the hybrid fallback.

DATA_MODE decides the policy:
  live      - explorer APIs only; failures surface as errors
  synthetic - the seeded dataset only; no network at all
  hybrid    - try live, and on failure, rate limiting or an empty result, fall
              back to the seeded dataset

Every response records which source actually answered, so the UI can badge a
result rather than silently presenting demo data as live intelligence.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.addresses import resolve_chain
from app.chains.base import AddressSummary, ChainAdapter, TransferPage
from app.chains.bitcoin import BitcoinAdapter
from app.chains.evm import EvmAdapter
from app.chains.synthetic import SyntheticAdapter
from app.chains.tron import TronAdapter
from app.config import settings
from app.core.enums import Chain, DataSource, Direction
from app.core.errors import UnsupportedChainError, UpstreamError
from app.core.runtime import get_data_mode
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class ResolvedAddress:
    address: str
    chain: Chain
    ambiguous: bool
    candidates: list[Chain]
    format: str


class ChainRegistry:
    """Owns the live adapter instances (and their connection pools)."""

    def __init__(self) -> None:
        self._live: list[ChainAdapter] = [EvmAdapter(), TronAdapter(), BitcoinAdapter()]

    def live_adapter_for(self, chain: Chain) -> ChainAdapter | None:
        for adapter in self._live:
            if chain in adapter.supported_chains:
                return adapter
        return None

    @property
    def live_adapters(self) -> list[ChainAdapter]:
        return list(self._live)

    async def close(self) -> None:
        for adapter in self._live:
            await adapter.close()


registry = ChainRegistry()


def resolve(address: str, chain_hint: Chain | str | None = None) -> ResolvedAddress:
    normalised, chain, info = resolve_chain(address, chain_hint)
    return ResolvedAddress(
        address=normalised,
        chain=chain,
        ambiguous=info.ambiguous and chain_hint is None,
        candidates=info.candidates,
        format=info.format,
    )


class DataRouter:
    """Serves transfer and summary requests according to DATA_MODE."""

    def __init__(self, session: AsyncSession | None = None, mode: str | None = None) -> None:
        self.mode = mode or get_data_mode()
        self.synthetic = SyntheticAdapter(session)
        self.fallbacks_used = 0
        self.live_calls = 0
        self.synthetic_calls = 0
        self.notes: list[str] = []

    def _note(self, message: str) -> None:
        if message not in self.notes:
            self.notes.append(message)

    async def _seeded(self, chain: Chain, address: str) -> bool:
        """In hybrid mode, addresses from the demo corpus skip the live lookup.

        Seeded addresses are synthetic and will never exist on a real chain, so
        calling an explorer for them burns free-tier quota to learn nothing and
        adds seconds of latency and retries to every demo trace.
        """
        if self.mode != "hybrid":
            return False
        if not await self.synthetic.knows(chain, address):
            return False
        self._note(
            f"{chain.display_name}: address belongs to the seeded demo dataset, "
            "served without a live lookup"
        )
        return True

    def _live_for(self, chain: Chain) -> ChainAdapter | None:
        adapter = registry.live_adapter_for(chain)
        if adapter is None:
            return None
        if not adapter.is_configured():
            self._note(
                f"{adapter.name} is not configured (missing API key); "
                f"{chain.display_name} served from the seeded dataset"
            )
            return None
        return adapter

    async def get_transfers(
        self,
        chain: Chain,
        address: str,
        direction: Direction,
        *,
        limit: int = 200,
        cursor: str | None = None,
    ) -> TransferPage:
        if self.mode == "synthetic" or await self._seeded(chain, address):
            self.synthetic_calls += 1
            return await self.synthetic.get_transfers(
                chain, address, direction, limit=limit, cursor=cursor
            )

        adapter = self._live_for(chain)
        if adapter is None:
            if self.mode == "live":
                raise UnsupportedChainError(
                    f"No configured live provider for {chain.display_name}"
                )
            self.synthetic_calls += 1
            self.fallbacks_used += 1
            return await self.synthetic.get_transfers(
                chain, address, direction, limit=limit, cursor=cursor
            )

        try:
            self.live_calls += 1
            page = await adapter.get_transfers(
                chain, address, direction, limit=limit, cursor=cursor
            )
        except (UpstreamError, UnsupportedChainError) as exc:
            if self.mode == "live":
                raise
            logger.warning(
                "live_fetch_failed_falling_back",
                chain=chain.value,
                address=address[:16],
                error=str(exc)[:160],
            )
            self._note(f"{chain.display_name}: live lookup failed, used the seeded dataset")
            self.fallbacks_used += 1
            self.synthetic_calls += 1
            return await self.synthetic.get_transfers(
                chain, address, direction, limit=limit, cursor=cursor
            )

        # A live provider that returns nothing is indistinguishable from an
        # unknown address, so in hybrid mode check whether the seed knows it.
        if not page.transfers and self.mode == "hybrid":
            fallback = await self.synthetic.get_transfers(
                chain, address, direction, limit=limit, cursor=cursor
            )
            if fallback.transfers:
                self._note(
                    f"{chain.display_name}: address is unknown on-chain but present in "
                    "the seeded dataset"
                )
                self.fallbacks_used += 1
                self.synthetic_calls += 1
                return fallback

        return page

    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary:
        if self.mode == "synthetic" or await self._seeded(chain, address):
            self.synthetic_calls += 1
            return await self.synthetic.get_address_summary(chain, address)

        adapter = self._live_for(chain)
        if adapter is None:
            if self.mode == "live":
                raise UnsupportedChainError(
                    f"No configured live provider for {chain.display_name}"
                )
            self.fallbacks_used += 1
            self.synthetic_calls += 1
            return await self.synthetic.get_address_summary(chain, address)

        try:
            self.live_calls += 1
            summary = await adapter.get_address_summary(chain, address)
        except (UpstreamError, UnsupportedChainError):
            if self.mode == "live":
                raise
            self.fallbacks_used += 1
            self.synthetic_calls += 1
            return await self.synthetic.get_address_summary(chain, address)

        if not summary.exists and self.mode == "hybrid":
            fallback = await self.synthetic.get_address_summary(chain, address)
            if fallback.exists:
                self.fallbacks_used += 1
                self.synthetic_calls += 1
                return fallback
        return summary

    @property
    def effective_source(self) -> DataSource:
        if self.live_calls and self.synthetic_calls:
            return DataSource.MIXED
        if self.synthetic_calls:
            return DataSource.SYNTHETIC
        if self.live_calls:
            return DataSource.LIVE
        return DataSource.SYNTHETIC if self.mode == "synthetic" else DataSource.LIVE

    def stats(self) -> dict:
        return {
            "mode": self.mode,
            "live_calls": self.live_calls,
            "synthetic_calls": self.synthetic_calls,
            "fallbacks_used": self.fallbacks_used,
            "effective_source": self.effective_source.value,
            "notes": list(self.notes),
        }


@dataclass
class ChainProbe:
    """Which of an address's candidate chains it is actually active on."""

    chain: Chain
    activity: dict[str, int]
    probed: bool

    @property
    def found_activity(self) -> bool:
        return any(count > 0 for count in self.activity.values())

    @property
    def active_chains(self) -> list[str]:
        return [chain for chain, count in self.activity.items() if count > 0]


async def detect_active_chain(
    router: DataRouter, resolved: ResolvedAddress
) -> ChainProbe:
    """Resolve an ambiguous EVM address to the chain it actually transacted on.

    Ethereum, BSC, Polygon, Arbitrum and Base share an address space, so a bare
    `0x...` from a complaint form could be any of them. Defaulting to Ethereum
    would silently trace the wrong chain and report "no activity" for an address
    that is busy on BSC. The candidates are probed concurrently - one
    rate-limiter window rather than five sequential ones - and the chain with
    the most transactions wins.
    """
    if not resolved.ambiguous or len(resolved.candidates) < 2:
        return ChainProbe(chain=resolved.chain, activity={}, probed=False)

    async def probe(chain: Chain) -> tuple[Chain, int]:
        try:
            summary = await router.get_address_summary(chain, resolved.address)
        except (UpstreamError, UnsupportedChainError):
            return chain, 0
        return chain, summary.tx_count if summary.exists else 0

    results = await asyncio.gather(*(probe(c) for c in resolved.candidates))
    activity = {chain.value: count for chain, count in results}
    best_chain, best_count = max(results, key=lambda item: item[1])

    if best_count == 0:
        # Nothing found anywhere: keep the conventional default and let the
        # trace report honestly that the address has no activity.
        return ChainProbe(chain=resolved.chain, activity=activity, probed=True)

    if sum(1 for _, count in results if count > 0) > 1:
        logger.info(
            "address_active_on_multiple_chains",
            address=resolved.address[:16],
            chains=[c for c, n in activity.items() if n > 0],
        )
    return ChainProbe(chain=best_chain, activity=activity, probed=True)


def provider_status() -> list[dict]:
    """Configuration snapshot for the admin panel."""
    rates = {
        "etherscan-v2": settings.etherscan_rate_per_sec,
        "trongrid": settings.trongrid_rate_per_sec,
        "mempool.space": settings.blockchair_rate_per_sec,
    }
    statuses = []
    for adapter in registry.live_adapters:
        configured = adapter.is_configured()
        statuses.append(
            {
                "name": adapter.name,
                "chains": sorted(c.value for c in adapter.supported_chains),
                "configured": configured,
                "rate_limit_per_sec": rates.get(adapter.name, 1.0),
                "detail": (
                    "ready"
                    if configured
                    else "API key missing - requests fall back to the seeded dataset"
                ),
            }
        )
    statuses.append(
        {
            "name": "synthetic",
            "chains": sorted(c.value for c in Chain),
            "configured": True,
            "rate_limit_per_sec": 0.0,
            "detail": "seeded fraud dataset (always available)",
        }
    )
    return statuses
