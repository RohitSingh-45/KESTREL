"""Adapter over the seeded fraud dataset.

Same interface as the live adapters, reading from `synthetic_transfers`. This
is what makes a demo work on a disconnected laptop and what gives the accuracy
harness a fixed ground truth to score against.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chains.base import (
    DEFAULT_PAGE_SIZE,
    AddressSummary,
    ChainAdapter,
    Transfer,
    TransferPage,
)
from app.core.enums import Chain, DataSource, Direction
from app.db.models import SyntheticTransfer
from app.db.session import session_scope


def _to_transfer(row: SyntheticTransfer, cospend: tuple[str, ...] | None = None) -> Transfer:
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
        token_contract=row.token_contract,
        input_group=row.input_group,
        cospend_inputs=cospend,
        data_source=DataSource.SYNTHETIC,
    )


class SyntheticAdapter(ChainAdapter):
    name = "synthetic"
    data_source = DataSource.SYNTHETIC

    def __init__(self, session: AsyncSession | None = None) -> None:
        # A session may be injected by the caller; otherwise each call opens its own.
        self._session = session
        self._known: dict[Chain, set[str]] = {}
        # An AsyncSession cannot serve two queries at once, and callers do fan
        # requests out with asyncio.gather (probing several chains for the same
        # address, for one). Serialise access to a borrowed session rather than
        # making every caller remember that constraint.
        self._lock = asyncio.Lock()

    async def _with_session(self, run):
        if self._session is not None:
            async with self._lock:
                return await run(self._session)
        async with session_scope() as session:
            return await run(session)

    @property
    def supported_chains(self) -> frozenset[Chain]:
        return frozenset(Chain)

    def is_configured(self) -> bool:
        return True

    async def _cospend_map(
        self, session: AsyncSession, rows: list[SyntheticTransfer]
    ) -> dict[str, tuple[str, ...]]:
        groups = {row.input_group for row in rows if row.input_group}
        if not groups:
            return {}
        result = await session.execute(
            select(SyntheticTransfer.input_group, SyntheticTransfer.src_address).where(
                SyntheticTransfer.input_group.in_(groups)
            )
        )
        mapping: dict[str, set[str]] = {}
        for group, src in result.all():
            mapping.setdefault(group, set()).add(src)
        return {group: tuple(sorted(values)) for group, values in mapping.items()}

    async def get_transfers(
        self,
        chain: Chain,
        address: str,
        direction: Direction,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> TransferPage:
        offset = int(cursor) if cursor and cursor.isdigit() else 0
        column = (
            SyntheticTransfer.src_address
            if direction is Direction.OUT
            else SyntheticTransfer.dst_address
        )
        stmt = (
            select(SyntheticTransfer)
            .where(SyntheticTransfer.chain == chain, column == address)
            .order_by(SyntheticTransfer.amount_usd.desc(), SyntheticTransfer.ts.desc())
            .offset(offset)
            .limit(limit + 1)
        )

        async def run(session: AsyncSession) -> TransferPage:
            rows = list((await session.execute(stmt)).scalars().all())
            has_more = len(rows) > limit
            rows = rows[:limit]
            cospend = await self._cospend_map(session, rows)
            return TransferPage(
                transfers=[_to_transfer(r, cospend.get(r.input_group or "")) for r in rows],
                cursor=str(offset + limit) if has_more else None,
                has_more=has_more,
                data_source=DataSource.SYNTHETIC,
            )

        return await self._with_session(run)

    async def get_address_summary(self, chain: Chain, address: str) -> AddressSummary:
        async def run(session: AsyncSession) -> AddressSummary:
            stmt = select(SyntheticTransfer).where(
                SyntheticTransfer.chain == chain,
                or_(
                    SyntheticTransfer.src_address == address,
                    SyntheticTransfer.dst_address == address,
                ),
            )
            rows = list((await session.execute(stmt)).scalars().all())
            inbound = [r for r in rows if r.dst_address == address]
            outbound = [r for r in rows if r.src_address == address]
            timestamps = [r.ts for r in rows]
            received = sum(r.amount_usd or 0 for r in inbound)
            sent = sum(r.amount_usd or 0 for r in outbound)

            native_in = sum(
                Decimal(str(r.amount)) for r in inbound if r.asset == chain.native_asset
            )
            native_out = sum(
                Decimal(str(r.amount)) for r in outbound if r.asset == chain.native_asset
            )

            return AddressSummary(
                chain=chain,
                address=address,
                exists=bool(rows),
                tx_count=len(rows),
                in_degree=len({r.src_address for r in inbound}),
                out_degree=len({r.dst_address for r in outbound}),
                first_seen=min(timestamps) if timestamps else None,
                last_seen=max(timestamps) if timestamps else None,
                balance_native=native_in - native_out,
                total_received_usd=round(received, 2),
                total_sent_usd=round(sent, 2),
                data_source=DataSource.SYNTHETIC,
            )

        return await self._with_session(run)

    async def knows(self, chain: Chain, address: str) -> bool:
        """Is this address part of the seeded corpus?

        A trace visits hundreds of addresses on one chain, so the whole set for
        that chain is loaded once rather than queried per address. The set is
        cached on the adapter instance, which lives for one trace, so a reseed
        between traces is picked up.
        """
        known = self._known.get(chain)
        if known is None:
            stmt = select(SyntheticTransfer.src_address, SyntheticTransfer.dst_address).where(
                SyntheticTransfer.chain == chain
            )

            async def run(session: AsyncSession):
                return (await session.execute(stmt)).all()

            rows = await self._with_session(run)
            known = {value for row in rows for value in row if value}
            self._known[chain] = known
        return address in known

    async def has_data(self, chain: Chain, address: str) -> bool:
        stmt = select(func.count()).select_from(SyntheticTransfer).where(
            SyntheticTransfer.chain == chain,
            or_(
                SyntheticTransfer.src_address == address,
                SyntheticTransfer.dst_address == address,
            ),
        )

        async def run(session: AsyncSession) -> bool:
            return bool(await session.scalar(stmt))

        return await self._with_session(run)
