"""The normalised interface every chain speaks.

The tracing engine only ever sees `Transfer` records, so adding a chain means
writing one adapter and nothing else changes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.core.enums import Chain, DataSource, Direction

DEFAULT_PAGE_SIZE = 200


@dataclass(frozen=True, slots=True)
class Transfer:
    """One value movement between two addresses."""

    chain: Chain
    tx_hash: str
    block_number: int
    ts: datetime
    src: str
    dst: str
    asset: str
    amount: Decimal
    amount_usd: float | None = None
    trace_index: int = 0
    is_token: bool = False
    token_contract: str | None = None
    # Bitcoin: inputs co-spent in one transaction share this key.
    input_group: str | None = None
    # Bitcoin: every address that funded this transaction. Common-input-ownership
    # clustering needs the whole set, not just the one that funded this edge.
    cospend_inputs: tuple[str, ...] | None = None
    is_internal: bool = False
    fee_native: Decimal | None = None
    data_source: DataSource = DataSource.LIVE

    @property
    def key(self) -> tuple[str, str, int, str, str]:
        return (self.chain.value, self.tx_hash, self.trace_index, self.src, self.dst)

    @property
    def usd(self) -> float:
        return float(self.amount_usd or 0.0)


@dataclass(slots=True)
class TransferPage:
    transfers: list[Transfer]
    cursor: str | None = None
    has_more: bool = False
    data_source: DataSource = DataSource.LIVE
    truncated_reason: str | None = None


@dataclass(slots=True)
class AddressSummary:
    chain: Chain
    address: str
    exists: bool = True
    tx_count: int = 0
    in_degree: int = 0
    out_degree: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    balance_native: Decimal | None = None
    total_received_usd: float = 0.0
    total_sent_usd: float = 0.0
    is_contract: bool = False
    labels: list[str] = field(default_factory=list)
    data_source: DataSource = DataSource.LIVE


class ChainAdapter(abc.ABC):
    """Contract implemented by every data source."""

    name: str = "adapter"
    data_source: DataSource = DataSource.LIVE

    @property
    @abc.abstractmethod
    def supported_chains(self) -> frozenset[Chain]: ...

    @abc.abstractmethod
    def is_configured(self) -> bool:
        """False when the adapter lacks the API key it needs."""

    @abc.abstractmethod
    async def get_transfers(
        self,
        chain: Chain,
        address: str,
        direction: Direction,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> TransferPage: ...

    @abc.abstractmethod
    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary: ...

    async def close(self) -> None:
        return None


def split_by_direction(
    transfers: list[Transfer], address: str, direction: Direction
) -> list[Transfer]:
    """Filter a mixed transfer list down to one direction relative to `address`."""
    target = address.lower()
    if direction is Direction.OUT:
        return [t for t in transfers if t.src.lower() == target]
    return [t for t in transfers if t.dst.lower() == target]


def dedupe_transfers(transfers: list[Transfer]) -> list[Transfer]:
    seen: set[tuple] = set()
    unique: list[Transfer] = []
    for transfer in transfers:
        if transfer.key in seen:
            continue
        seen.add(transfer.key)
        unique.append(transfer)
    return unique
