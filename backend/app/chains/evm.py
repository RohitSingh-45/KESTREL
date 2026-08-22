"""EVM adapter backed by the Etherscan V2 multichain endpoint.

One API key covers Ethereum, BSC, Polygon, Arbitrum and Base - the request is
routed by `chainid`. The free tier allows 3 requests/second and caps each
response at 1,000 records, both of which shape how this adapter paginates.
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
from app.core.enums import EVM_CHAIN_IDS, Chain, DataSource, Direction
from app.core.errors import UnsupportedChainError, UpstreamError
from app.core.ratelimit import limiters
from app.logging_config import get_logger

logger = get_logger(__name__)

ETHERSCAN_V2_URL = "https://api.etherscan.io/v2/api"
MAX_RECORDS_PER_CALL = 1_000


def _wei_to_decimal(raw: str | int, decimals: int) -> Decimal:
    try:
        value = Decimal(str(raw))
    except Exception:
        return Decimal(0)
    return value / (Decimal(10) ** decimals)


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(tz=UTC)


class EvmAdapter(ChainAdapter):
    name = "etherscan-v2"
    data_source = DataSource.LIVE

    def __init__(self) -> None:
        self.client = UpstreamClient(
            provider=self.name,
            bucket=limiters.get(self.name, settings.etherscan_rate_per_sec),
            base_url=ETHERSCAN_V2_URL,
        )

    @property
    def supported_chains(self) -> frozenset[Chain]:
        return frozenset(EVM_CHAIN_IDS)

    def is_configured(self) -> bool:
        return bool(settings.etherscan_api_key)

    def _chain_id(self, chain: Chain) -> int:
        try:
            return EVM_CHAIN_IDS[chain]
        except KeyError as exc:
            raise UnsupportedChainError(f"{chain.value} is not an Etherscan V2 chain") from exc

    async def _call(self, chain: Chain, module: str, action: str, **params: Any) -> Any:
        if not self.is_configured():
            raise UpstreamError("ETHERSCAN_API_KEY is not configured")
        payload, _cached = await self.client.get_json(
            "",
            {
                "chainid": self._chain_id(chain),
                "module": module,
                "action": action,
                "apikey": settings.etherscan_api_key,
                **params,
            },
        )
        if not isinstance(payload, dict):
            raise UpstreamError("Unexpected Etherscan response shape")

        status = str(payload.get("status", "0"))
        result = payload.get("result")
        message = str(payload.get("message", ""))

        if status == "1":
            return result
        # "No transactions found" is a valid empty answer, not a failure.
        if "no transactions found" in message.lower() or "no records found" in message.lower():
            return []
        if isinstance(result, str) and "rate limit" in result.lower():
            raise UpstreamError("Etherscan rate limit reached", details={"result": result})
        if module == "proxy" or action in {"balance", "eth_getCode"}:
            return result
        raise UpstreamError(
            f"Etherscan error: {message or 'unknown'}",
            details={"result": str(result)[:200]},
        )

    # -- transfers -------------------------------------------------------

    async def _native_transfers(
        self, chain: Chain, address: str, page: int, offset: int, prices: dict[str, float]
    ) -> list[Transfer]:
        rows = await self._call(
            chain,
            "account",
            "txlist",
            address=address,
            startblock=0,
            endblock=99_999_999,
            page=page,
            offset=offset,
            sort="desc",
        )
        asset = chain.native_asset
        transfers: list[Transfer] = []
        for row in rows or []:
            # A reverted transaction moved no value; including it would inflate the graph.
            if str(row.get("isError", "0")) == "1":
                continue
            amount = _wei_to_decimal(row.get("value", 0), 18)
            if amount <= 0:
                continue
            transfers.append(
                Transfer(
                    chain=chain,
                    tx_hash=row.get("hash", ""),
                    block_number=int(row.get("blockNumber") or 0),
                    ts=_ts(row.get("timeStamp")),
                    src=(row.get("from") or "").lower(),
                    dst=(row.get("to") or "").lower(),
                    asset=asset,
                    amount=amount,
                    amount_usd=to_usd_sync(amount, asset, prices),
                    is_token=False,
                    data_source=DataSource.LIVE,
                )
            )
        return transfers

    async def _token_transfers(
        self, chain: Chain, address: str, page: int, offset: int, prices: dict[str, float]
    ) -> list[Transfer]:
        rows = await self._call(
            chain,
            "account",
            "tokentx",
            address=address,
            startblock=0,
            endblock=99_999_999,
            page=page,
            offset=offset,
            sort="desc",
        )
        transfers: list[Transfer] = []
        for index, row in enumerate(rows or []):
            decimals = int(row.get("tokenDecimal") or 18)
            amount = _wei_to_decimal(row.get("value", 0), decimals)
            if amount <= 0:
                continue
            asset = (row.get("tokenSymbol") or "TOKEN").upper()[:32]
            transfers.append(
                Transfer(
                    chain=chain,
                    tx_hash=row.get("hash", ""),
                    trace_index=index + 1,
                    block_number=int(row.get("blockNumber") or 0),
                    ts=_ts(row.get("timeStamp")),
                    src=(row.get("from") or "").lower(),
                    dst=(row.get("to") or "").lower(),
                    asset=asset,
                    amount=amount,
                    amount_usd=to_usd_sync(amount, asset, prices),
                    is_token=True,
                    token_contract=(row.get("contractAddress") or "").lower() or None,
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
        address = address.lower()
        page = int(cursor) if cursor and cursor.isdigit() else 1
        offset = min(max(limit, 20), MAX_RECORDS_PER_CALL)
        prices = await get_prices()

        # Etherscan cannot filter by direction, so both sides come back and we
        # split locally. Token transfers dominate fraud cases, so they are
        # fetched even though it doubles the request count.
        native = await self._native_transfers(chain, address, page, offset, prices)
        tokens = await self._token_transfers(chain, address, page, offset, prices)
        combined = dedupe_transfers(native + tokens)

        wanted = (
            [t for t in combined if t.src == address]
            if direction is Direction.OUT
            else [t for t in combined if t.dst == address]
        )
        wanted.sort(key=lambda t: (t.usd, t.ts), reverse=True)

        saturated = len(native) >= offset or len(tokens) >= offset
        return TransferPage(
            transfers=wanted[:limit],
            cursor=str(page + 1) if saturated else None,
            has_more=saturated,
            data_source=DataSource.LIVE,
            truncated_reason=(
                f"Etherscan returned the maximum {offset} records per call"
                if saturated
                else None
            ),
        )

    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary:
        address = address.lower()
        prices = await get_prices()

        balance_raw = await self._call(chain, "account", "balance", address=address, tag="latest")
        balance = _wei_to_decimal(balance_raw or 0, 18)

        recent = await self._native_transfers(chain, address, 1, 200, prices)
        tokens = await self._token_transfers(chain, address, 1, 200, prices)
        combined = dedupe_transfers(recent + tokens)

        is_contract = False
        try:
            code = await self._call(chain, "proxy", "eth_getCode", address=address, tag="latest")
            is_contract = isinstance(code, str) and len(code) > 2
        except UpstreamError:
            pass

        inbound = [t for t in combined if t.dst == address]
        outbound = [t for t in combined if t.src == address]
        timestamps = [t.ts for t in combined]

        return AddressSummary(
            chain=chain,
            address=address,
            exists=bool(combined) or balance > 0,
            tx_count=len(combined),
            in_degree=len({t.src for t in inbound}),
            out_degree=len({t.dst for t in outbound}),
            first_seen=min(timestamps) if timestamps else None,
            last_seen=max(timestamps) if timestamps else None,
            balance_native=balance,
            total_received_usd=round(sum(t.usd for t in inbound), 2),
            total_sent_usd=round(sum(t.usd for t in outbound), 2),
            is_contract=is_contract,
            data_source=DataSource.LIVE,
        )

    async def close(self) -> None:
        await self.client.close()
