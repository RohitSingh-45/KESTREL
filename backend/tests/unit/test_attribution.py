"""Attribution tiers: labels, consolidation heuristic, clustering, classifier."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.attribution.btc_cluster import UnionFind, build_cluster_from_transfers
from app.attribution.classifier import classify_address
from app.attribution.deposit_heuristic import detect_deposit_address
from app.attribution.features import extract_features
from app.attribution.labels import LabelStore
from app.attribution.models import Attribution, Candidate
from app.attribution.service import AttributionContext, AttributionService
from app.chains.base import Transfer
from app.core.enums import AttributionMethod, Chain, EntityType

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def transfer(src, dst, usd, minutes=0, chain=Chain.TRON, cospend=None, tx="tx"):
    return Transfer(
        chain=chain,
        tx_hash=f"{tx}-{src}-{dst}-{minutes}",
        block_number=1,
        ts=T0 + timedelta(minutes=minutes),
        src=src,
        dst=dst,
        asset="USDT",
        amount=Decimal(str(usd)),
        amount_usd=float(usd),
        is_token=True,
        cospend_inputs=cospend,
        input_group=tx if cospend else None,
    )


class TestAttributionRanking:
    def test_sanctions_outrank_higher_confidence_labels(self):
        result = Attribution(chain=Chain.ETHEREUM, address="0xabc")
        result.add(
            Candidate("Binance", EntityType.CEX_HOT_WALLET, AttributionMethod.DIRECT_LABEL, 0.99)
        )
        result.add(
            Candidate(
                "Tornado Cash",
                EntityType.MIXER,
                AttributionMethod.SANCTIONS_LIST,
                0.9,
                is_sanctioned=True,
            )
        )
        result.finalise()
        assert result.best.entity_name == "Tornado Cash"
        assert result.is_sanctioned

    def test_confidence_is_capped_by_method(self):
        candidate = Candidate(
            "Guess", EntityType.CEX_DEPOSIT, AttributionMethod.ML_CLASSIFIER, 0.99
        )
        assert candidate.confidence == 0.75

    def test_only_a_confident_vasp_terminates_a_trace(self):
        result = Attribution(chain=Chain.TRON, address="T1")
        result.add(
            Candidate("Mixer", EntityType.MIXER, AttributionMethod.DIRECT_LABEL, 0.99)
        )
        result.finalise()
        assert not result.is_terminal(0.75)

        result.add(
            Candidate(
                "Binance",
                EntityType.CEX_DEPOSIT,
                AttributionMethod.DEPOSIT_CONSOLIDATION,
                0.85,
                vasp_slug="binance",
            )
        )
        result.finalise()
        assert result.is_terminal(0.75)
        assert result.vasp_slug == "binance"


class TestDepositHeuristic:
    async def test_detects_a_classic_deposit_address(self, seeded_session):
        from app.seeds.synthetic import build_scenarios

        scenario = next(s for s in build_scenarios() if s.key == "direct_tron")
        deposit = scenario.expected_deposit_address
        outbound = [t for t in scenario.transfers if t.src_address == deposit]
        inbound = [t for t in scenario.transfers if t.dst_address == deposit]

        store = LabelStore(seeded_session)
        candidate = await detect_deposit_address(
            store,
            Chain.TRON,
            deposit,
            [transfer(t.src_address, t.dst_address, t.amount_usd) for t in outbound],
            [transfer(t.src_address, t.dst_address, t.amount_usd) for t in inbound],
        )
        assert candidate is not None
        assert candidate.vasp_slug == "zebpay"
        assert candidate.entity_type is EntityType.CEX_DEPOSIT
        assert candidate.confidence >= 0.75
        assert candidate.evidence["sweep_ratio"] > 0.9

    async def test_ignores_an_address_with_no_exchange_destination(self, seeded_session):
        store = LabelStore(seeded_session)
        candidate = await detect_deposit_address(
            store, Chain.TRON, "TSender", [transfer("TSender", "TRandom", 500)], []
        )
        assert candidate is None

    async def test_single_payer_single_sweep_is_downgraded(self, seeded_session):
        """A user topping up their own exchange account is not a deposit-address hit."""
        from app.seeds.synthetic import build_scenarios

        scenario = next(s for s in build_scenarios() if s.key == "direct_tron")
        hot = next(
            label.address
            for label in scenario.labels
            if label.entity_type is EntityType.CEX_HOT_WALLET
        )
        store = LabelStore(seeded_session)
        candidate = await detect_deposit_address(
            store,
            Chain.TRON,
            "TLonelyWallet",
            [transfer("TLonelyWallet", hot, 1000)],
            [transfer("TOnePayer", "TLonelyWallet", 1000)],
        )
        assert candidate is None or candidate.confidence < 0.75


class TestUnionFind:
    def test_transitive_merging(self):
        uf = UnionFind()
        uf.union("a", "b")
        uf.union("b", "c")
        uf.union("d", "e")
        assert uf.members("a") == {"a", "b", "c"}
        assert uf.members("e") == {"d", "e"}

    def test_cluster_from_cospend_transfers(self):
        transfers = [
            transfer("A", "Z", 10, cospend=("A", "B", "C"), tx="t1", chain=Chain.BITCOIN),
            transfer("B", "Z", 10, cospend=("A", "B", "C"), tx="t1", chain=Chain.BITCOIN),
        ]
        cluster = build_cluster_from_transfers("A", transfers)
        assert cluster.members == {"A", "B", "C"}
        assert not cluster.coinjoin_suspected

    def test_coinjoin_shape_is_flagged(self):
        inputs = tuple(f"IN{i}" for i in range(8))
        transfers = [
            transfer("IN0", "OUT", 10, cospend=inputs, tx="cj", chain=Chain.BITCOIN)
        ]
        cluster = build_cluster_from_transfers("IN0", transfers)
        assert cluster.coinjoin_suspected


class TestFeatures:
    def test_pass_through_wallet(self):
        inbound = [transfer(f"P{i}", "X", 1000, minutes=i) for i in range(4)]
        outbound = [transfer("X", "HOT", 3990, minutes=10)]
        features = extract_features("X", inbound, outbound)
        assert features.in_degree == 4
        assert features.sweep_ratio > 0.99
        assert features.balance_retention < 0.01
        assert features.is_pass_through == 1.0
        assert features.max_out_share == 1.0

    def test_savings_wallet_retains_balance(self):
        inbound = [transfer("P", "X", 5000, minutes=0)]
        outbound = [transfer("X", "Y", 500, minutes=100)]
        features = extract_features("X", inbound, outbound)
        assert features.balance_retention == pytest.approx(0.9, abs=0.01)
        assert features.is_pass_through == 0.0

    def test_empty_history_is_all_zeros(self):
        features = extract_features("X", [], [])
        assert all(value == 0.0 for value in features.to_vector())


class TestClassifier:
    def test_high_throughput_uniform_wallet_is_called_a_mixer(self):
        inbound = [transfer(f"P{i}", "MIX", 10_000, minutes=i * 17) for i in range(25)]
        outbound = [
            transfer("MIX", f"Q{i}", 10_000, minutes=400 + i * 19) for i in range(25)
        ]
        candidate = classify_address(extract_features("MIX", inbound, outbound))
        assert candidate is not None
        assert candidate.entity_type is EntityType.MIXER
        assert candidate.confidence <= 0.75
        assert "caveat" in candidate.evidence

    def test_busy_two_sided_wallet_is_called_a_p2p_desk(self):
        rng = random.Random(1)
        inbound = [
            transfer(f"A{i}", "P2P", rng.uniform(400, 9000), minutes=i * 23)
            for i in range(30)
        ]
        outbound = [
            transfer("P2P", f"B{i}", rng.uniform(300, 7000), minutes=50 + i * 29)
            for i in range(28)
        ]
        candidate = classify_address(extract_features("P2P", inbound, outbound))
        assert candidate is not None
        assert candidate.entity_type is EntityType.P2P_MERCHANT

    def test_sweeping_wallet_is_not_claimed_as_a_deposit_address(self):
        # A small pass-through wallet is shape-identical to a layering mule; only
        # tier 2 can tell them apart, because it knows what the sweep target is.
        inbound = [transfer(f"P{i}", "X", 900, minutes=i * 5) for i in range(5)]
        outbound = [transfer("X", "HOT", 4480, minutes=40)]
        candidate = classify_address(extract_features("X", inbound, outbound))
        assert candidate is None or candidate.entity_type is not EntityType.CEX_DEPOSIT

    def test_quiet_personal_wallet_returns_nothing(self):
        inbound = [transfer("P", "X", 300, minutes=0)]
        outbound = [transfer("X", "Q", 40, minutes=900)]
        assert classify_address(extract_features("X", inbound, outbound)) is None


class TestAttributionService:
    async def test_labelled_hot_wallet_short_circuits_to_tier_one(self, seeded_session):
        from app.seeds.synthetic import build_scenarios

        scenario = next(s for s in build_scenarios() if s.key == "investment_scam_tron")
        hot = next(
            label.address
            for label in scenario.labels
            if label.entity_type is EntityType.CEX_HOT_WALLET
        )
        service = AttributionService(seeded_session)
        result = await service.identify(
            Chain.TRON, hot, AttributionContext(allow_fetch=False)
        )
        assert result.best.method is AttributionMethod.DIRECT_LABEL
        assert result.vasp_slug == "binance"
        assert result.confidence >= 0.95

    async def test_unlabelled_deposit_address_is_found_by_tier_two(self, seeded_session):
        from sqlalchemy import select

        from app.db.models import SyntheticGroundTruth

        truth = (
            await seeded_session.execute(
                select(SyntheticGroundTruth).where(
                    SyntheticGroundTruth.scenario == "investment_scam_tron"
                )
            )
        ).scalar_one()

        service = AttributionService(seeded_session)
        # No label exists for this address - that is the whole point.
        assert not await service.labels.get(Chain.TRON, truth.expected_deposit_address)

        result = await service.identify(Chain.TRON, truth.expected_deposit_address)
        assert result.vasp_slug == truth.expected_vasp_slug
        assert result.best.method is AttributionMethod.DEPOSIT_CONSOLIDATION
        assert result.is_terminal(0.75)

    async def test_sanctioned_mixer_is_flagged(self, seeded_session):
        service = AttributionService(seeded_session)
        result = await service.identify(
            Chain.ETHEREUM,
            "0x910cbd523d972eb0a6f4cae4618ad62622b39dbf",
            AttributionContext(allow_fetch=False),
        )
        assert result.entity_type is EntityType.MIXER
        assert not result.is_terminal(0.75), "a mixer is not a servable VASP"

    async def test_persist_and_reload(self, seeded_session):
        from sqlalchemy import select

        from app.db.models import SyntheticGroundTruth

        truth = (
            await seeded_session.execute(
                select(SyntheticGroundTruth).where(
                    SyntheticGroundTruth.scenario == "direct_tron"
                )
            )
        ).scalar_one()

        service = AttributionService(seeded_session)
        result = await service.identify(Chain.TRON, truth.expected_deposit_address)
        await service.persist(result)
        await seeded_session.commit()

        reloaded = await AttributionService(seeded_session).load_stored(
            Chain.TRON, truth.expected_deposit_address
        )
        assert reloaded is not None
        assert reloaded.vasp_slug == truth.expected_vasp_slug
