"""Taint arithmetic, pruning decisions and the traversal itself."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.attribution.models import Attribution, Candidate
from app.chains.base import Transfer, TransferPage
from app.core.enums import AttributionMethod, Chain, DataSource, Direction, EntityType
from app.tracing.models import TerminalReason, TraceParams
from app.tracing.paths import build_summary, humanise_duration, rank_paths
from app.tracing.pruning import decide, select_fanout
from app.tracing.recommendations import build_recommendations
from app.tracing.taint import aggregate_outflows, split_transfer_taint
from app.tracing.traversal import Traversal

T0 = datetime(2026, 6, 1, tzinfo=UTC)


def tx(src, dst, usd, minutes=0, chain=Chain.TRON, asset="USDT"):
    return Transfer(
        chain=chain,
        tx_hash=f"0x{src}-{dst}-{minutes}",
        block_number=1000 + minutes,
        ts=T0 + timedelta(minutes=minutes),
        src=src,
        dst=dst,
        asset=asset,
        amount=Decimal(str(usd)),
        amount_usd=float(usd),
        is_token=True,
        data_source=DataSource.SYNTHETIC,
    )


class FakeRouter:
    """Serves a hand-built graph through the DataRouter interface."""

    def __init__(self, transfers: list[Transfer]) -> None:
        self.transfers = transfers
        self.calls = 0
        self.live_calls = 0
        self.synthetic_calls = 1
        self.notes: list[str] = []

    async def get_transfers(self, chain, address, direction, *, limit=200, cursor=None):
        self.calls += 1
        if direction is Direction.OUT:
            found = [t for t in self.transfers if t.src == address]
        else:
            found = [t for t in self.transfers if t.dst == address]
        return TransferPage(transfers=found[:limit], data_source=DataSource.SYNTHETIC)

    @property
    def effective_source(self) -> DataSource:
        return DataSource.SYNTHETIC

    def stats(self) -> dict:
        return {"notes": self.notes}


class FakeAttribution:
    """Attributes addresses from a fixed dictionary, with no database."""

    def __init__(self, mapping: dict[str, Candidate] | None = None) -> None:
        self.mapping = mapping or {}

    async def prefetch_labels(self, chain, addresses) -> None:
        return None

    async def identify(self, chain, address, context=None) -> Attribution:
        result = Attribution(chain=chain, address=address)
        result.add(self.mapping.get(address))
        return result.finalise()

    def observation(self, chain, address):
        return None

    def stats(self) -> dict:
        return {}


def vasp_candidate(name="Binance", slug="binance", confidence=0.9) -> Candidate:
    return Candidate(
        entity_name=name,
        entity_type=EntityType.CEX_DEPOSIT,
        method=AttributionMethod.DEPOSIT_CONSOLIDATION,
        confidence=confidence,
        vasp_slug=slug,
    )


class TestTaint:
    def test_shares_are_proportional_to_value(self):
        transfers = [tx("A", "B", 750), tx("A", "C", 250, minutes=1)]
        outflows = aggregate_outflows(transfers, "A")
        by_dst = {f.dst: outflows.share_of(f) for f in outflows.flows}
        assert by_dst["B"] == pytest.approx(0.75)
        assert by_dst["C"] == pytest.approx(0.25)

    def test_repeat_payments_to_one_address_are_one_counterparty(self):
        transfers = [tx("A", "B", 100, minutes=i) for i in range(10)]
        outflows = aggregate_outflows(transfers, "A")
        assert outflows.counterparties == 1
        assert outflows.flows[0].tx_count == 10
        assert outflows.share_of(outflows.flows[0]) == pytest.approx(1.0)

    def test_taint_is_conserved_across_a_hop(self):
        transfers = [tx("A", chr(66 + i), 100 * (i + 1)) for i in range(4)]
        outflows = aggregate_outflows(transfers, "A")
        total = sum(0.5 * outflows.share_of(f) for f in outflows.flows)
        assert total == pytest.approx(0.5)

    def test_falls_back_to_native_amounts_without_pricing(self):
        unpriced = [
            Transfer(
                chain=Chain.ETHEREUM,
                tx_hash=f"0x{i}",
                block_number=1,
                ts=T0,
                src="A",
                dst=f"B{i}",
                asset="WEIRD",
                amount=Decimal(str(10 * (i + 1))),
                amount_usd=None,
            )
            for i in range(2)
        ]
        outflows = aggregate_outflows(unpriced, "A")
        assert outflows.weighted_by == "native_amount"
        assert outflows.share_of(outflows.flows[0]) == pytest.approx(2 / 3)

    def test_edge_taint_splits_across_transactions(self):
        transfers = [tx("A", "B", 300), tx("A", "B", 100, minutes=1)]
        flow = aggregate_outflows(transfers, "A").flows[0]
        split = split_transfer_taint(flow, 0.8)
        assert sum(split.values()) == pytest.approx(0.8)
        assert split[transfers[0].tx_hash] == pytest.approx(0.6)

    def test_self_transfers_are_ignored(self):
        outflows = aggregate_outflows([tx("A", "A", 500), tx("A", "B", 500)], "A")
        assert [f.dst for f in outflows.flows] == ["B"]


class TestPruning:
    def _node(self, **kwargs):
        from app.tracing.models import TraceNode

        node = TraceNode(chain=Chain.TRON, address="X", hop=0, taint=1.0)
        for key, value in kwargs.items():
            setattr(node, key, value)
        return node

    def test_confident_vasp_stops_the_branch(self):
        attribution = Attribution(chain=Chain.TRON, address="X")
        attribution.add(vasp_candidate())
        node = self._node(attribution=attribution.finalise())
        decision = decide(node, TraceParams(), 1)
        assert not decision.expand
        assert decision.terminal_reason == TerminalReason.VASP

    def test_low_confidence_vasp_keeps_going(self):
        attribution = Attribution(chain=Chain.TRON, address="X")
        attribution.add(vasp_candidate(confidence=0.4))
        node = self._node(attribution=attribution.finalise())
        assert decide(node, TraceParams(), 1).expand

    def test_sanctions_hit_does_not_stop_the_trace(self):
        # A sanctioned address is a critical finding, but the money usually
        # keeps moving and following it is the whole point.
        sanctioned = Candidate(
            entity_name="Sanctioned wallet",
            entity_type=EntityType.SANCTIONED,
            method=AttributionMethod.SANCTIONS_LIST,
            confidence=1.0,
            is_sanctioned=True,
        )
        attribution = Attribution(chain=Chain.TRON, address="X")
        attribution.add(sanctioned)
        node = self._node(attribution=attribution.finalise())
        assert decide(node, TraceParams(), 1).expand

    def test_mixer_stops_the_branch(self):
        mixer = Candidate(
            entity_name="Tornado Cash",
            entity_type=EntityType.MIXER,
            method=AttributionMethod.CONTRACT_REGISTRY,
            confidence=0.99,
        )
        attribution = Attribution(chain=Chain.ETHEREUM, address="X")
        attribution.add(mixer)
        node = self._node(attribution=attribution.finalise())
        decision = decide(node, TraceParams(), 1)
        assert decision.terminal_reason == TerminalReason.MIXER

    def test_hop_and_taint_limits(self):
        params = TraceParams(max_hops=3, min_taint=0.05)
        assert decide(self._node(hop=3), params, 1).terminal_reason == TerminalReason.MAX_HOPS
        assert (
            decide(self._node(taint=0.01), params, 1).terminal_reason
            == TerminalReason.MIN_TAINT
        )
        assert (
            decide(self._node(), params, 9_999).terminal_reason
            == TerminalReason.NODE_BUDGET
        )

    def test_fanout_keeps_the_biggest_and_drops_dust(self):
        transfers = [tx("A", f"B{i}", 1000 - i * 10) for i in range(40)]
        transfers += [tx("A", f"D{i}", 0.4, minutes=i) for i in range(15)]
        outflows = aggregate_outflows(transfers, "A")
        selection = select_fanout(outflows, TraceParams(max_fanout=10, min_edge_usd=1.0))
        assert len(selection.kept) == 10
        assert selection.dust_count == 15
        assert selection.kept[0].value_usd == 1000
        assert selection.truncated


class TestTraversal:
    async def _run(self, transfers, mapping=None, params=None, seed="V"):
        router = FakeRouter(transfers)
        traversal = Traversal(
            router=router,
            attribution=FakeAttribution(mapping or {}),
            params=params or TraceParams(),
        )
        return await traversal.run(Chain.TRON, seed)

    async def test_straight_line_reaches_the_exchange(self):
        transfers = [
            tx("V", "M1", 10_000),
            tx("M1", "M2", 9_800, minutes=30),
            tx("M2", "DEP", 9_600, minutes=90),
        ]
        result = await self._run(transfers, {"DEP": vasp_candidate()})
        endpoint = result.graph.nodes["DEP"]
        assert endpoint.is_terminal
        assert endpoint.terminal_reason == TerminalReason.VASP
        assert endpoint.taint == pytest.approx(1.0)
        assert endpoint.hop == 3

    async def test_trace_does_not_expand_past_a_vasp(self):
        transfers = [
            tx("V", "DEP", 5_000),
            tx("DEP", "HOT", 5_000, minutes=60),
            tx("HOT", "ELSEWHERE", 5_000, minutes=120),
        ]
        result = await self._run(transfers, {"DEP": vasp_candidate()})
        assert "HOT" not in result.graph.nodes

    async def test_split_flow_halves_the_taint(self):
        transfers = [tx("V", "A", 500), tx("V", "B", 500, minutes=1)]
        result = await self._run(transfers)
        assert result.graph.nodes["A"].taint == pytest.approx(0.5)
        assert result.graph.nodes["B"].taint == pytest.approx(0.5)

    async def test_taint_recombines_when_paths_merge(self):
        transfers = [
            tx("V", "A", 500),
            tx("V", "B", 500, minutes=1),
            tx("A", "C", 500, minutes=10),
            tx("B", "C", 500, minutes=11),
        ]
        result = await self._run(transfers)
        assert result.graph.nodes["C"].taint == pytest.approx(1.0)

    async def test_address_is_expanded_only_once(self):
        transfers = [
            tx("V", "A", 500),
            tx("V", "B", 500, minutes=1),
            tx("A", "C", 500, minutes=10),
            tx("B", "C", 500, minutes=11),
            tx("C", "D", 1000, minutes=20),
        ]
        router = FakeRouter(transfers)
        traversal = Traversal(router=router, attribution=FakeAttribution())
        result = await traversal.run(Chain.TRON, "V")
        assert result.stats.addresses_expanded == len(
            [n for n in result.graph.nodes.values() if n.expanded]
        )
        assert result.graph.nodes["C"].expanded

    async def test_address_holding_funds_is_flagged_at_rest(self):
        result = await self._run([tx("V", "REST", 4_000)])
        assert result.graph.nodes["REST"].terminal_reason == TerminalReason.NO_OUTFLOW

    async def test_hop_limit_is_respected(self):
        transfers = [tx(f"H{i}", f"H{i + 1}", 1000, minutes=i * 10) for i in range(8)]
        transfers.insert(0, tx("V", "H0", 1000))
        result = await self._run(transfers, params=TraceParams(max_hops=3))
        assert max(n.hop for n in result.graph.nodes.values()) == 3
        assert result.stats.hops_completed == 3

    async def test_low_taint_branch_is_abandoned(self):
        transfers = [tx("V", "BIG", 9_990), tx("V", "SMALL", 10, minutes=1)]
        transfers.append(tx("SMALL", "ONWARD", 10, minutes=20))
        result = await self._run(transfers, params=TraceParams(min_taint=0.05))
        assert (
            result.graph.nodes["SMALL"].terminal_reason == TerminalReason.MIN_TAINT
        )
        assert "ONWARD" not in result.graph.nodes

    async def test_progress_events_are_emitted_per_hop(self):
        events: list[dict] = []

        async def hook(payload):
            events.append(payload)

        transfers = [tx("V", "A", 100), tx("A", "B", 100, minutes=5)]
        traversal = Traversal(
            router=FakeRouter(transfers),
            attribution=FakeAttribution(),
            on_progress=hook,
        )
        await traversal.run(Chain.TRON, "V")
        assert events[0]["event"] == "started"
        assert any(e["event"] == "hop_completed" for e in events)


class TestSummary:
    async def test_summary_names_the_exchange_and_the_share(self):
        transfers = [
            tx("V", "M1", 10_000),
            tx("M1", "DEP", 8_000, minutes=30),
            tx("M1", "OTHER", 2_000, minutes=31),
        ]
        router = FakeRouter(transfers)
        traversal = Traversal(
            router=router, attribution=FakeAttribution({"DEP": vasp_candidate()})
        )
        result = await traversal.run(Chain.TRON, "V")
        result.paths = rank_paths(result.graph)
        summary = build_summary(result.graph, result.paths)

        finding = summary["attribution"]
        assert finding["found"] is True
        assert finding["vasp_slug"] == "binance"
        assert finding["taint_pct"] == pytest.approx(80.0, abs=0.1)
        assert "Binance" in finding["statement"]
        assert summary["taint"]["reached_vasps"] == pytest.approx(0.8)

    async def test_recommendations_ask_for_a_freeze_request(self):
        transfers = [tx("V", "DEP", 10_000)]
        traversal = Traversal(
            router=FakeRouter(transfers),
            attribution=FakeAttribution({"DEP": vasp_candidate()}),
        )
        result = await traversal.run(Chain.TRON, "V")
        result.paths = rank_paths(result.graph)
        recommendations = build_recommendations(
            result.graph, result.paths, TraceParams()
        )
        assert recommendations[0]["kind"] == "lea_request"
        assert recommendations[0]["urgency"] == "urgent"
        assert "Section 94 BNSS" in recommendations[0]["action"]

    async def test_recommends_a_rerun_when_the_hop_limit_bites(self):
        transfers = [tx(f"H{i}", f"H{i + 1}", 1000, minutes=i * 10) for i in range(6)]
        transfers.insert(0, tx("V", "H0", 1000))
        traversal = Traversal(
            router=FakeRouter(transfers),
            attribution=FakeAttribution(),
            params=TraceParams(max_hops=2),
        )
        result = await traversal.run(Chain.TRON, "V")
        result.paths = rank_paths(result.graph)
        recommendations = build_recommendations(
            result.graph, result.paths, TraceParams(max_hops=2)
        )
        assert any(r["kind"] == "rerun" for r in recommendations)

    def test_duration_formatting(self):
        assert humanise_duration(45) == "45s"
        assert humanise_duration(8040) == "2h 14m"
        assert humanise_duration(None) is None
