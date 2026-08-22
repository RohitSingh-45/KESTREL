"""TRON adapter backed by TronGrid.

The highest-priority chain for Indian cyber fraud: USDT-TRC20 is the dominant
settlement rail for investment and task-based scams, so TRC20 transfers are
fetched first and native TRX second.
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
    dedupe_transfers,
)
from app.chains.http import UpstreamClient
from app.chains.pricing import get_prices, to_usd_sync
from app.config import settings
from app.core.enums import Chain, DataSource, Direction
from app.core.errors import UnsupportedChainError, UpstreamError
from app.core.ratelimit import limiters
from app.logging_config import get_logger

logger = get_logger(__name__)

TRONGRID_URL = "https://api.trongrid.io"
SUN_PER_TRX = Decimal(1_000_000)
MAX_PAGE = 200


def _ts_ms(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(tz=UTC)


class TronAdapter(ChainAdapter):
    name = "trongrid"
    data_source = DataSource.LIVE

    def __init__(self) -> None:
        headers = {}
        if settings.trongrid_api_key:
            headers["TRON-PRO-API-KEY"] = settings.trongrid_api_key
        self.client = UpstreamClient(
            provider=self.name,
            bucket=limiters.get(self.name, settings.trongrid_rate_per_sec),
            base_url=TRONGRID_URL,
            headers=headers,
        )

    @property
    def supported_chains(self) -> frozenset[Chain]:
        return frozenset({Chain.TRON})

    def is_configured(self) -> bool:
        # TronGrid serves anonymous traffic at a reduced quota, so the adapter
        # is usable without a key - just slower and easier to throttle.
        return True

    def _check(self, chain: Chain) -> None:
        if chain is not Chain.TRON:
            raise UnsupportedChainError(f"TronAdapter cannot serve {chain.value}")

    async def _trc20(
        self, address: str, limit: int, cursor: str | None, prices: dict[str, float]
    ) -> tuple[list[Transfer], str | None]:
        path = cursor or f"/v1/accounts/{address}/transactions/trc20"
        params = None if cursor else {"limit": min(limit, MAX_PAGE), "order_by": "block_timestamp,desc"}
        payload, _ = await self.client.get_json(path, params)
        if not isinstance(payload, dict):
            raise UpstreamError("Unexpected TronGrid response shape")

        transfers: list[Transfer] = []
        for index, row in enumerate(payload.get("data") or []):
            info = row.get("token_info") or {}
            decimals = int(info.get("decimals") or 6)
            symbol = (info.get("symbol") or "TRC20").upper()[:32]
            try:
                amount = Decimal(str(row.get("value") or 0)) / (Decimal(10) ** decimals)
            except Exception:
                continue
            if amount <= 0:
                continue
            transfers.append(
                Transfer(
                    chain=Chain.TRON,
                    tx_hash=row.get("transaction_id", ""),
                    trace_index=index + 1,
                    block_number=0,
                    ts=_ts_ms(row.get("block_timestamp")),
                    src=row.get("from") or "",
                    dst=row.get("to") or "",
                    asset=symbol,
                    amount=amount,
                    amount_usd=to_usd_sync(amount, symbol, prices),
                    is_token=True,
                    token_contract=info.get("address"),
                    data_source=DataSource.LIVE,
                )
            )
        next_link = (payload.get("meta") or {}).get("links", {}).get("next")
        return transfers, next_link

    async def _native(self, address: str, limit: int, prices: dict[str, float]) -> list[Transfer]:
        payload, _ = await self.client.get_json(
            f"/v1/accounts/{address}/transactions",
            {"limit": min(limit, MAX_PAGE), "order_by": "block_timestamp,desc"},
        )
        if not isinstance(payload, dict):
            return []

        transfers: list[Transfer] = []
        for row in payload.get("data") or []:
            contracts = ((row.get("raw_data") or {}).get("contract")) or []
            if not contracts:
                continue
            contract = contracts[0]
            if contract.get("type") != "TransferContract":
                continue
            value = ((contract.get("parameter") or {}).get("value")) or {}
            amount = Decimal(str(value.get("amount") or 0)) / SUN_PER_TRX
            if amount <= 0:
                continue
            transfers.append(
                Transfer(
                    chain=Chain.TRON,
                    tx_hash=row.get("txID", ""),
                    block_number=int(row.get("blockNumber") or 0),
                    ts=_ts_ms(row.get("block_timestamp")),
                    src=value.get("owner_address_base58") or value.get("owner_address") or "",
                    dst=value.get("to_address_base58") or value.get("to_address") or "",
                    asset="TRX",
                    amount=amount,
                    amount_usd=to_usd_sync(amount, "TRX", prices),
                    data_source=DataSource.LIVE,
                )
            )
        # Hex-encoded participants cannot be matched against base58 input, so drop them.
        return [t for t in transfers if t.src.startswith("T") and t.dst.startswith("T")]

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

        trc20, next_cursor = await self._trc20(address, limit, cursor, prices)
        native: list[Transfer] = []
        if cursor is None:
            try:
                native = await self._native(address, limit, prices)
            except UpstreamError as exc:
                logger.warning("tron_native_fetch_failed", error=str(exc)[:160])

        combined = dedupe_transfers(trc20 + native)
        wanted = (
            [t for t in combined if t.src == address]
            if direction is Direction.OUT
            else [t for t in combined if t.dst == address]
        )
        wanted.sort(key=lambda t: (t.usd, t.ts), reverse=True)

        return TransferPage(
            transfers=wanted[:limit],
            cursor=next_cursor,
            has_more=bool(next_cursor),
            data_source=DataSource.LIVE,
        )

    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary:
        self._check(chain)
        prices = await get_prices()

        balance = Decimal(0)
        exists = False
        try:
            payload, _ = await self.client.get_json(f"/v1/accounts/{address}")
            rows = (payload or {}).get("data") or []
            if rows:
                exists = True
                balance = Decimal(str(rows[0].get("balance") or 0)) / SUN_PER_TRX
        except UpstreamError as exc:
            logger.warning("tron_account_fetch_failed", error=str(exc)[:160])

        trc20, _ = await self._trc20(address, MAX_PAGE, None, prices)
        inbound = [t for t in trc20 if t.dst == address]
        outbound = [t for t in trc20 if t.src == address]
        timestamps = [t.ts for t in trc20]

        return AddressSummary(
            chain=Chain.TRON,
            address=address,
            exists=exists or bool(trc20),
            tx_count=len(trc20),
            in_degree=len({t.src for t in inbound}),
            out_degree=len({t.dst for t in outbound}),
            first_seen=min(timestamps) if timestamps else None,
            last_seen=max(timestamps) if timestamps else None,
            balance_native=balance,
            total_received_usd=round(sum(t.usd for t in inbound), 2),
            total_sent_usd=round(sum(t.usd for t in outbound), 2),
            data_source=DataSource.LIVE,
        )

    async def close(self) -> None:
        await self.client.close()
