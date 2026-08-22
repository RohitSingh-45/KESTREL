"""Schemas for the law-enforcement analytics dashboard."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Bucket(BaseModel):
    """A single labelled measurement - the shape every chart on the dashboard eats."""

    key: str
    label: str
    count: int = 0
    value_usd: float = 0.0
    extra: dict[str, float | str | None] = Field(default_factory=dict)


class OverviewStats(BaseModel):
    cases_total: int = 0
    cases_open: int = 0
    cases_attributed: int = 0
    cases_frozen: int = 0
    addresses_reported: int = 0
    traces_run: int = 0
    traces_succeeded: int = 0
    vasps_identified: int = 0
    alerts_open: int = 0
    alerts_overdue: int = 0
    total_loss_inr: float = 0.0
    traced_value_usd: float = 0.0
    attribution_rate: float = 0.0
    median_trace_ms: float = 0.0
    median_hops_to_vasp: float = 0.0
    sanctioned_exposure_count: int = 0
    cases_by_status: list[Bucket] = Field(default_factory=list)
    cases_by_category: list[Bucket] = Field(default_factory=list)
    recent_attributions: list[Bucket] = Field(default_factory=list)


class TypologyStats(BaseModel):
    distribution: list[Bucket] = Field(default_factory=list)
    by_chain: list[Bucket] = Field(default_factory=list)
    total_hits: int = 0


class VaspStatsOut(BaseModel):
    top_receiving: list[Bucket] = Field(default_factory=list)
    by_jurisdiction: list[Bucket] = Field(default_factory=list)
    indian_share_pct: float = 0.0
    fiu_registered_share_pct: float = 0.0


class PerformanceStats(BaseModel):
    """How fast the system is, which is the whole point of the project."""

    traces_total: int = 0
    success_rate: float = 0.0
    median_trace_ms: float = 0.0
    p95_trace_ms: float = 0.0
    mean_addresses_examined: float = 0.0
    mean_upstream_calls: float = 0.0
    by_method: list[Bucket] = Field(default_factory=list)
    by_chain: list[Bucket] = Field(default_factory=list)
    by_data_source: list[Bucket] = Field(default_factory=list)
    manual_baseline_hours: float = 72.0
    median_time_to_attribution_seconds: float = 0.0
