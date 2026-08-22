"""ORM models for cases, the transaction graph, attribution and case output."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AlertStatus,
    AlertType,
    AttributionMethod,
    CasePriority,
    CaseStatus,
    Chain,
    DataSource,
    EntityType,
    FraudCategory,
    JobStatus,
    ReportKind,
    RiskLevel,
    UserRole,
)
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import CryptoAmount, JSONColumn

# ---------------------------------------------------------------------------
# Identity and audit
# ---------------------------------------------------------------------------


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(32), default=UserRole.INVESTIGATOR)
    agency: Mapped[str | None] = mapped_column(String(160), nullable=True)
    badge_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_resource", "resource_type", "resource_id"),)

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


# ---------------------------------------------------------------------------
# VASP directory and label store
# ---------------------------------------------------------------------------


class Vasp(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A virtual asset service provider we can serve a request on."""

    __tablename__ = "vasps"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str] = mapped_column(String(120), default="Unknown")
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_indian_entity: Mapped[bool] = mapped_column(Boolean, default=False)
    fiu_ind_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    fiu_ind_reg_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compliance_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    grievance_officer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    law_enforcement_portal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    response_sla_hours: Mapped[int] = mapped_column(Integer, default=72)
    accepts_emergency_freeze: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)


class AddressLabel(UUIDPrimaryKeyMixin, Base):
    """An externally sourced label for an address (eth-labels, OFAC, MBAL...)."""

    __tablename__ = "address_labels"
    __table_args__ = (
        UniqueConstraint("chain", "address", "source", "label", name="uq_label_identity"),
        Index("ix_label_lookup", "chain", "address"),
    )

    chain: Mapped[Chain] = mapped_column(String(24), index=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    label: Mapped[str] = mapped_column(String(255))
    entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    entity_type: Mapped[EntityType] = mapped_column(String(40), default=EntityType.UNKNOWN)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.9)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    vasp_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    details: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


class ContractRegistryEntry(UUIDPrimaryKeyMixin, Base):
    """Known mixer, bridge and DeFi router contracts."""

    __tablename__ = "contract_registry"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_contract_identity"),)

    chain: Mapped[Chain] = mapped_column(String(24), index=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(160))
    protocol: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[EntityType] = mapped_column(String(40))
    supported_chains: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


# ---------------------------------------------------------------------------
# Observed chain data
# ---------------------------------------------------------------------------


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Cached profile of an address we have looked at."""

    __tablename__ = "addresses"
    __table_args__ = (UniqueConstraint("chain", "address", name="uq_address_identity"),)

    chain: Mapped[Chain] = mapped_column(String(24), index=True)
    address: Mapped[str] = mapped_column(String(128), index=True)
    entity_type: Mapped[EntityType] = mapped_column(String(40), default=EntityType.UNKNOWN)
    entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    cluster_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    tx_count: Mapped[int] = mapped_column(Integer, default=0)
    in_degree: Mapped[int] = mapped_column(Integer, default=0)
    out_degree: Mapped[int] = mapped_column(Integer, default=0)
    total_received_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_sent_usd: Mapped[float] = mapped_column(Float, default=0.0)
    balance_native: Mapped[Decimal | None] = mapped_column(CryptoAmount, nullable=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[RiskLevel] = mapped_column(String(16), default=RiskLevel.LOW)
    typologies: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    is_contract: Mapped[bool] = mapped_column(Boolean, default=False)
    data_source: Mapped[DataSource] = mapped_column(String(16), default=DataSource.SYNTHETIC)
    features: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    profiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Transfer(UUIDPrimaryKeyMixin, Base):
    """A normalised value transfer - one edge of the money-flow graph."""

    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("chain", "tx_hash", "trace_index", name="uq_transfer_identity"),
        Index("ix_transfer_src", "chain", "src_address", "ts"),
        Index("ix_transfer_dst", "chain", "dst_address", "ts"),
    )

    chain: Mapped[Chain] = mapped_column(String(24), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), index=True)
    trace_index: Mapped[int] = mapped_column(Integer, default=0)
    block_number: Mapped[int] = mapped_column(Integer, default=0, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    src_address: Mapped[str] = mapped_column(String(128), index=True)
    dst_address: Mapped[str] = mapped_column(String(128), index=True)
    asset: Mapped[str] = mapped_column(String(32), default="")
    amount: Mapped[Decimal] = mapped_column(CryptoAmount, default=Decimal("0"))
    amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_token: Mapped[bool] = mapped_column(Boolean, default=False)
    token_contract: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fee_native: Mapped[Decimal | None] = mapped_column(CryptoAmount, nullable=True)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False)
    data_source: Mapped[DataSource] = mapped_column(String(16), default=DataSource.SYNTHETIC)


class SyntheticTransfer(UUIDPrimaryKeyMixin, Base):
    """Seeded ground-truth chain data backing synthetic / fallback mode.

    Deliberately separate from `transfers` so the demo dataset is never
    confused with data actually observed from a live explorer.
    """

    __tablename__ = "synthetic_transfers"
    __table_args__ = (
        Index("ix_syn_src", "chain", "src_address"),
        Index("ix_syn_dst", "chain", "dst_address"),
        UniqueConstraint("chain", "tx_hash", "trace_index", name="uq_syn_transfer"),
    )

    scenario: Mapped[str] = mapped_column(String(64), index=True)
    chain: Mapped[Chain] = mapped_column(String(24), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128), index=True)
    trace_index: Mapped[int] = mapped_column(Integer, default=0)
    block_number: Mapped[int] = mapped_column(Integer, default=0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    src_address: Mapped[str] = mapped_column(String(128))
    dst_address: Mapped[str] = mapped_column(String(128))
    asset: Mapped[str] = mapped_column(String(32), default="")
    amount: Mapped[Decimal] = mapped_column(CryptoAmount, default=Decimal("0"))
    amount_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_token: Mapped[bool] = mapped_column(Boolean, default=False)
    token_contract: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Bitcoin co-spend grouping: inputs sharing a tx share this key.
    input_group: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class SyntheticGroundTruth(UUIDPrimaryKeyMixin, Base):
    """Expected answer for a seeded scenario, used to measure accuracy."""

    __tablename__ = "synthetic_ground_truth"

    scenario: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    chain: Mapped[Chain] = mapped_column(String(24))
    seed_address: Mapped[str] = mapped_column(String(128))
    expected_vasp_slug: Mapped[str] = mapped_column(String(64))
    expected_deposit_address: Mapped[str] = mapped_column(String(128))
    expected_hops: Mapped[int] = mapped_column(Integer, default=1)
    fraud_category: Mapped[FraudCategory] = mapped_column(String(40))
    expected_typologies: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class Case(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cases"

    case_number: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ncrp_ack_no: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fraud_category: Mapped[FraudCategory] = mapped_column(String(40), default=FraudCategory.OTHER)
    status: Mapped[CaseStatus] = mapped_column(String(24), default=CaseStatus.OPEN, index=True)
    priority: Mapped[CasePriority] = mapped_column(String(16), default=CasePriority.NORMAL)
    victim_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    victim_contact: Mapped[str | None] = mapped_column(String(160), nullable=True)
    loss_amount_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    incident_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    police_station: Mapped[str | None] = mapped_column(String(160), nullable=True)
    state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_to_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    intake_source: Mapped[str] = mapped_column(String(32), default="manual")
    details: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    addresses: Mapped[list[CaseAddress]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin"
    )
    assigned_to: Mapped[User | None] = relationship(lazy="joined")


class CaseAddress(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "case_addresses"
    __table_args__ = (
        UniqueConstraint("case_id", "chain", "address", name="uq_case_address"),
    )

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    chain: Mapped[Chain] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(32), default="reported")
    amount_sent_inr: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    case: Mapped[Case] = relationship(back_populates="addresses")


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


class TraceJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "trace_jobs"

    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    chain: Mapped[Chain] = mapped_column(String(24))
    seed_address: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[JobStatus] = mapped_column(String(16), default=JobStatus.QUEUED, index=True)
    params: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)
    data_source: Mapped[DataSource] = mapped_column(String(16), default=DataSource.SYNTHETIC)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requested_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    nodes: Mapped[list[TraceNode]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    edges: Mapped[list[TraceEdge]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class TraceNode(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trace_nodes"
    __table_args__ = (
        UniqueConstraint("job_id", "chain", "address", name="uq_trace_node"),
    )

    job_id: Mapped[str] = mapped_column(
        ForeignKey("trace_jobs.id", ondelete="CASCADE"), index=True
    )
    chain: Mapped[Chain] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128), index=True)
    hop: Mapped[int] = mapped_column(Integer, default=0)
    taint: Mapped[float] = mapped_column(Float, default=0.0)
    value_in_usd: Mapped[float] = mapped_column(Float, default=0.0)
    value_out_usd: Mapped[float] = mapped_column(Float, default=0.0)
    entity_type: Mapped[EntityType] = mapped_column(String(40), default=EntityType.UNKNOWN)
    entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    vasp_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attribution_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    attribution_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_level: Mapped[RiskLevel] = mapped_column(String(16), default=RiskLevel.LOW)
    typologies: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    is_seed: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)

    job: Mapped[TraceJob] = relationship(back_populates="nodes")


class TraceEdge(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "trace_edges"
    __table_args__ = (Index("ix_trace_edge_job", "job_id", "hop"),)

    job_id: Mapped[str] = mapped_column(
        ForeignKey("trace_jobs.id", ondelete="CASCADE"), index=True
    )
    chain: Mapped[Chain] = mapped_column(String(24))
    src_address: Mapped[str] = mapped_column(String(128), index=True)
    dst_address: Mapped[str] = mapped_column(String(128), index=True)
    tx_hash: Mapped[str] = mapped_column(String(128))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    block_number: Mapped[int] = mapped_column(Integer, default=0)
    asset: Mapped[str] = mapped_column(String(32), default="")
    amount: Mapped[Decimal] = mapped_column(CryptoAmount, default=Decimal("0"))
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    hop: Mapped[int] = mapped_column(Integer, default=0)
    taint_share: Mapped[float] = mapped_column(Float, default=0.0)
    is_on_primary_path: Mapped[bool] = mapped_column(Boolean, default=False)
    edge_kind: Mapped[str] = mapped_column(String(24), default="transfer")

    job: Mapped[TraceJob] = relationship(back_populates="edges")


class CrossChainLink(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A heuristically inferred bridge hop between two chains."""

    __tablename__ = "cross_chain_links"

    job_id: Mapped[str] = mapped_column(
        ForeignKey("trace_jobs.id", ondelete="CASCADE"), index=True
    )
    bridge_name: Mapped[str] = mapped_column(String(120))
    src_chain: Mapped[Chain] = mapped_column(String(24))
    src_address: Mapped[str] = mapped_column(String(128))
    src_tx_hash: Mapped[str] = mapped_column(String(128))
    src_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dst_chain: Mapped[Chain] = mapped_column(String(24))
    dst_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dst_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dst_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    value_usd: Mapped[float] = mapped_column(Float, default=0.0)
    value_delta_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    time_delta_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_inferred: Mapped[bool] = mapped_column(Boolean, default=True)
    evidence: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


class Attribution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A stored conclusion about who controls an address."""

    __tablename__ = "attributions"
    __table_args__ = (
        UniqueConstraint("chain", "address", "method", name="uq_attribution_identity"),
        Index("ix_attribution_lookup", "chain", "address"),
    )

    chain: Mapped[Chain] = mapped_column(String(24))
    address: Mapped[str] = mapped_column(String(128), index=True)
    entity_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    entity_type: Mapped[EntityType] = mapped_column(String(40), default=EntityType.UNKNOWN)
    vasp_slug: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    method: Mapped[AttributionMethod] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    is_sanctioned: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


# ---------------------------------------------------------------------------
# Output: alerts and reports
# ---------------------------------------------------------------------------


class Alert(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "alerts"

    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    alert_type: Mapped[AlertType] = mapped_column(String(40), index=True)
    severity: Mapped[RiskLevel] = mapped_column(String(16), default=RiskLevel.MEDIUM, index=True)
    status: Mapped[AlertStatus] = mapped_column(String(24), default=AlertStatus.NEW, index=True)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    chain: Mapped[Chain | None] = mapped_column(String(24), nullable=True)
    address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vasp_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommended_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONColumn(), nullable=True)


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reports"

    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[ReportKind] = mapped_column(String(40))
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str] = mapped_column(String(80), default="application/pdf")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    generated_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    generated_by_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sections: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
    custody_log: Mapped[list | None] = mapped_column(JSONColumn(), nullable=True)
