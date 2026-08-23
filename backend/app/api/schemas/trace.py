"""Schemas for starting traces and reading their results."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.enums import Chain, DataSource, JobStatus


class TraceRequest(BaseModel):
    address: str = Field(min_length=8, max_length=128)
    chain: Chain | None = Field(
        default=None, description="Omit to auto-detect from the address format"
    )
    case_id: str | None = None
    max_hops: int | None = Field(default=None, ge=1, le=8)
    max_fanout: int | None = Field(default=None, ge=1, le=100)
    min_taint: float | None = Field(default=None, ge=0.0, le=1.0)
    max_nodes: int | None = Field(default=None, ge=10, le=10_000)


class TraceJobOut(BaseModel):
    id: str
    case_id: str | None = None
    chain: Chain
    seed_address: str
    status: JobStatus
    params: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] | None = None
    data_source: DataSource = DataSource.SYNTHETIC
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class TraceAccepted(BaseModel):
    job_id: str
    status: JobStatus
    chain: Chain
    address: str
    stream_url: str
    backend: str
    chain_detected: bool = False
    chain_activity: dict[str, int] = Field(
        default_factory=dict,
        description="Transaction counts per candidate chain when the address format was ambiguous",
    )


class GraphNode(BaseModel):
    id: str
    address: str
    chain: Chain
    hop: int
    taint: float
    entity_type: str
    entity_name: str | None = None
    vasp_slug: str | None = None
    attribution_method: str | None = None
    attribution_confidence: float = 0.0
    risk_score: float = 0.0
    risk_level: str = "low"
    typologies: list[str] = Field(default_factory=list)
    value_in_usd: float = 0.0
    value_out_usd: float = 0.0
    is_seed: bool = False
    is_terminal: bool = False
    is_sanctioned: bool = False
    terminal_reason: str | None = None
    terminal_explanation: str | None = None
    notes: list[str] = Field(default_factory=list)
    first_seen: datetime | None = None


class GraphEdge(BaseModel):
    id: str
    source: str
    target: str
    chain: Chain
    tx_hash: str
    ts: datetime
    block_number: int = 0
    asset: str = ""
    amount: str = "0"
    amount_usd: float = 0.0
    hop: int = 0
    taint_share: float = 0.0
    is_on_primary_path: bool = False
    edge_kind: str = "transfer"


class TraceGraphOut(BaseModel):
    job_id: str
    chain: Chain
    seed_address: str
    status: JobStatus
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    summary: dict[str, Any] | None = None
    data_source: DataSource = DataSource.SYNTHETIC


class TraceTimelineStep(BaseModel):
    """One fund movement, in the order it happened, described in plain language."""

    step: int
    hop: int
    chain: Chain
    from_address: str
    from_label: str
    to_address: str
    to_label: str
    asset: str
    amount: str
    amount_usd: float
    tx_hash: str
    timestamp: datetime
    elapsed_since_previous_seconds: float | None = None
    elapsed_since_previous_human: str | None = None
    share_of_victim_funds_pct: float = 0.0
    narrative: str


class TraceTimelineOut(BaseModel):
    job_id: str
    chain: Chain
    seed_address: str
    primary_path_only: bool
    steps: list[TraceTimelineStep]


class TraceNodeDetailOut(BaseModel):
    """Everything known about one address reached by a trace: its profile plus
    every transaction (in and out) that touched it, individually."""

    node: GraphNode
    inbound: list[GraphEdge]
    outbound: list[GraphEdge]
