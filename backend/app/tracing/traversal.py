"""Forward breadth-first traversal from the victim-reported address.

This is the heart of the system. Everything else - risk scoring, cross-chain
correlation, reports, dashboards - decorates the result of this walk.

The traversal is level-synchronous rather than a plain queue: all nodes at hop N
are discovered, then attributed as one batch, then expanded together. Two things
fall out of that. Taint arriving at an address along several different routes is
summed *before* the address is expanded, so its onward share is correct the first
time and no node is ever expanded twice. And attribution runs on a whole hop at
once, which lets the label store prefetch the level in a single query instead of
one round trip per address.

Every node is attributed *before* it is expanded, because the answer to "did the
money reach an exchange" has to be checked at arrival - expanding past a VASP
deposit address would follow the exchange's own internal sweeps and produce
nonsense.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.attribution.service import AttributionContext, AttributionService
from app.chains.addresses import normalise_address
from app.chains.base import Transfer
from app.chains.resolver import DataRouter
from app.core.enums import Chain, Direction
from app.core.errors import UnsupportedChainError, UpstreamError
from app.logging_config import get_logger
from app.tracing.models import (
    TerminalReason,
    TraceEdge,
    TraceGraph,
    TraceNode,
    TraceParams,
    TraceResult,
    TraceStats,
)
from app.tracing.pruning import decide, select_fanout
from app.tracing.taint import aggregate_outflows, split_transfer_taint

logger = get_logger(__name__)

ProgressHook = Callable[[dict], Awaitable[None]]


class Traversal:
    def __init__(
        self,
        *,
        router: DataRouter,
        attribution: AttributionService,
        params: TraceParams | None = None,
        on_progress: ProgressHook | None = None,
    ) -> None:
        self.router = router
        self.attribution = attribution
        self.params = params or TraceParams()
        self.on_progress = on_progress
        self.stats = TraceStats()

    async def _emit(self, event: str, **payload) -> None:
        if self.on_progress is None:
            return
        await self.on_progress({"event": event, **payload})

    async def _fetch_transfers(
        self, chain: Chain, address: str, direction: Direction
    ) -> tuple[list[Transfer], str | None]:
        try:
            page = await self.router.get_transfers(
                chain, address, direction, limit=self.params.page_size
            )
        except (UpstreamError, UnsupportedChainError) as exc:
            logger.warning(
                "trace_fetch_failed", address=address[:16], error=str(exc)[:200]
            )
            return [], str(exc)
        self.stats.upstream_calls += 1
        self.stats.transfers_examined += len(page.transfers)
        return page.transfers, None

    async def run(self, chain: Chain, seed_address: str) -> TraceResult:
        started = time.perf_counter()
        seed_address = normalise_address(seed_address, chain)
        graph = TraceGraph(chain=chain, seed=seed_address)

        seed = graph.node(seed_address, hop=0)
        seed.is_seed = True
        seed.taint = 1.0

        frontier: list[TraceNode] = [seed]
        await self._emit(
            "started",
            chain=chain.value,
            seed=seed_address,
            params=self.params.to_dict(),
        )

        for hop in range(self.params.max_hops + 1):
            if not frontier:
                break

            await self._attribute_level(chain, frontier)
            self.stats.addresses_examined = len(graph.nodes)

            expandable: list[TraceNode] = []
            for node in frontier:
                decision = decide(node, self.params, len(graph.nodes))
                if decision.expand:
                    expandable.append(node)
                    continue
                node.is_terminal = True
                node.terminal_reason = decision.terminal_reason
                if decision.note:
                    node.notes.append(decision.note)

            await self._emit(
                "hop_started",
                hop=hop,
                frontier=len(frontier),
                expanding=len(expandable),
                addresses_examined=self.stats.addresses_examined,
                vasp_hits=len(graph.vasp_endpoints()),
            )

            next_taint: dict[str, float] = {}
            for node in expandable:
                await self._expand(graph, node, hop, next_taint)

            self.stats.hops_completed = hop
            frontier = self._build_frontier(graph, next_taint, hop + 1)

            await self._emit(
                "hop_completed",
                hop=hop,
                addresses_examined=len(graph.nodes),
                edges=len(graph.edges),
                next_frontier=len(frontier),
                vasp_hits=len(graph.vasp_endpoints()),
            )

            if hop >= self.params.max_hops:
                break

        # Anything still unexplored when the loop ends is a hop-limit stop.
        for node in frontier:
            if not node.is_terminal:
                node.is_terminal = True
                node.terminal_reason = TerminalReason.MAX_HOPS

        self.stats.edges_kept = len(graph.edges)
        self.stats.duration_ms = int((time.perf_counter() - started) * 1000)
        self.stats.data_source = self.router.effective_source
        self.stats.notes = self.router.stats()["notes"]
        self.stats.attribution = self.attribution.stats()

        return TraceResult(graph=graph, params=self.params, stats=self.stats)

    async def _attribute_level(self, chain: Chain, level: list[TraceNode]) -> None:
        """Attribute a whole hop, warming the label cache in one query first."""
        pending = [n for n in level if n.attribution is None]
        if not pending:
            return
        await self.attribution.prefetch_labels(chain, [n.address for n in pending])
        for node in pending:
            # The seed is the victim's own reported address, so a behavioural
            # guess about it is noise; labels still apply.
            context = AttributionContext(allow_fetch=not node.is_seed)
            node.attribution = await self.attribution.identify(
                chain, node.address, context
            )

    async def _expand(
        self,
        graph: TraceGraph,
        node: TraceNode,
        hop: int,
        next_taint: dict[str, float],
    ) -> None:
        transfers, error = await self._fetch_transfers(
            node.chain, node.address, Direction.OUT
        )
        node.expanded = True
        self.stats.addresses_expanded += 1

        if error:
            node.is_terminal = True
            node.terminal_reason = TerminalReason.FETCH_FAILED
            node.notes.append(error[:200])
            return

        outflows = aggregate_outflows(transfers, node.address)
        if not outflows.flows:
            node.is_terminal = True
            node.terminal_reason = TerminalReason.NO_OUTFLOW
            return

        node.value_out_usd = outflows.total_usd
        node.observed_out_degree = outflows.counterparties
        if outflows.weighted_by != "usd":
            node.notes.append(
                "no USD pricing available; shares computed from native amounts"
            )

        selection = select_fanout(outflows, self.params)
        if selection.truncated:
            node.notes.append(
                f"{selection.dropped_count} lower-value counterparties "
                f"({selection.dropped_usd:,.0f} USD) not followed"
            )
        if selection.dust_count:
            node.notes.append(
                f"{selection.dust_count} dust transfers below "
                f"{self.params.min_edge_usd:,.0f} USD ignored"
            )

        for flow in selection.kept:
            flow_taint = node.taint * outflows.share_of(flow)
            per_tx = split_transfer_taint(flow, flow_taint)
            child = graph.node(flow.dst, hop=hop + 1)
            child.observed_in_degree += 1
            child.value_in_usd += flow.value_usd

            best_edge: TraceEdge | None = None
            for transfer in flow.transfers[: self.params.max_tx_per_edge]:
                edge = graph.add_edge(
                    TraceEdge(
                        chain=transfer.chain,
                        src=node.address,
                        dst=flow.dst,
                        tx_hash=transfer.tx_hash,
                        ts=transfer.ts,
                        block_number=transfer.block_number,
                        asset=transfer.asset,
                        amount=transfer.amount,
                        amount_usd=transfer.usd,
                        hop=hop,
                        taint_share=per_tx.get(transfer.tx_hash, 0.0),
                    )
                )
                node.observe(transfer.ts)
                child.observe(transfer.ts)
                if best_edge is None or edge.taint_share > best_edge.taint_share:
                    best_edge = edge

            if best_edge is not None and (
                child.best_parent is None or flow_taint > child.taint
            ):
                child.best_parent = best_edge

            next_taint[flow.dst] = next_taint.get(flow.dst, 0.0) + flow_taint

    def _build_frontier(
        self, graph: TraceGraph, next_taint: dict[str, float], hop: int
    ) -> list[TraceNode]:
        """Commit the hop's accumulated taint and return the nodes to process next."""
        frontier: list[TraceNode] = []
        for address, taint in next_taint.items():
            node = graph.nodes.get(address)
            if node is None:
                continue
            node.taint += taint
            # Already expanded at an earlier hop: extra taint is recorded on the
            # node but the branch is not walked again.
            if node.expanded:
                node.notes.append(
                    f"reached again at hop {hop} with an extra {taint:.2%} of "
                    "traced value; not re-expanded"
                )
                continue
            if node not in frontier:
                frontier.append(node)
        frontier.sort(key=lambda n: n.taint, reverse=True)
        return frontier


def seed_first_movement(graph: TraceGraph) -> datetime | None:
    """When the victim's funds first left the reported address."""
    edges = graph.out_edges.get(graph.seed) or []
    return min((edge.ts for edge in edges), default=None)
