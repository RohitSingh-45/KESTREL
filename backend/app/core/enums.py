"""Shared vocabulary used across the tracing, attribution and risk layers."""

from __future__ import annotations

from enum import StrEnum


class Chain(StrEnum):
    BITCOIN = "bitcoin"
    ETHEREUM = "ethereum"
    BSC = "bsc"
    POLYGON = "polygon"
    ARBITRUM = "arbitrum"
    BASE = "base"
    TRON = "tron"

    @property
    def is_evm(self) -> bool:
        return self in _EVM_CHAINS

    @property
    def display_name(self) -> str:
        return _CHAIN_DISPLAY[self]

    @property
    def native_asset(self) -> str:
        return _NATIVE_ASSET[self]


_EVM_CHAINS = frozenset(
    {Chain.ETHEREUM, Chain.BSC, Chain.POLYGON, Chain.ARBITRUM, Chain.BASE}
)

_CHAIN_DISPLAY = {
    Chain.BITCOIN: "Bitcoin",
    Chain.ETHEREUM: "Ethereum",
    Chain.BSC: "BNB Smart Chain",
    Chain.POLYGON: "Polygon",
    Chain.ARBITRUM: "Arbitrum One",
    Chain.BASE: "Base",
    Chain.TRON: "TRON",
}

_NATIVE_ASSET = {
    Chain.BITCOIN: "BTC",
    Chain.ETHEREUM: "ETH",
    Chain.BSC: "BNB",
    Chain.POLYGON: "POL",
    Chain.ARBITRUM: "ETH",
    Chain.BASE: "ETH",
    Chain.TRON: "TRX",
}

# Etherscan V2 uses a single endpoint keyed by EVM chain id.
EVM_CHAIN_IDS: dict[Chain, int] = {
    Chain.ETHEREUM: 1,
    Chain.BSC: 56,
    Chain.POLYGON: 137,
    Chain.ARBITRUM: 42161,
    Chain.BASE: 8453,
}


class DataSource(StrEnum):
    """Where the transaction data behind a result actually came from."""

    LIVE = "live"
    SYNTHETIC = "synthetic"
    CACHE = "cache"
    MIXED = "mixed"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"


class EntityType(StrEnum):
    """What kind of actor controls an address."""

    UNKNOWN = "unknown"
    CEX_HOT_WALLET = "cex_hot_wallet"
    CEX_DEPOSIT = "cex_deposit"
    CEX_COLD_WALLET = "cex_cold_wallet"
    VASP_OTHER = "vasp_other"
    P2P_MERCHANT = "p2p_merchant"
    MIXER = "mixer"
    BRIDGE = "bridge"
    DEFI_CONTRACT = "defi_contract"
    TOKEN_CONTRACT = "token_contract"
    GAMBLING = "gambling"
    DARKNET = "darknet"
    RANSOMWARE = "ransomware"
    SCAM = "scam"
    SANCTIONED = "sanctioned"
    PERSONAL_WALLET = "personal_wallet"
    VICTIM = "victim"


VASP_ENTITY_TYPES = frozenset(
    {
        EntityType.CEX_HOT_WALLET,
        EntityType.CEX_DEPOSIT,
        EntityType.CEX_COLD_WALLET,
        EntityType.VASP_OTHER,
        EntityType.P2P_MERCHANT,
    }
)

ILLICIT_ENTITY_TYPES = frozenset(
    {
        EntityType.MIXER,
        EntityType.DARKNET,
        EntityType.RANSOMWARE,
        EntityType.SCAM,
        EntityType.SANCTIONED,
    }
)


class AttributionMethod(StrEnum):
    """Tier of evidence behind an attribution, strongest first."""

    DIRECT_LABEL = "direct_label"
    SANCTIONS_LIST = "sanctions_list"
    DEPOSIT_CONSOLIDATION = "deposit_consolidation"
    COMMON_INPUT_CLUSTER = "common_input_cluster"
    ML_CLASSIFIER = "ml_classifier"
    CONTRACT_REGISTRY = "contract_registry"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> RiskLevel:
        if score >= 80:
            return cls.CRITICAL
        if score >= 60:
            return cls.HIGH
        if score >= 35:
            return cls.MEDIUM
        return cls.LOW


class Typology(StrEnum):
    PEEL_CHAIN = "peel_chain"
    FAN_OUT_LAYERING = "fan_out_layering"
    FAN_IN_AGGREGATION = "fan_in_aggregation"
    RAPID_RELAY = "rapid_relay"
    STRUCTURING = "structuring"
    DORMANT_BURST = "dormant_burst"
    MIXER_INTERACTION = "mixer_interaction"
    BRIDGE_INTERACTION = "bridge_interaction"
    SANCTIONS_EXPOSURE = "sanctions_exposure"
    DIRECT_TO_EXCHANGE = "direct_to_exchange"


TYPOLOGY_LABELS: dict[Typology, str] = {
    Typology.PEEL_CHAIN: "Peel chain",
    Typology.FAN_OUT_LAYERING: "Fan-out layering",
    Typology.FAN_IN_AGGREGATION: "Fan-in aggregation",
    Typology.RAPID_RELAY: "Rapid relay",
    Typology.STRUCTURING: "Structuring / smurfing",
    Typology.DORMANT_BURST: "Dormant-then-burst",
    Typology.MIXER_INTERACTION: "Mixer interaction",
    Typology.BRIDGE_INTERACTION: "Cross-chain bridge use",
    Typology.SANCTIONS_EXPOSURE: "Sanctions exposure",
    Typology.DIRECT_TO_EXCHANGE: "Direct deposit to exchange",
}


class FraudCategory(StrEnum):
    INVESTMENT_SCAM = "investment_scam"
    TASK_BASED_FRAUD = "task_based_fraud"
    SEXTORTION = "sextortion"
    RANSOMWARE = "ransomware"
    PHISHING = "phishing"
    DARKNET = "darknet"
    ORGANISED_CYBER_CRIME = "organised_cyber_crime"
    OTHER = "other"


class CaseStatus(StrEnum):
    OPEN = "open"
    TRACING = "tracing"
    ATTRIBUTED = "attributed"
    REQUEST_SENT = "request_sent"
    FROZEN = "frozen"
    CLOSED = "closed"


class CasePriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AlertType(StrEnum):
    VASP_IDENTIFIED = "vasp_identified"
    SANCTIONS_HIT = "sanctions_hit"
    MIXER_DETECTED = "mixer_detected"
    BRIDGE_DETECTED = "bridge_detected"
    HIGH_VALUE_FLOW = "high_value_flow"
    RAPID_MOVEMENT = "rapid_movement"
    TRACE_FAILED = "trace_failed"


class AlertStatus(StrEnum):
    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    ACTIONED = "actioned"
    DISMISSED = "dismissed"


class UserRole(StrEnum):
    INVESTIGATOR = "investigator"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return _ROLE_RANK[self]


_ROLE_RANK = {UserRole.INVESTIGATOR: 1, UserRole.SUPERVISOR: 2, UserRole.ADMIN: 3}


class ReportKind(StrEnum):
    INVESTIGATION_PACKET = "investigation_packet"
    BNSS_94_REQUEST = "bnss_94_request"
    SAHYOG_PACKAGE = "sahyog_package"
