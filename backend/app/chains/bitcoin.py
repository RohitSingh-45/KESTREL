"""Bitcoin adapter.

UTXO transactions have no inherent sender/receiver pair, so they are flattened
into directed edges. Primary source is mempool.space because it returns full
inputs and outputs in a single call per address - which is what makes
common-input-ownership clustering possible without a request per transaction.
Blockchair is used as a fallback for address statistics.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.chains.base import (
    DEFAULT_PAGE_SIZE,
    AddressSummary,
    ChainAdapter,
    Transfer,
    TransferPage,
)
from app.chains.http import UpstreamClient
from app.chains.pricing import get_prices, to_usd_sync
from app.config import settings
from app.core.enums import Chain, DataSource, Direction
from app.core.errors import UnsupportedChainError, UpstreamError
from app.core.ratelimit import limiters
from app.logging_config import get_logger

logger = get_logger(__name__)

MEMPOOL_URL = "https://mempool.space/api"
BLOCKCHAIR_URL = "https://api.blockchair.com/bitcoin"
SATS = Decimal(100_000_000)

# Beyond this the input x output product is a coinjoin-shaped mess; fall back
# to attributing everything to the largest input.
MAX_EDGE_PRODUCT = 64


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(tz=UTC)


class BitcoinAdapter(ChainAdapter):
    name = "mempool.space"
    data_source = DataSource.LIVE

    def __init__(self) -> None:
        self.client = UpstreamClient(
            provider=self.name,
            bucket=limiters.get(self.name, settings.blockchair_rate_per_sec),
            base_url=MEMPOOL_URL,
        )
        self.fallback = UpstreamClient(
            provider="blockchair",
            bucket=limiters.get("blockchair", settings.blockchair_rate_per_sec),
            base_url=BLOCKCHAIR_URL,
        )

    @property
    def supported_chains(self) -> frozenset[Chain]:
        return frozenset({Chain.BITCOIN})

    def is_configured(self) -> bool:
        return True  # mempool.space needs no key

    def _check(self, chain: Chain) -> None:
        if chain is not Chain.BITCOIN:
            raise UnsupportedChainError(f"BitcoinAdapter cannot serve {chain.value}")

    def _flatten(self, tx: dict, prices: dict[str, float]) -> list[Transfer]:
        """Turn one UTXO transaction into directed value edges."""
        tx_hash = tx.get("txid", "")
        status = tx.get("status") or {}
        ts = _ts(status.get("block_time"))
        block = int(status.get("block_height") or 0)

        inputs: list[tuple[str, Decimal]] = []
        for vin in tx.get("vin") or []:
            prevout = vin.get("prevout") or {}
            address = prevout.get("scriptpubkey_address")
            value = Decimal(str(prevout.get("value") or 0)) / SATS
            if address and value > 0:
                inputs.append((address, value))

        outputs: list[tuple[str, Decimal]] = []
        for vout in tx.get("vout") or []:
            address = vout.get("scriptpubkey_address")
            value = Decimal(str(vout.get("value") or 0)) / SATS
            if address and value > 0:
                outputs.append((address, value))

        if not inputs or not outputs:
            return []

        cospend = tuple(sorted({addr for addr, _ in inputs})) if len(inputs) > 1 else None
        total_in = sum(value for _, value in inputs) or Decimal(1)
        transfers: list[Transfer] = []
        index = 0

        use_proportional = len(inputs) * len(outputs) <= MAX_EDGE_PRODUCT
        if not use_proportional:
            inputs = [max(inputs, key=lambda pair: pair[1])]
            total_in = inputs[0][1] or Decimal(1)

        for src, funded in inputs:
            share = funded / total_in
            for dst, out_value in outputs:
                if src == dst:
                    # Change returning to the funding address is not a movement.
                    continue
                amount = (out_value * share).quantize(Decimal("0.00000001"))
                if amount <= 0:
                    continue
                index += 1
                transfers.append(
                    Transfer(
                        chain=Chain.BITCOIN,
                        tx_hash=tx_hash,
                        trace_index=index,
                        block_number=block,
                        ts=ts,
                        src=src,
                        dst=dst,
                        asset="BTC",
                        amount=amount,
                        amount_usd=to_usd_sync(amount, "BTC", prices),
                        input_group=tx_hash if cospend else None,
                        cospend_inputs=cospend,
                        data_source=DataSource.LIVE,
                    )
                )
        return transfers

    async def get_transfers(
        self,
        chain: Chain,
        address: str,
        direction: Direction,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> TransferPage:
        self._check(chain)
        prices = await get_prices()

        path = f"/address/{address}/txs"
        if cursor:
            path = f"{path}/chain/{cursor}"
        payload, _ = await self.client.get_json(path)
        if not isinstance(payload, list):
            raise UpstreamError("Unexpected mempool.space response shape")

        transfers: list[Transfer] = []
        for tx in payload:
            transfers.extend(self._flatten(tx, prices))

        wanted = (
            [t for t in transfers if t.src == address]
            if direction is Direction.OUT
            else [t for t in transfers if t.dst == address]
        )
        wanted.sort(key=lambda t: (t.usd, t.ts), reverse=True)

        # mempool.space pages 25 confirmed transactions at a time, keyed by the
        # last txid seen.
        next_cursor = payload[-1].get("txid") if len(payload) >= 25 else None

        return TransferPage(
            transfers=wanted[:limit],
            cursor=next_cursor,
            has_more=bool(next_cursor),
            data_source=DataSource.LIVE,
        )

    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary:
        self._check(chain)
        prices = await get_prices()
        try:
            payload, _ = await self.client.get_json(f"/address/{address}")
        except UpstreamError:
            return await self._summary_via_blockchair(address, prices)

        chain_stats = payload.get("chain_stats") or {}
        mempool_stats = payload.get("mempool_stats") or {}
        funded = Decimal(str(chain_stats.get("funded_txo_sum") or 0)) / SATS
        spent = Decimal(str(chain_stats.get("spent_txo_sum") or 0)) / SATS
        tx_count = int(chain_stats.get("tx_count") or 0) + int(mempool_stats.get("tx_count") or 0)

        return AddressSummary(
            chain=Chain.BITCOIN,
            address=address,
            exists=tx_count > 0,
            tx_count=tx_count,
            balance_native=funded - spent,
            total_received_usd=to_usd_sync(funded, "BTC", prices),
            total_sent_usd=to_usd_sync(spent, "BTC", prices),
            data_source=DataSource.LIVE,
        )

    async def _summary_via_blockchair(
        self, address: str, prices: dict[str, float]
    ) -> AddressSummary:
        payload, _ = await self.fallback.get_json(
            f"/dashboards/address/{address}",
            {"limit": 0, "key": settings.blockchair_api_key},
        )
        data = ((payload or {}).get("data") or {}).get(address) or {}
        info = data.get("address") or {}
        received = Decimal(str(info.get("received") or 0)) / SATS
        spent = Decimal(str(info.get("spent") or 0)) / SATS
        return AddressSummary(
            chain=Chain.BITCOIN,
            address=address,
            exists=bool(info),
            tx_count=int(info.get("transaction_count") or 0),
            balance_native=received - spent,
            total_received_usd=to_usd_sync(received, "BTC", prices),
            total_sent_usd=to_usd_sync(spent, "BTC", prices),
            data_source=DataSource.LIVE,
        )

    async def close(self) -> None:
        await self.client.close()
        await self.fallback.close()
