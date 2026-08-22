"""Start traces, watch them run, read the resulting graph."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, select

from app.api.deps import CurrentUser, SessionDep, audit_request
from app.api.schemas.common import Page
from app.api.schemas.trace import (
    GraphEdge,
    GraphNode,
    TraceAccepted,
    TraceGraphOut,
    TraceJobOut,
    TraceRequest,
)
from app.chains.resolver import DataRouter, detect_active_chain, resolve
from app.config import settings
from app.core.enums import Chain, JobStatus
from app.core.errors import NotFoundError
from app.db.models import TraceEdge as TraceEdgeRow
from app.db.models import TraceJob
from app.db.models import TraceNode as TraceNodeRow
from app.tracing.models import TERMINAL_EXPLANATION, TraceParams
from app.tracing.progress import is_terminal, read_events
from app.workers.runner import submit_trace

router = APIRouter(prefix="/trace", tags=["trace"])

# How often the SSE endpoint checks the progress log, and how long it waits
# before giving up on a job that has gone quiet.
STREAM_POLL_SECONDS = 0.3
STREAM_IDLE_TIMEOUT_SECONDS = 300.0
HEARTBEAT_SECONDS = 15.0


def _job_out(job: TraceJob) -> TraceJobOut:
    return TraceJobOut(
        id=job.id,
        case_id=job.case_id,
        chain=Chain(job.chain),
        seed_address=job.seed_address,
        status=JobStatus(job.status),
        params=job.params or {},
        progress=job.progress or {},
        summary=job.summary,
        data_source=job.data_source,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        duration_ms=job.duration_ms,
    )


async def _load_job(session: SessionDep, job_id: str) -> TraceJob:
    job = await session.get(TraceJob, job_id)
    if job is None:
        raise NotFoundError(f"Trace job {job_id} not found")
    return job


@router.post("", response_model=TraceAccepted, status_code=status.HTTP_202_ACCEPTED)
async def start_trace(
    payload: TraceRequest,
    session: SessionDep,
    user: CurrentUser,
    request: Request,
) -> TraceAccepted:
    """Queue a forward trace and return immediately with a job to stream."""
    resolved = resolve(payload.address, payload.chain)

    # A bare 0x address could be on any EVM chain, so find the one it is
    # actually active on rather than assuming Ethereum.
    chain = resolved.chain
    probe = None
    if payload.chain is None and resolved.ambiguous:
        probe = await detect_active_chain(DataRouter(session), resolved)
        chain = probe.chain

    params = TraceParams.from_dict(
        {
            "max_hops": payload.max_hops,
            "max_fanout": payload.max_fanout,
            "min_taint": payload.min_taint,
            "max_nodes": payload.max_nodes,
        }
    )

    from app.tracing.engine import create_job

    job = await create_job(
        session,
        chain=chain,
        seed_address=resolved.address,
        params=params,
        case_id=payload.case_id,
        requested_by_id=user.id,
    )
    await audit_request(
        session,
        request,
        user,
        action="trace.start",
        resource_type="trace_job",
        resource_id=job.id,
        details={"chain": chain.value, "address": resolved.address},
    )
    await session.commit()

    submit_trace(job.id)
    return TraceAccepted(
        job_id=job.id,
        status=JobStatus.QUEUED,
        chain=chain,
        address=resolved.address,
        stream_url=f"{settings.api_prefix}/trace/{job.id}/stream",
        backend=settings.job_backend,
        chain_detected=probe is not None,
        chain_activity=probe.activity if probe else {},
    )


@router.get("", response_model=Page[TraceJobOut])
async def list_trace_jobs(
    session: SessionDep,
    user: CurrentUser,
    case_id: str | None = None,
    job_status: JobStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=25, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[TraceJobOut]:
    query = select(TraceJob)
    if case_id:
        query = query.where(TraceJob.case_id == case_id)
    if job_status:
        query = query.where(TraceJob.status == job_status)

    total = len((await session.execute(query)).scalars().all())
    rows = (
        await session.execute(
            query.order_by(desc(TraceJob.created_at)).limit(limit).offset(offset)
        )
    ).scalars().all()
    return Page(
        items=[_job_out(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{job_id}", response_model=TraceJobOut)
async def get_trace_job(
    job_id: str, session: SessionDep, user: CurrentUser
) -> TraceJobOut:
    return _job_out(await _load_job(session, job_id))


@router.get("/{job_id}/stream")
async def stream_trace(
    job_id: str,
    session: SessionDep,
    last_event_id: int = Query(default=0, alias="from", ge=0),
) -> StreamingResponse:
    """Server-sent events for a running trace.

    Reads the progress log by sequence number, so a client that drops and
    reconnects with `?from=` replays exactly what it missed. Authentication is
    by job id rather than bearer token because `EventSource` cannot set headers;
    ids are unguessable UUIDs and the stream carries only progress counters.
    """
    await _load_job(session, job_id)

    async def event_stream() -> AsyncIterator[str]:
        cursor = last_event_id
        idle = 0.0
        since_heartbeat = 0.0

        # Replay first so a late subscriber still sees the whole trace.
        while idle < STREAM_IDLE_TIMEOUT_SECONDS:
            events = await read_events(job_id, after=cursor)
            if events:
                idle = 0.0
                since_heartbeat = 0.0
                for event in events:
                    cursor = int(event.get("seq", cursor))
                    yield f"id: {cursor}\nevent: {event.get('event', 'progress')}\ndata: {json.dumps(event)}\n\n"
                    if is_terminal(event):
                        return
            else:
                await asyncio.sleep(STREAM_POLL_SECONDS)
                idle += STREAM_POLL_SECONDS
                since_heartbeat += STREAM_POLL_SECONDS
                if since_heartbeat >= HEARTBEAT_SECONDS:
                    since_heartbeat = 0.0
                    yield ": keep-alive\n\n"

        yield f"event: timeout\ndata: {json.dumps({'job_id': job_id})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{job_id}/graph", response_model=TraceGraphOut)
async def get_trace_graph(
    job_id: str,
    session: SessionDep,
    user: CurrentUser,
    min_taint: float = Query(default=0.0, ge=0.0, le=1.0),
    primary_path_only: bool = False,
) -> TraceGraphOut:
    """Nodes and edges for the fund-flow canvas."""
    job = await _load_job(session, job_id)

    node_query = select(TraceNodeRow).where(TraceNodeRow.job_id == job_id)
    if min_taint > 0:
        node_query = node_query.where(TraceNodeRow.taint >= min_taint)
    node_rows = (await session.execute(node_query)).scalars().all()

    edge_query = select(TraceEdgeRow).where(TraceEdgeRow.job_id == job_id)
    if primary_path_only:
        edge_query = edge_query.where(TraceEdgeRow.is_on_primary_path.is_(True))
    edge_rows = (await session.execute(edge_query)).scalars().all()

    kept = {row.address for row in node_rows}
    nodes = [
        GraphNode(
            id=row.address,
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
            notes=(row.details or {}).get("notes") or [],
            first_seen=row.first_seen,
        )
        for row in node_rows
    ]
    edges = [
        GraphEdge(
            id=row.id,
            source=row.src_address,
            target=row.dst_address,
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
        for row in edge_rows
        if row.src_address in kept and row.dst_address in kept
    ]

    return TraceGraphOut(
        job_id=job_id,
        chain=Chain(job.chain),
        seed_address=job.seed_address,
        status=JobStatus(job.status),
        nodes=nodes,
        edges=edges,
        summary=job.summary,
        data_source=job.data_source,
    )
