"""Application configuration via environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_RSS = ",".join(
    [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
        "https://www.schneier.com/feed/atom/",
    ]
)

_DEFAULT_TIER1 = ",".join(
    [
        "cisa.gov",
        "nvd.nist.gov",
        "nist.gov",
        "cert.org",
        "openai.com",
        "anthropic.com",
        "googleblog.com",
        "microsoft.com",
        "arxiv.org",
    ]
)
_DEFAULT_TIER2 = ",".join(
    [
        "schneier.com",
        "bleepingcomputer.com",
        "thehackernews.com",
        "krebsonsecurity.com",
        "darkreading.com",
        "securityweek.com",
        "wired.com",
        "technologyreview.com",
    ]
)


class Settings(BaseSettings):
    """Runtime settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/kestrel.db",
        alias="DATABASE_URL",
    )
    agent_cycle_seconds: int = Field(
        default=600,
        alias="AGENT_CYCLE_SECONDS",
        ge=1,
        le=86_400,
    )
    generation_lease_seconds: int = Field(
        default=300,
        alias="GENERATION_LEASE_SECONDS",
        ge=1,
        le=86_400,
    )
    sqlite_busy_timeout_ms: int = Field(
        default=5000,
        alias="SQLITE_BUSY_TIMEOUT_MS",
        ge=0,
        le=60_000,
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL", min_length=1)
    openai_timeout_seconds: float = Field(
        default=45.0,
        alias="OPENAI_TIMEOUT_SECONDS",
        gt=0,
        le=600,
    )
    openai_max_retries: int = Field(
        default=3,
        alias="OPENAI_MAX_RETRIES",
        ge=0,
        le=10,
    )

    research_api_key: str | None = Field(default=None, alias="RESEARCH_API_KEY")
    research_max_items: int = Field(
        default=20,
        alias="RESEARCH_MAX_ITEMS",
        ge=1,
        le=500,
    )
    research_rss_urls: str = Field(default=_DEFAULT_RSS, alias="RESEARCH_RSS_URLS")
    research_http_timeout_seconds: float = Field(
        default=20.0,
        alias="RESEARCH_HTTP_TIMEOUT_SECONDS",
        gt=0,
        le=300,
    )
    arxiv_enabled: bool = Field(default=True, alias="ARXIV_ENABLED")
    arxiv_query: str = Field(
        default="AI security OR LLM security OR prompt injection OR adversarial machine learning",
        alias="ARXIV_QUERY",
    )
    arxiv_max_results: int = Field(
        default=8,
        alias="ARXIV_MAX_RESULTS",
        ge=1,
        le=100,
    )

    topic_funnel_top_k: int = Field(default=3, alias="TOPIC_FUNNEL_TOP_K", ge=1)
    score_weight_recency: float = Field(default=0.20, alias="SCORE_WEIGHT_RECENCY")
    score_weight_persona_fit: float = Field(default=0.25, alias="SCORE_WEIGHT_PERSONA_FIT")
    score_weight_importance: float = Field(default=0.20, alias="SCORE_WEIGHT_IMPORTANCE")
    score_weight_novelty: float = Field(default=0.15, alias="SCORE_WEIGHT_NOVELTY")
    score_weight_source_quality: float = Field(
        default=0.10,
        alias="SCORE_WEIGHT_SOURCE_QUALITY",
    )
    score_weight_evidence: float = Field(default=0.10, alias="SCORE_WEIGHT_EVIDENCE")

    source_quality_tier1_domains: str = Field(
        default=_DEFAULT_TIER1,
        alias="SOURCE_QUALITY_TIER1_DOMAINS",
    )
    source_quality_tier2_domains: str = Field(
        default=_DEFAULT_TIER2,
        alias="SOURCE_QUALITY_TIER2_DOMAINS",
    )
    source_quality_tier3_domains: str = Field(
        default="",
        alias="SOURCE_QUALITY_TIER3_DOMAINS",
    )

    debate_top_k: int = Field(default=3, alias="DEBATE_TOP_K", ge=1)
    debate_min_confidence: float = Field(default=0.65, alias="DEBATE_MIN_CONFIDENCE")
    debate_min_score_for_proceed: float = Field(
        default=0.60,
        alias="DEBATE_MIN_SCORE_FOR_PROCEED",
    )

    memory_max_items: int = Field(default=10, alias="MEMORY_MAX_ITEMS", ge=1)
    memory_max_source_urls: int = Field(default=10, alias="MEMORY_MAX_SOURCE_URLS", ge=1)
    memory_extraction_enabled: bool = Field(default=True, alias="MEMORY_EXTRACTION_ENABLED")
    memory_min_confidence: float = Field(default=0.55, alias="MEMORY_MIN_CONFIDENCE")

    consistency_enabled: bool = Field(default=True, alias="CONSISTENCY_ENABLED")
    consistency_approve_threshold: float = Field(
        default=0.80,
        alias="CONSISTENCY_APPROVE_THRESHOLD",
    )
    consistency_revise_threshold: float = Field(
        default=0.60,
        alias="CONSISTENCY_REVISE_THRESHOLD",
    )
    consistency_max_revisions: int = Field(default=1, alias="CONSISTENCY_MAX_REVISIONS", ge=0)
    consistency_history_limit: int = Field(default=10, alias="CONSISTENCY_HISTORY_LIMIT", ge=0)

    # Module 12 — intelligence enhancements (metadata + prompt context; weights unchanged)
    source_diversity_lookback: int = Field(
        default=10,
        alias="SOURCE_DIVERSITY_LOOKBACK",
        ge=1,
        le=100,
    )
    source_diversity_min_score: float = Field(
        default=0.25,
        alias="SOURCE_DIVERSITY_MIN_SCORE",
        ge=0.0,
        le=1.0,
    )
    story_match_threshold: float = Field(
        default=0.70,
        alias="STORY_MATCH_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    story_lookback: int = Field(default=20, alias="STORY_LOOKBACK", ge=1, le=200)
    contradiction_enabled: bool = Field(default=True, alias="CONTRADICTION_ENABLED")
    contradiction_min_confidence: float = Field(
        default=0.70,
        alias="CONTRADICTION_MIN_CONFIDENCE",
        ge=0.0,
        le=1.0,
    )
    contradiction_max_items: int = Field(
        default=10,
        alias="CONTRADICTION_MAX_ITEMS",
        ge=1,
        le=50,
    )

    # Module 13 — adaptive learning (sidecar; does not modify score weights)
    adaptive_learning_enabled: bool = Field(
        default=True,
        alias="ADAPTIVE_LEARNING_ENABLED",
    )
    adaptive_lookback_posts: int = Field(
        default=20,
        alias="ADAPTIVE_LOOKBACK_POSTS",
        ge=1,
        le=200,
    )
    adaptive_max_signals: int = Field(
        default=20,
        alias="ADAPTIVE_MAX_SIGNALS",
        ge=1,
        le=100,
    )
    adaptive_max_context_items: int = Field(
        default=20,
        alias="ADAPTIVE_MAX_CONTEXT_ITEMS",
        ge=1,
        le=100,
    )
    adaptive_stale_days: int = Field(
        default=30,
        alias="ADAPTIVE_STALE_DAYS",
        ge=1,
        le=3650,
    )
    adaptive_decay_days: int = Field(
        default=30,
        alias="ADAPTIVE_DECAY_DAYS",
        ge=1,
        le=3650,
    )

    # Module 14 — post-publish evaluation (sidecar; never undoes publishing)
    evaluation_enabled: bool = Field(
        default=True,
        alias="EVALUATION_ENABLED",
    )
    evaluation_reflection_enabled: bool = Field(
        default=True,
        alias="EVALUATION_REFLECTION_ENABLED",
    )
    evaluation_lookback_posts: int = Field(
        default=20,
        alias="EVALUATION_LOOKBACK_POSTS",
        ge=1,
        le=200,
    )
    evaluation_excellent_threshold: float = Field(
        default=0.85,
        alias="EVALUATION_EXCELLENT_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    evaluation_good_threshold: float = Field(
        default=0.70,
        alias="EVALUATION_GOOD_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    evaluation_acceptable_threshold: float = Field(
        default=0.55,
        alias="EVALUATION_ACCEPTABLE_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    evaluation_weak_threshold: float = Field(
        default=0.40,
        alias="EVALUATION_WEAK_THRESHOLD",
        ge=0.0,
        le=1.0,
    )
    evaluation_feedback_decay_days: int = Field(
        default=30,
        alias="EVALUATION_FEEDBACK_DECAY_DAYS",
        ge=1,
        le=3650,
    )
    evaluation_max_feedback_items: int = Field(
        default=10,
        alias="EVALUATION_MAX_FEEDBACK_ITEMS",
        ge=1,
        le=50,
    )

    # Module 15 — controlled learning (guidance only; never rewrites persona/weights)
    learning_enabled: bool = Field(
        default=True,
        alias="LEARNING_ENABLED",
    )
    learning_min_observations: int = Field(
        default=3,
        alias="LEARNING_MIN_OBSERVATIONS",
        ge=1,
        le=50,
    )
    learning_min_confidence: float = Field(
        default=0.65,
        alias="LEARNING_MIN_CONFIDENCE",
        ge=0.0,
        le=1.0,
    )
    learning_max_signals: int = Field(
        default=20,
        alias="LEARNING_MAX_SIGNALS",
        ge=1,
        le=100,
    )
    learning_max_context_items: int = Field(
        default=20,
        alias="LEARNING_MAX_CONTEXT_ITEMS",
        ge=1,
        le=100,
    )
    learning_decay_days: int = Field(
        default=30,
        alias="LEARNING_DECAY_DAYS",
        ge=1,
        le=3650,
    )
    learning_lookback_evaluations: int = Field(
        default=20,
        alias="LEARNING_LOOKBACK_EVALUATIONS",
        ge=1,
        le=200,
    )

    # Module 16 — decision orchestration (deterministic; never disables safety)
    decision_enabled: bool = Field(
        default=True,
        alias="DECISION_ENABLED",
    )
    decision_min_research_items: int = Field(
        default=1,
        alias="DECISION_MIN_RESEARCH_ITEMS",
        ge=1,
        le=100,
    )
    decision_min_grounded_items: int = Field(
        default=1,
        alias="DECISION_MIN_GROUNDED_ITEMS",
        ge=1,
        le=100,
    )
    decision_min_candidates: int = Field(
        default=1,
        alias="DECISION_MIN_CANDIDATES",
        ge=1,
        le=100,
    )
    decision_min_selected: int = Field(
        default=1,
        alias="DECISION_MIN_SELECTED",
        ge=1,
        le=100,
    )
    decision_min_consistency_score: float = Field(
        default=0.70,
        alias="DECISION_MIN_CONSISTENCY_SCORE",
        ge=0.0,
        le=1.0,
    )
    decision_allow_uncertain_debate: bool = Field(
        default=False,
        alias="DECISION_ALLOW_UNCERTAIN_DEBATE",
    )
    decision_max_trace_items: int = Field(
        default=12,
        alias="DECISION_MAX_TRACE_ITEMS",
        ge=1,
        le=50,
    )

    # Module 17 — observability / explainability (sidecar; never authoritative)
    observability_enabled: bool = Field(
        default=True,
        alias="OBSERVABILITY_ENABLED",
    )
    observability_max_events: int = Field(
        default=100,
        alias="OBSERVABILITY_MAX_EVENTS",
        ge=1,
        le=500,
    )
    observability_max_reason_length: int = Field(
        default=300,
        alias="OBSERVABILITY_MAX_REASON_LENGTH",
        ge=40,
        le=2000,
    )
    observability_max_evidence_items: int = Field(
        default=10,
        alias="OBSERVABILITY_MAX_EVIDENCE_ITEMS",
        ge=1,
        le=50,
    )

    @field_validator("openai_api_key", "research_api_key", mode="before")
    @classmethod
    def empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value: object) -> object:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("LOG_LEVEL must be a non-empty string")
        upper = value.strip().upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}")
        return upper

    @field_validator("openai_model", mode="before")
    @classmethod
    def validate_openai_model(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("OPENAI_MODEL must be non-empty")
            return cleaned
        return value

    @model_validator(mode="after")
    def validate_score_and_debate_settings(self) -> Settings:
        total = (
            self.score_weight_recency
            + self.score_weight_persona_fit
            + self.score_weight_importance
            + self.score_weight_novelty
            + self.score_weight_source_quality
            + self.score_weight_evidence
        )
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                f"Score weights must sum to 1.0 (got {total}). "
                "Check SCORE_WEIGHT_* environment variables."
            )
        for name, weight in self.score_weights.items():
            if weight < 0:
                raise ValueError(f"Score weight {name} must be >= 0")
        if not 0.0 <= self.debate_min_confidence <= 1.0:
            raise ValueError("DEBATE_MIN_CONFIDENCE must be between 0 and 1")
        if not 0.0 <= self.debate_min_score_for_proceed <= 1.0:
            raise ValueError("DEBATE_MIN_SCORE_FOR_PROCEED must be between 0 and 1")
        if self.debate_top_k < 1:
            raise ValueError("DEBATE_TOP_K must be >= 1")
        if not 0.0 <= self.memory_min_confidence <= 1.0:
            raise ValueError("MEMORY_MIN_CONFIDENCE must be between 0 and 1")
        if self.memory_max_items < 1:
            raise ValueError("MEMORY_MAX_ITEMS must be >= 1")
        if self.memory_max_source_urls < 1:
            raise ValueError("MEMORY_MAX_SOURCE_URLS must be >= 1")
        if not 0.0 <= self.consistency_revise_threshold <= 1.0:
            raise ValueError("CONSISTENCY_REVISE_THRESHOLD must be between 0 and 1")
        if not 0.0 <= self.consistency_approve_threshold <= 1.0:
            raise ValueError("CONSISTENCY_APPROVE_THRESHOLD must be between 0 and 1")
        if self.consistency_revise_threshold > self.consistency_approve_threshold:
            raise ValueError(
                "CONSISTENCY_REVISE_THRESHOLD must be <= CONSISTENCY_APPROVE_THRESHOLD"
            )
        if self.consistency_max_revisions < 0:
            raise ValueError("CONSISTENCY_MAX_REVISIONS must be >= 0")
        if not 0.0 <= self.source_diversity_min_score <= 1.0:
            raise ValueError("SOURCE_DIVERSITY_MIN_SCORE must be between 0 and 1")
        if not 0.0 <= self.story_match_threshold <= 1.0:
            raise ValueError("STORY_MATCH_THRESHOLD must be between 0 and 1")
        if not 0.0 <= self.contradiction_min_confidence <= 1.0:
            raise ValueError("CONTRADICTION_MIN_CONFIDENCE must be between 0 and 1")
        if not (
            self.evaluation_excellent_threshold
            >= self.evaluation_good_threshold
            >= self.evaluation_acceptable_threshold
            >= self.evaluation_weak_threshold
        ):
            raise ValueError(
                "Evaluation thresholds must satisfy "
                "EXCELLENT >= GOOD >= ACCEPTABLE >= WEAK"
            )
        return self

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def rss_url_list(self) -> list[str]:
        return [part.strip() for part in self.research_rss_urls.split(",") if part.strip()]

    @property
    def llm_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def score_weights(self) -> dict[str, float]:
        return {
            "recency": self.score_weight_recency,
            "persona_fit": self.score_weight_persona_fit,
            "importance": self.score_weight_importance,
            "novelty": self.score_weight_novelty,
            "source_quality": self.score_weight_source_quality,
            "evidence_quality": self.score_weight_evidence,
        }

    def domain_list(self, raw: str) -> list[str]:
        return [part.strip().lower() for part in raw.split(",") if part.strip()]

    def sqlite_path(self) -> Path | None:
        """Return filesystem path for SQLite URLs, else None."""
        if not self.is_sqlite:
            return None
        raw = self.database_url.split(":///", maxsplit=1)[-1]
        return Path(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()
