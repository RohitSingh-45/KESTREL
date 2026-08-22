"""Address detection, rate limiting and UTXO flattening."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.chains.addresses import detect_address, is_valid_for_chain, resolve_chain
from app.chains.bitcoin import BitcoinAdapter
from app.core.enums import Chain, Direction
from app.core.errors import UnsupportedChainError
from app.core.ratelimit import RateLimitExceeded, TokenBucket
from app.seeds.addresses import btc_address, evm_address, tron_address


class TestAddressDetection:
    def test_evm_is_ambiguous_across_chains(self):
        info = detect_address("0x28C6c06298d514Db089934071355E5743bf21d60")
        assert info.primary is Chain.ETHEREUM
        assert info.ambiguous is True
        assert Chain.BSC in info.candidates
        assert info.address == info.address.lower()

    def test_tron_address_round_trips(self):
        address = tron_address("unit-test-tron")
        info = detect_address(address)
        assert info.primary is Chain.TRON
        assert info.ambiguous is False

    def test_bitcoin_legacy_address(self):
        address = btc_address("unit-test-btc")
        info = detect_address(address)
        assert info.primary is Chain.BITCOIN

    def test_bech32_address(self):
        info = detect_address("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
        assert info.primary is Chain.BITCOIN
        assert info.format == "btc-bech32"

    def test_garbage_is_rejected(self):
        with pytest.raises(UnsupportedChainError):
            detect_address("not-a-wallet-address")

    def test_tron_checksum_is_enforced(self):
        broken = tron_address("unit-test-tron")[:-1] + "X"
        with pytest.raises(UnsupportedChainError):
            detect_address(broken)

    def test_hint_selects_among_evm_chains(self):
        address, chain, _ = resolve_chain(evm_address("x"), Chain.POLYGON)
        assert chain is Chain.POLYGON
        assert address.startswith("0x")

    def test_hint_conflicting_with_format_is_rejected(self):
        with pytest.raises(UnsupportedChainError):
            resolve_chain(tron_address("x"), Chain.ETHEREUM)

    def test_is_valid_for_chain(self):
        assert is_valid_for_chain(evm_address("y"), Chain.BASE)
        assert not is_valid_for_chain(evm_address("y"), Chain.BITCOIN)


class TestTokenBucket:
    async def test_burst_then_throttle(self):
        bucket = TokenBucket("test", rate_per_sec=5.0, burst=2)
        assert await bucket.acquire() == 0.0
        assert await bucket.acquire() == 0.0
        waited = await bucket.acquire()
        assert waited > 0

    async def test_refuses_to_wait_beyond_budget(self):
        bucket = TokenBucket("test", rate_per_sec=0.5, burst=1)
        await bucket.acquire()
        with pytest.raises(RateLimitExceeded):
            await bucket.acquire(max_wait=0.1)

    async def test_concurrent_callers_are_serialised(self):
        bucket = TokenBucket("test", rate_per_sec=20.0, burst=1)
        await asyncio.gather(*(bucket.acquire() for _ in range(4)))
        assert bucket.total_acquisitions == 4


class TestUtxoFlattening:
    @staticmethod
    def _tx(vin, vout, txid="tx1"):
        return {
            "txid": txid,
            "status": {"block_height": 800_000, "block_time": 1_700_000_000},
            "vin": [
                {"prevout": {"scriptpubkey_address": addr, "value": sats}}
                for addr, sats in vin
            ],
            "vout": [
                {"scriptpubkey_address": addr, "value": sats} for addr, sats in vout
            ],
        }

    def test_single_input_fans_out_to_outputs(self):
        adapter = BitcoinAdapter()
        tx = self._tx([("A", 100_000_000)], [("B", 60_000_000), ("C", 39_000_000)])
        transfers = adapter._flatten(tx, {"BTC": 60_000.0})
        assert {t.dst for t in transfers} == {"B", "C"}
        assert all(t.src == "A" for t in transfers)
        assert transfers[0].amount == Decimal("0.60000000")

    def test_multi_input_records_the_cospend_set(self):
        adapter = BitcoinAdapter()
        tx = self._tx([("A", 50_000_000), ("B", 50_000_000)], [("C", 99_000_000)])
        transfers = adapter._flatten(tx, {"BTC": 60_000.0})
        assert all(t.cospend_inputs == ("A", "B") for t in transfers)
        assert all(t.input_group == "tx1" for t in transfers)
        # Value is split proportionally to what each input funded.
        assert sum(t.amount for t in transfers) == Decimal("0.99000000")

    def test_change_back_to_the_funding_address_is_not_an_edge(self):
        adapter = BitcoinAdapter()
        tx = self._tx([("A", 100_000_000)], [("B", 30_000_000), ("A", 69_000_000)])
        transfers = adapter._flatten(tx, {"BTC": 60_000.0})
        assert {t.dst for t in transfers} == {"B"}

    def test_coinjoin_shape_collapses_to_the_largest_input(self):
        adapter = BitcoinAdapter()
        vin = [(f"IN{i}", 10_000_000) for i in range(10)]
        vin[3] = ("IN3", 90_000_000)
        vout = [(f"OUT{i}", 10_000_000) for i in range(10)]
        transfers = adapter._flatten(self._tx(vin, vout), {"BTC": 60_000.0})
        assert {t.src for t in transfers} == {"IN3"}
        assert len(transfers) == 10


class TestSyntheticAdapter:
    async def test_reads_seeded_scenario(self, seeded_session):
        from sqlalchemy import select

        from app.chains.synthetic import SyntheticAdapter
        from app.db.models import SyntheticGroundTruth

        truth = (
            await seeded_session.execute(
                select(SyntheticGroundTruth).where(
                    SyntheticGroundTruth.scenario == "investment_scam_tron"
                )
            )
        ).scalar_one()

        adapter = SyntheticAdapter(seeded_session)
        page = await adapter.get_transfers(
            Chain(truth.chain), truth.seed_address, Direction.OUT
        )
        assert page.transfers, "seed address should have outbound transfers"
        assert all(t.src == truth.seed_address for t in page.transfers)
        assert page.data_source.value == "synthetic"

    async def test_summary_counts_both_directions(self, seeded_session):
        from app.chains.synthetic import SyntheticAdapter
        from app.seeds.synthetic import build_scenarios

        scenario = next(s for s in build_scenarios() if s.key == "investment_scam_tron")
        collector = next(
            t.dst_address for t in scenario.transfers if t.src_address == scenario.seed_address
        )
        adapter = SyntheticAdapter(seeded_session)
        summary = await adapter.get_address_summary(Chain.TRON, collector)
        assert summary.exists
        assert summary.in_degree > 1
        assert summary.total_received_usd > 0
