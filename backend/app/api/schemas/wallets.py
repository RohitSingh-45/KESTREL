"""Schemas for the wallet profile view."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas.attribution import CandidateOut
from app.core.enums import Chain, DataSource, EntityType, RiskLevel


class WalletLabel(BaseModel):
    label: str
    source: str
    category: str | None = None
    entity_name: str | None = None
    confidence: float
    is_sanctioned: bool = False


class WalletTypology(BaseModel):
    typology: str
    label: str
    confidence: float
    description: str | None = None


class CounterpartyOut(BaseModel):
    address: str
    chain: Chain
    direction: str
    transfer_count: int
    total_usd: float
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    entity_type: EntityType = EntityType.UNKNOWN
    entity_name: str | None = None


class TransferOut(BaseModel):
    tx_hash: str
    chain: Chain
    ts: datetime
    block_number: int
    direction: str
    counterparty: str
    asset: str
    amount: str
    amount_usd: float | None = None
    is_token: bool = False


class WalletProfile(BaseModel):
    chain: Chain
    address: str
    entity_type: EntityType = EntityType.UNKNOWN
    entity_name: str | None = None
    is_contract: bool = False
    is_sanctioned: bool = False
    risk_score: float = 0.0
    risk_level: RiskLevel = RiskLevel.LOW
    risk_reasons: list[str] = Field(default_factory=list)
    balance_native: str | None = None
    native_asset: str = ""
    tx_count: int = 0
    in_degree: int = 0
    out_degree: int = 0
    total_received_usd: float = 0.0
    total_sent_usd: float = 0.0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    labels: list[WalletLabel] = Field(default_factory=list)
    typologies: list[WalletTypology] = Field(default_factory=list)
    attribution: CandidateOut | None = None
    counterparties: list[CounterpartyOut] = Field(default_factory=list)
    recent_transfers: list[TransferOut] = Field(default_factory=list)
    seen_in_jobs: list[str] = Field(default_factory=list)
    data_source: DataSource = DataSource.SYNTHETIC
    profiled_at: datetime | None = None
