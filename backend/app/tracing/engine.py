"""Runs a trace job end to end and writes the result down.

Separated from `traversal.py` on purpose: the traversal is pure graph walking and
is unit-testable without a database, while everything here is about job state,
persistence and progress reporting.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.attribution.service import AttributionService
from app.chains.resolver import DataRouter
from app.core.enums import Chain, DataSource, JobStatus
from app.core.errors import NotFoundError
from app.db.models import Address, TraceJob
from app.db.models import CrossChainLink as CrossChainLinkRow
from app.db.models import TraceEdge as TraceEdgeRow
from app.db.models import TraceNode as TraceNodeRow
from app.logging_config import get_logger
from app.risk.alerts import generate_alerts
from app.risk.scorer import RECOMMENDED_ACTION, apply_scores
from app.risk.typologies import annotate_nodes, detect_all
from app.tracing.crosschain import CrossChainCorrelator
from app.tracing.crosschain import CrossChainLink as CrossChainLinkResult
from app.tracing.models import TraceEdge, TraceParams, TraceResult
from app.tracing.paths import build_summary, mark_primary_path, rank_paths
from app.tracing.progress import ProgressReporter
from app.tracing.recommendations import build_recommendations
from app.tracing.traversal import Traversal

logger = get_logger(__name__)

# Only follow a bridge hop onward when the correlation is strong enough to be
# worth an investigator's time, and never chase more than a couple of them.
BRIDGE_FOLLOW_CONFIDENCE = 0.5
MAX_BRIDGE_CONTINUATIONS = 2


async def create_job(
    session: AsyncSession,
    *,
    chain: Chain,
    seed_address: str,
    params: TraceParams,
    case_id: str | None = None,
    requested_by_id: str | None = None,
) -> TraceJob:
    job = TraceJob(
        case_id=case_id,
        chain=chain,
        seed_address=seed_address,
        status=JobStatus.QUEUED,
        params=params.to_dict(),
        progress={"event": "queued", "hop": 0, "addresses_examined": 0},
        requested_by_id=requested_by_id,
    )
    session.add(job)
    await session.flush()
    return job


class TraceEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(self, job_id: str) -> TraceResult:
        job = await self.session.get(TraceJob, job_id)
        if job is None:
            raise NotFoundError(f"Trace job {job_id} not found")

        reporter = ProgressReporter(job_id)
        params = TraceParams.from_dict(job.params)
        chain = Chain(job.chain)

        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        job.error = None
        await self.session.commit()

        router = DataRouter(self.session)
        attribution = AttributionService(self.session, router)

        async def on_progress(payload: dict) -> None:
            await reporter.emit(payload)
            # Mirror the latest event onto the job row so a client that polls
            # instead of streaming still sees live progress.
            job.progress = payload
            await self.session.merge(job)

        traversal = Traversal(
            router=router,
            attribution=attribution,
            params=params,
            on_progress=on_progress,
        )

        try:
            result = await traversal.run(chain, job.seed_address)
        except Exception as exc:  # noqa: BLE001 - job failure must be recorded
            logger.exception("trace_failed", job_id=job_id)
            job.status = JobStatus.FAILED
            job.error = str(exc)[:1000]
            job.finished_at = datetime.now(UTC)
            await self.session.commit()
            await reporter.failed(str(exc))
            raise

        # --- interpretation: patterns, risk, cross-chain, alerts -----------
        await on_progress({"event": "analysing", "hop": result.stats.hops_completed})

        hits = detect_all(result.graph)
        annotate_nodes(result.graph, hits)
        risk = apply_scores(result.graph, hits)

        links = await CrossChainCorrelator(self.session, router).run(result.graph)
        if links:
            await self._follow_across_bridges(result, links, router, attribution, params)
            # Patterns and risk are recomputed over the extended graph.
            hits = detect_all(result.graph)
            annotate_nodes(result.graph, hits)
            risk = apply_scores(result.graph, hits)

        result.paths = rank_paths(result.graph)
        mark_primary_path(result.graph, result.primary_path)
        result.summary = build_summary(result.graph, result.paths)
        result.summary["typologies"] = [hit.to_dict() for hit in hits]
        result.summary["risk"] = {
            **risk.to_dict(),
            "recommended_action": RECOMMENDED_ACTION[risk.level],
        }
        result.summary["cross_chain_links"] = [link.to_dict() for link in links]
        result.summary["recommendations"] = build_recommendations(
            result.graph, result.paths, params
        )
        result.summary["stats"] = result.stats.to_dict()

        await self._persist(job, result)
        await self._persist_cross_chain(job, links)
        alerts = await generate_alerts(
            self.session,
            result.graph,
            hits,
            risk,
            links,
            case_id=job.case_id,
            job_id=job.id,
        )
        result.summary["alerts_raised"] = len(alerts)
        for node in result.graph.nodes.values():
            if node.attribution:
                await attribution.persist(node.attribution)

        job.status = JobStatus.COMPLETED
        job.finished_at = datetime.now(UTC)
        job.duration_ms = result.stats.duration_ms
        job.data_source = result.stats.data_source
        job.summary = result.summary
        job.progress = {
            "event": "completed",
            "hop": result.stats.hops_completed,
            "addresses_examined": result.stats.addresses_examined,
            "vasp_hits": result.summary["counts"]["vasp_endpoints"],
        }
        await self.session.commit()
        await reporter.completed(result.summary)

        logger.info(
            "trace_completed",
            job_id=job_id,
            chain=chain.value,
            addresses=result.stats.addresses_examined,
            edges=result.stats.edges_kept,
            duration_ms=result.stats.duration_ms,
            vasp_hits=result.summary["counts"]["vasp_endpoints"],
        )
        return result

    async def _follow_across_bridges(
        self,
        result: TraceResult,
        links: list[CrossChainLinkResult],
        router: DataRouter,
        attribution: AttributionService,
        params: TraceParams,
    ) -> None:
        """Resume tracing on the destination chain after a confident bridge match.

        Detecting a bridge hop is only half an answer; the money kept going and
        the exchange it reached is on the other chain. Continuations are limited
        to the strongest links and given a shorter hop budget, and everything
        they discover is marked as resting on an inference.
        """
        followed = 0
        for link in links:
            if followed >= MAX_BRIDGE_CONTINUATIONS:
                break
            if not link.matched or link.confidence < BRIDGE_FOLLOW_CONFIDENCE:
                continue
            if not link.dst_address or link.dst_address in result.graph.nodes:
                continue

            source = result.graph.nodes.get(link.src_address)
            if source is None:
                continue

            continuation = TraceParams.from_dict(params.to_dict())
            continuation.max_hops = max(1, params.max_hops - source.hop - 1)

            sub = Traversal(
                router=router, attribution=attribution, params=continuation
            )
            subgraph_result = await sub.run(link.dst_chain, link.dst_address)

            bridge_edge = TraceEdge(
                chain=link.dst_chain,
                src=link.src_address,
                dst=link.dst_address,
                tx_hash=link.dst_tx_hash or "",
                ts=link.dst_ts or link.src_ts,
                block_number=0,
                asset="",
                amount=Decimal("0"),
                amount_usd=link.value_usd,
                hop=source.hop,
                taint_share=link.taint,
                edge_kind="bridge_inferred",
            )
            result.graph.merge(
                subgraph_result.graph,
                via=bridge_edge,
                hop_offset=source.hop + 1,
                taint_scale=source.taint * link.confidence,
            )
            result.stats.addresses_examined = len(result.graph.nodes)
            result.stats.edges_kept = len(result.graph.edges)
            result.stats.upstream_calls += sub.stats.upstream_calls
            followed += 1

            logger.info(
                "bridge_continuation_traced",
                bridge=link.bridge_name,
                dst_chain=link.dst_chain.value,
                confidence=link.confidence,
                added_nodes=len(subgraph_result.graph.nodes),
            )

    async def _persist(self, job: TraceJob, result: TraceResult) -> None:
        """Replace any previous graph for this job with the new one."""
        await self.session.execute(
            delete(TraceNodeRow).where(TraceNodeRow.job_id == job.id)
        )
        await self.session.execute(
            delete(TraceEdgeRow).where(TraceEdgeRow.job_id == job.id)
        )

        for node in result.graph.nodes.values():
            best = node.attribution.best if node.attribution else None
            self.session.add(
                TraceNodeRow(
                    job_id=job.id,
                    chain=node.chain,
                    address=node.address,
                    hop=node.hop,
                    taint=node.taint,
                    value_in_usd=node.value_in_usd,
                    value_out_usd=node.value_out_usd,
                    entity_type=node.entity_type,
                    entity_name=node.entity_name,
                    vasp_slug=node.vasp_slug,
                    attribution_method=best.method.value if best else None,
                    attribution_confidence=node.attribution_confidence,
                    risk_score=node.risk_score,
                    risk_level=node.risk_level,
                    typologies=node.typologies or None,
                    is_terminal=node.is_terminal,
                    is_seed=node.is_seed,
                    first_seen=node.first_seen,
                    details={
                        "terminal_reason": node.terminal_reason,
                        "notes": node.notes,
                        "in_degree": node.observed_in_degree,
                        "out_degree": node.observed_out_degree,
                        "is_sanctioned": node.is_sanctioned,
                    },
                )
            )

        for edge in result.graph.edges.values():
            self.session.add(
                TraceEdgeRow(
                    job_id=job.id,
                    chain=edge.chain,
                    src_address=edge.src,
                    dst_address=edge.dst,
                    tx_hash=edge.tx_hash,
                    ts=edge.ts,
                    block_number=edge.block_number,
                    asset=edge.asset,
                    amount=edge.amount,
                    amount_usd=edge.amount_usd,
                    hop=edge.hop,
                    taint_share=edge.taint_share,
                    is_on_primary_path=edge.is_on_primary_path,
                    edge_kind=edge.edge_kind,
                )
            )

        await self._upsert_addresses(result)
        await self.session.flush()

    async def _persist_cross_chain(
        self, job: TraceJob, links: list[CrossChainLinkResult]
    ) -> None:
        await self.session.execute(
            delete(CrossChainLinkRow).where(CrossChainLinkRow.job_id == job.id)
        )
        for link in links:
            # Unmatched destinations are useful context in the UI but are not
            # evidence of anything, so they are not stored as links.
            if not link.matched:
                continue
            self.session.add(
                CrossChainLinkRow(
                    job_id=job.id,
                    bridge_name=link.bridge_name,
                    src_chain=link.src_chain,
                    src_address=link.src_address,
                    src_tx_hash=link.src_tx_hash,
                    src_ts=link.src_ts,
                    dst_chain=link.dst_chain,
                    dst_address=link.dst_address,
                    dst_tx_hash=link.dst_tx_hash,
                    dst_ts=link.dst_ts,
                    value_usd=link.value_usd,
                    value_delta_pct=link.value_delta_pct,
                    time_delta_seconds=link.time_delta_seconds,
                    confidence=link.confidence,
                    is_inferred=True,
                    evidence=link.evidence,
                )
            )
        await self.session.flush()

    async def _upsert_addresses(self, result: TraceResult) -> None:
        """Keep the durable address profile table in step with what we just learned."""
        addresses = list(result.graph.nodes)
        if not addresses:
            return
        # A graph that crossed a bridge holds nodes on more than one chain, so
        # identity here is (chain, address) - the same string can exist on two
        # EVM chains as entirely different accounts.
        existing_rows = (
            await self.session.execute(
                select(Address).where(Address.address.in_(addresses))
            )
        ).scalars().all()
        by_address = {(row.chain, row.address): row for row in existing_rows}
        now = datetime.now(UTC)

        for node in result.graph.nodes.values():
            row = by_address.get((node.chain, node.address))
            if row is None:
                row = Address(chain=node.chain, address=node.address)
                self.session.add(row)
            row.entity_type = node.entity_type
            row.entity_name = node.entity_name
            row.in_degree = max(row.in_degree or 0, node.observed_in_degree)
            row.out_degree = max(row.out_degree or 0, node.observed_out_degree)
            row.total_received_usd = max(row.total_received_usd or 0.0, node.value_in_usd)
            row.total_sent_usd = max(row.total_sent_usd or 0.0, node.value_out_usd)
            row.risk_score = max(row.risk_score or 0.0, node.risk_score)
            row.risk_level = node.risk_level
            row.typologies = node.typologies or None
            row.data_source = result.stats.data_source or DataSource.SYNTHETIC
            row.profiled_at = now
            if node.first_seen and (row.first_seen is None or node.first_seen < row.first_seen):
                row.first_seen = node.first_seen
            if node.last_seen and (row.last_seen is None or node.last_seen > row.last_seen):
                row.last_seen = node.last_seen
