"""Analytics for the LEA dashboard.

Everything here is derived from stored trace summaries rather than recomputed,
so the dashboard stays cheap no matter how many traces have been run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import median

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.api.schemas.analytics import (
    Bucket,
    OverviewStats,
    PerformanceStats,
    TypologyStats,
    VaspStatsOut,
)
from app.api.schemas.trace import GraphEdge, GraphNode, TraceGraphOut
from app.core.enums import (
    TYPOLOGY_LABELS,
    AlertStatus,
    AttributionMethod,
    CaseStatus,
    Chain,
    DataSource,
    JobStatus,
    Typology,
)
from app.db.models import Alert, Case, CaseAddress, TraceJob, TraceNode, Vasp
from app.db.models import TraceEdge as TraceEdgeRow
from app.tracing.models import TERMINAL_EXPLANATION
from app.tracing.paths import read_attribution, read_risk, read_stats

router = APIRouter(prefix="/analytics", tags=["analytics"])

# What a manual trace of the same case would realistically cost an investigator,
# used only as the comparison baseline on the performance panel.
MANUAL_BASELINE_HOURS = 72.0


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return float(ordered[index])


def _titleise(value: str) -> str:
    return value.replace("_", " ").capitalize()


async def _completed_jobs(session: SessionDep, days: int | None) -> list[TraceJob]:
    query = select(TraceJob).where(TraceJob.status == JobStatus.COMPLETED)
    if days:
        cutoff = func.datetime("now", f"-{days} days")
        query = query.where(TraceJob.created_at >= cutoff)
    return list((await session.execute(query)).scalars().all())


@router.get("/overview", response_model=OverviewStats)
async def overview(
    session: SessionDep,
    user: CurrentUser,
    days: int | None = Query(default=None, ge=1, le=365),
) -> OverviewStats:
    """The headline numbers: how many cases, how many attributed, how fast."""
    cases = (await session.execute(select(Case))).scalars().all()
    by_status = Counter(str(row.status) for row in cases)
    by_category = Counter(str(row.fraud_category) for row in cases)

    addresses = (
        await session.execute(select(func.count()).select_from(CaseAddress))
    ).scalar_one()
    all_jobs = (await session.execute(select(TraceJob))).scalars().all()
    completed = [job for job in all_jobs if job.status == JobStatus.COMPLETED]

    durations: list[float] = []
    hops: list[float] = []
    vasp_hits = 0
    traced_usd = 0.0
    sanctioned = 0
    recent: list[Bucket] = []

    for job in sorted(completed, key=lambda j: j.created_at, reverse=True):
        summary = job.summary or {}
        finding = read_attribution(summary)
        if job.duration_ms:
            durations.append(float(job.duration_ms))
        traced_usd += float(finding.get("value_usd") or 0.0)
        if read_risk(summary).get("sanctions_override"):
            sanctioned += 1
        if finding.get("vasp_slug"):
            vasp_hits += 1
            if finding.get("hop") is not None:
                hops.append(float(finding["hop"]))
            if len(recent) < 8:
                recent.append(
                    Bucket(
                        key=job.id,
                        label=finding.get("entity_name") or "Unknown entity",
                        count=int(finding.get("hop") or 0),
                        value_usd=float(finding.get("value_usd") or 0.0),
                        extra={
                            "address": finding.get("address"),
                            "chain": str(job.chain),
                            "confidence": float(finding.get("confidence") or 0.0),
                            "taint": float(finding.get("taint") or 0.0),
                            "case_id": job.case_id,
                        },
                    )
                )

    open_alerts = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.status.in_((AlertStatus.NEW, AlertStatus.ACKNOWLEDGED)))
        )
    ).scalar_one()
    overdue = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(
                Alert.sla_due_at.is_not(None),
                Alert.sla_due_at < func.now(),
                Alert.status.in_((AlertStatus.NEW, AlertStatus.ACKNOWLEDGED)),
            )
        )
    ).scalar_one()

    return OverviewStats(
        cases_total=len(cases),
        cases_open=by_status.get(CaseStatus.OPEN.value, 0),
        cases_attributed=by_status.get(CaseStatus.ATTRIBUTED.value, 0),
        cases_frozen=by_status.get(CaseStatus.FROZEN.value, 0),
        addresses_reported=addresses,
        traces_run=len(all_jobs),
        traces_succeeded=len(completed),
        vasps_identified=vasp_hits,
        alerts_open=open_alerts,
        alerts_overdue=overdue,
        total_loss_inr=sum(row.loss_amount_inr or 0.0 for row in cases),
        traced_value_usd=traced_usd,
        attribution_rate=(vasp_hits / len(completed)) if completed else 0.0,
        median_trace_ms=float(median(durations)) if durations else 0.0,
        median_hops_to_vasp=float(median(hops)) if hops else 0.0,
        sanctioned_exposure_count=sanctioned,
        cases_by_status=[
            Bucket(key=key, label=_titleise(key), count=value)
            for key, value in by_status.most_common()
        ],
        cases_by_category=[
            Bucket(key=key, label=_titleise(key), count=value)
            for key, value in by_category.most_common()
        ],
        recent_attributions=recent,
    )


@router.get("/typologies", response_model=TypologyStats)
async def typologies(
    session: SessionDep,
    user: CurrentUser,
) -> TypologyStats:
    """Which laundering patterns are actually showing up in the caseload."""
    rows = (
        await session.execute(
            select(TraceNode.chain, TraceNode.typologies).where(
                TraceNode.typologies.is_not(None)
            )
        )
    ).all()

    counts: Counter[str] = Counter()
    per_chain: dict[str, Counter[str]] = defaultdict(Counter)
    for chain, entries in rows:
        for entry in entries or []:
            name = entry.get("typology") if isinstance(entry, dict) else str(entry)
            if not name:
                continue
            counts[name] += 1
            per_chain[str(chain)][name] += 1

    # Job summaries carry the detector output even when a node was not tagged.
    for job in (
        await session.execute(select(TraceJob).where(TraceJob.status == JobStatus.COMPLETED))
    ).scalars().all():
        for entry in (job.summary or {}).get("typologies") or []:
            name = entry.get("typology") if isinstance(entry, dict) else str(entry)
            if name:
                counts[name] += 1
                per_chain[str(job.chain)][name] += 1

    def label_for(name: str) -> str:
        try:
            return TYPOLOGY_LABELS[Typology(name)]
        except ValueError:
            return _titleise(name)

    return TypologyStats(
        distribution=[
            Bucket(key=name, label=label_for(name), count=count)
            for name, count in counts.most_common()
        ],
        by_chain=[
            Bucket(
                key=chain,
                label=Chain(chain).display_name if chain in Chain.__members__.values() else chain,
                count=sum(chain_counts.values()),
                extra={name: float(value) for name, value in chain_counts.items()},
            )
            for chain, chain_counts in per_chain.items()
        ],
        total_hits=sum(counts.values()),
    )


@router.get("/vasps", response_model=VaspStatsOut)
async def vasp_analytics(session: SessionDep, user: CurrentUser) -> VaspStatsOut:
    """Which exchanges are receiving traced illicit funds, and where they sit."""
    jobs = (
        await session.execute(
            select(TraceJob).where(TraceJob.status == JobStatus.COMPLETED)
        )
    ).scalars().all()

    hits: Counter[str] = Counter()
    value: dict[str, float] = defaultdict(float)
    for job in jobs:
        finding = read_attribution(job.summary)
        slug = finding.get("vasp_slug")
        if not slug:
            continue
        hits[slug] += 1
        value[slug] += float(finding.get("value_usd") or 0.0)

    vasps = {
        row.slug: row
        for row in (await session.execute(select(Vasp))).scalars().all()
    }
    top = [
        Bucket(
            key=slug,
            label=vasps[slug].name if slug in vasps else slug,
            count=count,
            value_usd=round(value[slug], 2),
            extra={
                "jurisdiction": vasps[slug].jurisdiction if slug in vasps else None,
                "is_indian_entity": str(bool(slug in vasps and vasps[slug].is_indian_entity)),
                "fiu_ind_registered": str(
                    bool(slug in vasps and vasps[slug].fiu_ind_registered)
                ),
                "response_sla_hours": float(
                    vasps[slug].response_sla_hours if slug in vasps else 72
                ),
            },
        )
        for slug, count in hits.most_common(15)
    ]

    jurisdictions: Counter[str] = Counter()
    for slug, count in hits.items():
        jurisdictions[vasps[slug].jurisdiction if slug in vasps else "Unknown"] += count

    total_hits = sum(hits.values())
    indian = sum(
        count for slug, count in hits.items() if slug in vasps and vasps[slug].is_indian_entity
    )
    registered = sum(
        count
        for slug, count in hits.items()
        if slug in vasps and vasps[slug].fiu_ind_registered
    )
    return VaspStatsOut(
        top_receiving=top,
        by_jurisdiction=[
            Bucket(key=name, label=name, count=count)
            for name, count in jurisdictions.most_common()
        ],
        indian_share_pct=round(100 * indian / total_hits, 1) if total_hits else 0.0,
        fiu_registered_share_pct=(
            round(100 * registered / total_hits, 1) if total_hits else 0.0
        ),
    )


@router.get("/performance", response_model=PerformanceStats)
async def performance(session: SessionDep, user: CurrentUser) -> PerformanceStats:
    """Speed and reliability of the tracing pipeline itself."""
    jobs = (await session.execute(select(TraceJob))).scalars().all()
    completed = [job for job in jobs if job.status == JobStatus.COMPLETED]

    durations = [float(job.duration_ms) for job in completed if job.duration_ms]
    examined: list[float] = []
    upstream: list[float] = []
    by_method: Counter[str] = Counter()
    by_chain: Counter[str] = Counter()
    by_source: Counter[str] = Counter()
    time_to_attribution: list[float] = []

    for job in completed:
        stats = read_stats(job.summary)
        finding = read_attribution(job.summary)
        examined.append(float(stats.get("addresses_examined") or 0))
        upstream.append(float(stats.get("upstream_calls") or 0))
        by_chain[str(job.chain)] += 1
        by_source[str(job.data_source)] += 1
        if finding.get("method"):
            by_method[str(finding["method"])] += 1
        if finding.get("vasp_slug") and job.duration_ms:
            time_to_attribution.append(job.duration_ms / 1000.0)

    def method_label(name: str) -> str:
        try:
            return _titleise(AttributionMethod(name).value)
        except ValueError:
            return _titleise(name)

    return PerformanceStats(
        traces_total=len(jobs),
        success_rate=(len(completed) / len(jobs)) if jobs else 0.0,
        median_trace_ms=float(median(durations)) if durations else 0.0,
        p95_trace_ms=_percentile(durations, 0.95),
        mean_addresses_examined=(sum(examined) / len(examined)) if examined else 0.0,
        mean_upstream_calls=(sum(upstream) / len(upstream)) if upstream else 0.0,
        by_method=[
            Bucket(key=key, label=method_label(key), count=count)
            for key, count in by_method.most_common()
        ],
        by_chain=[
            Bucket(key=key, label=Chain(key).display_name, count=count)
            for key, count in by_chain.most_common()
        ],
        by_data_source=[
            Bucket(key=key, label=_titleise(key), count=count)
            for key, count in by_source.most_common()
        ],
        manual_baseline_hours=MANUAL_BASELINE_HOURS,
        median_time_to_attribution_seconds=(
            float(median(time_to_attribution)) if time_to_attribution else 0.0
        ),
    )


@router.get("/chains", response_model=list[Bucket])
async def chain_stats(session: SessionDep, user: CurrentUser) -> list[Bucket]:
    """Cases and traced value broken down by chain — used by the dashboard chart."""
    jobs = (
        await session.execute(
            select(TraceJob).where(TraceJob.status == JobStatus.COMPLETED)
        )
    ).scalars().all()
    counts: Counter[str] = Counter()
    value: dict[str, float] = defaultdict(float)
    for job in jobs:
        counts[str(job.chain)] += 1
        value[str(job.chain)] += float(read_attribution(job.summary).get("value_usd") or 0.0)

    address_counts = dict(
        (
            await session.execute(
                select(CaseAddress.chain, func.count()).group_by(CaseAddress.chain)
            )
        ).all()
    )
    keys = set(counts) | {str(k) for k in address_counts}
    return [
        Bucket(
            key=chain,
            label=Chain(chain).display_name if chain in {c.value for c in Chain} else chain,
            count=counts.get(chain, 0),
            value_usd=round(value.get(chain, 0.0), 2),
            extra={"reported_addresses": float(address_counts.get(chain, 0))},
        )
        for chain in sorted(keys, key=lambda c: counts.get(c, 0), reverse=True)
    ]


@router.get("/network-graph", response_model=TraceGraphOut)
async def network_graph(
    session: SessionDep,
    user: CurrentUser,
    limit: int = Query(default=400, ge=10, le=2000),
    min_taint: float = Query(default=0.02, ge=0.0, le=1.0),
    days: int | None = Query(default=None, ge=1, le=365),
) -> TraceGraphOut:
    """Every completed trace in the system, merged onto one canvas.

    Each case is traced in isolation, so a single-case view can never show
    two unrelated-looking complaints converging on the same deposit address
    - exactly the signal that turns isolated reports into a fraud ring.
    Capped to the highest-taint addresses so the canvas stays legible.
    """
    query = select(TraceJob).where(TraceJob.status == JobStatus.COMPLETED)
    if days:
        cutoff = func.datetime("now", f"-{days} days")
        query = query.where(TraceJob.created_at >= cutoff)
    jobs = (await session.execute(query)).scalars().all()
    if not jobs:
        return TraceGraphOut(
            job_id="",
            chain=Chain.TRON,
            seed_address="",
            status=JobStatus.COMPLETED,
            nodes=[],
            edges=[],
            summary={"merged_jobs": 0, "cross_case_addresses": 0},
            data_source=DataSource.SYNTHETIC,
        )

    job_ids = [job.id for job in jobs]
    # Only a real case id counts toward the cross-case signal - an address hit
    # by several ad-hoc traces (no case attached) is not evidence of a ring,
    # just the same lead re-run.
    job_case: dict[str, str | None] = {job.id: job.case_id for job in jobs}

    node_rows = (
        await session.execute(
            select(TraceNode).where(
                TraceNode.job_id.in_(job_ids), TraceNode.taint >= min_taint
            )
        )
    ).scalars().all()

    # Keep the strongest observation of each address, and every distinct
    # case that reached it - an address touched by more than one case is
    # the cross-case signal this view exists to surface.
    best: dict[str, TraceNode] = {}
    cases_by_key: dict[str, set[str]] = defaultdict(set)
    for row in node_rows:
        key = f"{row.chain}:{row.address}"
        case_id = job_case[row.job_id]
        if case_id:
            cases_by_key[key].add(case_id)
        current = best.get(key)
        if current is None or row.taint > current.taint:
            best[key] = row

    ranked = sorted(best.items(), key=lambda kv: kv[1].taint, reverse=True)[:limit]
    best = dict(ranked)
    present = set(best)
    cross_case = {key for key in present if len(cases_by_key[key]) > 1}

    nodes = []
    for key, row in best.items():
        notes = list((row.details or {}).get("notes") or [])
        if key in cross_case:
            notes.append(
                f"Reached by {len(cases_by_key[key])} separate cases - "
                "possible shared fraud infrastructure"
            )
        nodes.append(
            GraphNode(
                id=key,
                address=row.address,
                chain=Chain(row.chain),
                hop=row.hop,
                taint=row.taint,
                entity_type=str(row.entity_type),
                entity_name=row.entity_name,
                vasp_slug=row.vasp_slug,
                attribution_method=row.attribution_method,
                attribution_confidence=row.attribution_confidence,
                risk_score=row.risk_score,
                risk_level=str(row.risk_level),
                typologies=row.typologies or [],
                value_in_usd=row.value_in_usd,
                value_out_usd=row.value_out_usd,
                is_seed=row.is_seed,
                is_terminal=row.is_terminal,
                is_sanctioned=bool((row.details or {}).get("is_sanctioned")),
                terminal_reason=(row.details or {}).get("terminal_reason"),
                terminal_explanation=TERMINAL_EXPLANATION.get(
                    (row.details or {}).get("terminal_reason") or ""
                ),
                notes=notes,
                first_seen=row.first_seen,
            )
        )

    edge_rows = (
        await session.execute(select(TraceEdgeRow).where(TraceEdgeRow.job_id.in_(job_ids)))
    ).scalars().all()
    seen_edges: set[str] = set()
    edges: list[GraphEdge] = []
    for row in edge_rows:
        source = f"{row.chain}:{row.src_address}"
        target = f"{row.chain}:{row.dst_address}"
        if source not in present or target not in present:
            continue
        edge_key = f"{row.tx_hash}:{source}:{target}"
        if edge_key in seen_edges:
            continue
        seen_edges.add(edge_key)
        edges.append(
            GraphEdge(
                id=row.id,
                source=source,
                target=target,
                chain=Chain(row.chain),
                tx_hash=row.tx_hash,
                ts=row.ts,
                block_number=row.block_number,
                asset=row.asset,
                amount=str(row.amount),
                amount_usd=row.amount_usd,
                hop=row.hop,
                taint_share=row.taint_share,
                is_on_primary_path=row.is_on_primary_path,
                edge_kind=row.edge_kind,
            )
        )

    return TraceGraphOut(
        job_id=job_ids[-1],
        chain=Chain(jobs[0].chain),
        seed_address="",
        status=JobStatus.COMPLETED,
        nodes=nodes,
        edges=edges,
        summary={
            "merged_jobs": len(job_ids),
            "total_addresses": len(best),
            "cross_case_addresses": len(cross_case),
        },
        data_source=jobs[-1].data_source,
    )
