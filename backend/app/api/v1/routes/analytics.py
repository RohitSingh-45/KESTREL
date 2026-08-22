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
from app.core.enums import (
    TYPOLOGY_LABELS,
    AlertStatus,
    AttributionMethod,
    CaseStatus,
    Chain,
    JobStatus,
    Typology,
)
from app.db.models import Alert, Case, CaseAddress, TraceJob, TraceNode, Vasp
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
