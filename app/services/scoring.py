"""Deterministic topic funnel scoring engine.

Heuristics are transparent and reproducible. They are not perfect real-world
severity/reputation scores — they provide explainable ranking signals for Kestrel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.logging import get_logger
from app.services.llm.base import CandidateTopic
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

PERSONA_CONCEPTS: tuple[str, ...] = (
    "ai security",
    "llm security",
    "prompt injection",
    "adversarial machine learning",
    "adversarial",
    "model vulnerability",
    "model vulnerabilities",
    "ai agent",
    "ai agents",
    "agent security",
    "model supply chain",
    "ai red team",
    "ai red teaming",
    "jailbreak",
    "data poisoning",
    "model theft",
    "ai privacy",
    "inference attack",
    "inference attacks",
    "ai safety",
    "model security",
    "llm jailbreak",
    "security",
)

IMPORTANCE_TERMS: tuple[str, ...] = (
    "critical",
    "vulnerability",
    "vulnerabilities",
    "exploit",
    "exploitation",
    "attack",
    "breach",
    "security",
    "advisory",
    "cve",
    "risk",
    "threat",
    "injection",
    "poisoning",
    "bypass",
    "leak",
    "compromise",
    "zero-day",
    "zeroday",
    "ransomware",
    "malware",
)


class TopicScores(BaseModel):
    """Bounded per-dimension scores plus weighted final score."""

    recency: float
    persona_fit: float
    importance: float
    novelty: float
    source_quality: float
    evidence_quality: float
    final_score: float
    llm_confidence: float | None = None

    @field_validator(
        "recency",
        "persona_fit",
        "importance",
        "novelty",
        "source_quality",
        "evidence_quality",
        "final_score",
        mode="before",
    )
    @classmethod
    def clamp_unit(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except Exception:
            return 0.0
        return max(0.0, min(1.0, number))


class ScoredCandidate(BaseModel):
    topic_id: str
    title: str
    candidate: CandidateTopic
    scores: TopicScores
    rank: int = 0
    decision: str = "candidate"
    top_k: int = 3


@dataclass(frozen=True)
class HistoricalTopic:
    topic_id: str
    title: str


@dataclass(frozen=True)
class ScoringContext:
    persona_name: str
    persona_domain: str
    now: datetime
    research_items: list[ResearchItem] = field(default_factory=list)
    previous_topics: list[HistoricalTopic] = field(default_factory=list)
    previous_post_titles: list[str] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    top_k: int = 3
    tier1_domains: tuple[str, ...] = ()
    tier2_domains: tuple[str, ...] = ()
    tier3_domains: tuple[str, ...] = ()


DEFAULT_WEIGHTS: dict[str, float] = {
    "recency": 0.20,
    "persona_fit": 0.25,
    "importance": 0.20,
    "novelty": 0.15,
    "source_quality": 0.10,
    "evidence_quality": 0.10,
}


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def tokenize(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def jaccard_similarity(a: str, b: str) -> float:
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    union = ta | tb
    if not union:
        return 0.0
    return len(ta & tb) / len(union)


def score_recency(reference: datetime | None, now: datetime) -> float:
    """Deterministic recency decay based on age buckets."""
    if reference is None:
        return 0.10
    try:
        ts = reference
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age_seconds = (now_aware - ts.astimezone(timezone.utc)).total_seconds()
    except Exception:
        return 0.10

    # Future timestamps: treat as maximally recent but not > 1.0
    if age_seconds < 0:
        return 1.0

    hours = age_seconds / 3600.0
    if hours <= 6:
        return 1.00
    if hours <= 24:
        return 0.90
    if hours <= 72:
        return 0.75
    if hours <= 168:
        return 0.55
    if hours <= 336:
        return 0.30
    return 0.10


def score_persona_fit(
    candidate: CandidateTopic,
    *,
    research_text: str = "",
    persona_domain: str = "AI Security",
) -> float:
    # Score the candidate/research text only — do not inject persona_domain into the
    # blob (that would inflate unrelated topics just because the agent domain is set).
    blob = " ".join(
        [
            candidate.title,
            candidate.angle,
            candidate.why_now,
            candidate.relevance_to_persona,
            research_text,
        ]
    ).lower()
    _ = persona_domain  # reserved for future domain-specific dictionaries
    hits = sum(1 for concept in PERSONA_CONCEPTS if concept in blob)
    if hits == 0:
        # Mild credit if domain token appears loosely.
        if "security" in blob and ("ai" in blob or "llm" in blob or "model" in blob):
            return 0.35
        return 0.10
    if hits == 1:
        return 0.55
    if hits == 2:
        return 0.75
    if hits == 3:
        return 0.88
    return 1.0


def score_importance(candidate: CandidateTopic, *, research_text: str = "") -> float:
    """Transparent impact heuristic using security terminology density."""
    blob = " ".join(
        [
            candidate.title,
            candidate.angle,
            candidate.why_now,
            candidate.relevance_to_persona,
            research_text,
        ]
    ).lower()
    hits = sum(1 for term in IMPORTANCE_TERMS if term in blob)
    if hits == 0:
        return 0.20
    if hits == 1:
        return 0.45
    if hits == 2:
        return 0.65
    if hits == 3:
        return 0.80
    return 0.95


def score_novelty(candidate_title: str, history_titles: list[str]) -> float:
    if not history_titles:
        return 1.0
    max_sim = 0.0
    for title in history_titles:
        max_sim = max(max_sim, jaccard_similarity(candidate_title, title))
    return clamp01(1.0 - max_sim)


def _domain_from_url(url: str) -> str:
    try:
        host = urlsplit(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def score_source_quality(
    source_urls: list[str],
    *,
    tier1: tuple[str, ...],
    tier2: tuple[str, ...],
    tier3: tuple[str, ...],
) -> float:
    if not source_urls:
        return 0.0
    best = 0.40  # unknown neutral baseline
    for url in source_urls:
        domain = _domain_from_url(url)
        if not domain:
            continue
        if any(domain == d or domain.endswith("." + d) for d in tier1):
            best = max(best, 1.0)
        elif any(domain == d or domain.endswith("." + d) for d in tier2):
            best = max(best, 0.80)
        elif any(domain == d or domain.endswith("." + d) for d in tier3):
            best = max(best, 0.65)
    return clamp01(best)


def score_evidence_quality(
    candidate: CandidateTopic,
    *,
    research_by_url: dict[str, ResearchItem],
) -> float:
    grounded_items: list[ResearchItem] = []
    for url in candidate.source_urls:
        key = normalize_url(url) or url
        item = research_by_url.get(key)
        if item is not None:
            grounded_items.append(item)

    if not grounded_items:
        return 0.0

    score = 0.50  # one grounded source + title assumed from candidate
    if any(item.summary for item in grounded_items):
        score = 0.65
    if any(item.published_at is not None for item in grounded_items):
        score = max(score, 0.75)
    if candidate.angle.strip() and candidate.why_now.strip():
        score = min(1.0, score + 0.05)
    if len(grounded_items) >= 2:
        score = max(score, 0.85)
    if len(grounded_items) >= 3:
        score = max(score, 0.92)
    return clamp01(score)


def compute_final_score(
    scores: dict[str, float],
    weights: dict[str, float],
) -> float:
    total = 0.0
    for key, weight in weights.items():
        total += float(scores.get(key, 0.0)) * float(weight)
    return clamp01(total)


def resolve_candidate_timestamp(
    candidate: CandidateTopic,
    research_by_url: dict[str, ResearchItem],
) -> datetime | None:
    dates: list[datetime] = []
    for url in candidate.source_urls:
        item = research_by_url.get(normalize_url(url) or url)
        if item is None:
            continue
        if item.published_at is not None:
            dates.append(item.published_at)
        elif item.discovered_at is not None:
            dates.append(item.discovered_at)
    if not dates:
        return None
    return max(dates)


class TopicScoringService:
    """Deterministic scoring + ranking for candidate topics."""

    def score_candidate(
        self,
        candidate: CandidateTopic,
        context: ScoringContext,
        *,
        topic_id: str = "",
    ) -> TopicScores:
        research_by_url = {
            (normalize_url(item.url) or item.url): item for item in context.research_items
        }
        research_text = " ".join(
            f"{item.title} {item.summary} {' '.join(item.tags)}"
            for item in context.research_items
            if (normalize_url(item.url) or item.url)
            in {(normalize_url(u) or u) for u in candidate.source_urls}
        )

        reference = resolve_candidate_timestamp(candidate, research_by_url)
        history_titles = [t.title for t in context.previous_topics] + list(
            context.previous_post_titles
        )
        weights = context.weights or DEFAULT_WEIGHTS

        dims = {
            "recency": score_recency(reference, context.now),
            "persona_fit": score_persona_fit(
                candidate,
                research_text=research_text,
                persona_domain=context.persona_domain,
            ),
            "importance": score_importance(candidate, research_text=research_text),
            "novelty": score_novelty(candidate.title, history_titles),
            "source_quality": score_source_quality(
                candidate.source_urls,
                tier1=context.tier1_domains,
                tier2=context.tier2_domains,
                tier3=context.tier3_domains,
            ),
            "evidence_quality": score_evidence_quality(
                candidate,
                research_by_url=research_by_url,
            ),
        }
        final_score = compute_final_score(dims, weights)
        result = TopicScores(
            **dims,
            final_score=final_score,
            llm_confidence=candidate.confidence,
        )
        logger.info(
            "topic_scored",
            topic_id=topic_id or None,
            title=candidate.title,
            final_score=result.final_score,
            scores=dims,
        )
        return result

    def rank_candidates(
        self,
        candidates: list[tuple[str, CandidateTopic]],
        context: ScoringContext,
    ) -> list[ScoredCandidate]:
        """Score and rank candidates. Input tuples are (topic_id, candidate)."""
        logger.info(
            "topic_scoring_started",
            item_count=len(candidates),
            top_k=context.top_k,
        )
        scored: list[ScoredCandidate] = []
        for topic_id, candidate in candidates:
            siblings = [
                HistoricalTopic(topic_id=other_id, title=other.title)
                for other_id, other in candidates
                if other_id != topic_id
            ]
            local_context = replace(
                context,
                previous_topics=list(context.previous_topics) + siblings,
            )
            scores = self.score_candidate(candidate, local_context, topic_id=topic_id)
            scored.append(
                ScoredCandidate(
                    topic_id=topic_id,
                    title=candidate.title,
                    candidate=candidate,
                    scores=scores,
                    top_k=context.top_k,
                )
            )

        scored.sort(
            key=lambda item: (
                -item.scores.final_score,
                -item.scores.persona_fit,
                -item.scores.recency,
                -item.scores.evidence_quality,
                item.topic_id,
                item.title,
            )
        )

        for index, item in enumerate(scored, start=1):
            item.rank = index
            item.decision = "selected" if index <= context.top_k else "rejected"
            if item.decision == "selected":
                logger.info(
                    "topic_selected",
                    topic_id=item.topic_id,
                    rank=item.rank,
                    final_score=item.scores.final_score,
                    decision=item.decision,
                )
            else:
                logger.info(
                    "topic_rejected",
                    topic_id=item.topic_id,
                    rank=item.rank,
                    final_score=item.scores.final_score,
                    decision=item.decision,
                )

        logger.info(
            "topic_ranking_completed",
            item_count=len(scored),
            selected=sum(1 for s in scored if s.decision == "selected"),
            rejected=sum(1 for s in scored if s.decision == "rejected"),
        )
        return scored


def scores_payload(
    scored: ScoredCandidate,
    *,
    weights: dict[str, float],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Structured scores_json payload for persistence.

    Module 5 weights/scores remain authoritative. Optional meta holds Module 12
    intelligence annotations (diversity/story/contradiction) without changing ranks.
    """
    candidate = scored.candidate
    payload: dict[str, Any] = {
        "version": 1,
        "weights": weights,
        "scores": {
            "recency": scored.scores.recency,
            "persona_fit": scored.scores.persona_fit,
            "importance": scored.scores.importance,
            "novelty": scored.scores.novelty,
            "source_quality": scored.scores.source_quality,
            "evidence_quality": scored.scores.evidence_quality,
        },
        "final_score": scored.scores.final_score,
        "rank": scored.rank,
        "decision": scored.decision,
        "top_k": scored.top_k,
        "llm_confidence": scored.scores.llm_confidence,
        "angle": candidate.angle,
        "why_now": candidate.why_now,
        "relevance_to_persona": candidate.relevance_to_persona,
        "source_urls": candidate.source_urls,
    }
    if meta:
        payload["meta"] = meta
    return payload
