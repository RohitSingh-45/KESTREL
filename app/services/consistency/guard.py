"""Persona consistency guard with bounded controlled revision."""

from __future__ import annotations

import re
import time
from typing import Any

from app.logging import get_logger
from app.services.consistency.analyzer import (
    ConsistencyAnalyzer,
    ConsistencyContext,
    build_historical_style,
    candidate_text,
)
from app.services.consistency.models import (
    ConsistencyDecisionEnum,
    ConsistencyResult,
    HistoricalStyleProfile,
    consistency_payload,
)
from app.services.consistency.profile import get_persona_profile
from app.services.debate import DebateResult
from app.services.llm.base import CandidateTopic
from app.services.memory.models import MemoryRecord
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate

logger = get_logger(__name__)

_HYPE_REPLACEMENTS = (
    (re.compile(r"!{2,}"), "."),
    (re.compile(r"\bABSOLUTELY\b", re.I), ""),
    (re.compile(r"\bTHE WORST\b.+\bEVER\b", re.I), "a significant"),
    (re.compile(r"\bunquestionably\b", re.I), "currently appears"),
    (re.compile(r"\bguaranteed\b", re.I), "likely"),
    (re.compile(r"\bcompletely safe\b", re.I), "context-dependent"),
    (re.compile(r"\bshocking\b", re.I), "notable"),
    (re.compile(r"\bmind[- ]blowing\b", re.I), "important"),
)


class ConsistencyGuard:
    """Runs deterministic consistency analysis and at most one controlled revision."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        approve_threshold: float = 0.80,
        revise_threshold: float = 0.60,
        max_revisions: int = 1,
        history_limit: int = 10,
        analyzer: ConsistencyAnalyzer | None = None,
    ) -> None:
        if not 0.0 <= revise_threshold <= approve_threshold <= 1.0:
            raise ValueError(
                "Consistency thresholds must satisfy "
                "0 <= revise <= approve <= 1"
            )
        if max_revisions < 0:
            raise ValueError("CONSISTENCY_MAX_REVISIONS must be >= 0")
        self.enabled = enabled
        self.approve_threshold = approve_threshold
        self.revise_threshold = revise_threshold
        self.max_revisions = max_revisions
        self.history_limit = history_limit
        self._analyzer = analyzer or ConsistencyAnalyzer()

    def evaluate(
        self,
        scored: ScoredCandidate,
        *,
        debate: DebateResult | None,
        persona_name: str,
        persona_domain: str,
        research_items: list[ResearchItem],
        memories: list[MemoryRecord],
        historical_topics: list[Any] | None = None,
        previous_titles: list[str] | None = None,
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> tuple[ConsistencyResult, ScoredCandidate]:
        """Analyze candidate; optionally revise once. Never publishes."""
        if not self.enabled:
            from app.services.consistency.models import ConsistencySignals

            profile = get_persona_profile(name=persona_name, domain=persona_domain)
            result = ConsistencyResult(
                topic_id=scored.topic_id,
                decision=ConsistencyDecisionEnum.REJECT,
                overall_score=0.0,
                signals=ConsistencySignals(),
                reasons=["consistency_disabled"],
                hard_reject=True,
                profile_version=profile.version,
            )
            return result, scored

        profile = get_persona_profile(name=persona_name, domain=persona_domain)
        historical = build_historical_style(
            historical_topics or [],
            limit=self.history_limit,
        )
        context = ConsistencyContext(
            profile=profile,
            research_items=research_items,
            memories=memories,
            historical_style=historical,
            previous_titles=previous_titles or [],
            approve_threshold=self.approve_threshold,
            revise_threshold=self.revise_threshold,
        )

        current = scored
        revision_count = 0
        revised = False
        result = self._analyzer.analyze(
            current,
            debate=debate,
            context=context,
            agent_id=agent_id,
            cycle_id=cycle_id,
            revision_count=revision_count,
            revised=revised,
        )

        if (
            result.decision == ConsistencyDecisionEnum.REVISE
            and revision_count < self.max_revisions
        ):
            try:
                logger.info(
                    "consistency_revision_requested",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=scored.topic_id,
                    stage="consistency",
                )
                revised_candidate = revise_candidate(
                    current.candidate,
                    research_items=research_items,
                    profile_domain=profile.domain,
                )
                current = current.model_copy(
                    update={
                        "candidate": revised_candidate,
                        "title": revised_candidate.title,
                    }
                )
                revision_count = 1
                revised = True
                result = self._analyzer.analyze(
                    current,
                    debate=debate,
                    context=context,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    revision_count=revision_count,
                    revised=revised,
                )
                # Bound revisions: if still revise after one attempt → reject.
                if result.decision == ConsistencyDecisionEnum.REVISE:
                    result = result.model_copy(
                        update={
                            "decision": ConsistencyDecisionEnum.REJECT,
                            "reasons": list(
                                dict.fromkeys(
                                    result.reasons + ["revision_limit_exhausted"]
                                )
                            ),
                            "revision_count": revision_count,
                            "revised": True,
                        }
                    )
                    logger.info(
                        "consistency_revision_failed",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        topic_id=scored.topic_id,
                        stage="consistency",
                        reason="revision_limit_exhausted",
                    )
                else:
                    logger.info(
                        "consistency_revision_success",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        topic_id=scored.topic_id,
                        stage="consistency",
                        decision=result.decision.value,
                    )
            except Exception as exc:
                logger.exception(
                    "consistency_revision_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=scored.topic_id,
                    stage="consistency",
                )
                result = ConsistencyResult(
                    topic_id=scored.topic_id,
                    decision=ConsistencyDecisionEnum.REJECT,
                    overall_score=result.overall_score,
                    signals=result.signals,
                    reasons=list(
                        dict.fromkeys(result.reasons + ["revision_failed"])
                    ),
                    hard_reject=True,
                    drift_flags=result.drift_flags,
                    error=str(exc),
                    profile_version=profile.version,
                    revision_count=revision_count,
                    revised=False,
                )
                current = scored

        return result, current


def revise_candidate(
    candidate: CandidateTopic,
    *,
    research_items: list[ResearchItem],
    profile_domain: str,
) -> CandidateTopic:
    """Deterministic controlled revision. Never invents URLs or facts."""
    started = time.perf_counter()
    allowed = {(normalize_url(item.url) or item.url) for item in research_items}
    url_by_norm = {
        (normalize_url(item.url) or item.url): item.url for item in research_items
    }

    def _clean(text: str) -> str:
        out = text
        for pattern, repl in _HYPE_REPLACEMENTS:
            out = pattern.sub(repl, out)
        out = re.sub(r"\s+", " ", out).strip(" .")
        if out and out[0].islower():
            out = out[0].upper() + out[1:]
        return out

    grounded_urls: list[str] = []
    for raw in candidate.source_urls:
        key = normalize_url(raw) or raw
        if key in allowed:
            grounded_urls.append(url_by_norm.get(key, raw))
    grounded_urls = list(dict.fromkeys(grounded_urls))
    if not grounded_urls:
        raise ValueError("revision_cannot_proceed_without_grounded_sources")

    title = _clean(candidate.title)
    angle = _clean(candidate.angle)
    why_now = _clean(candidate.why_now)
    relevance = _clean(candidate.relevance_to_persona)
    if profile_domain.lower() not in relevance.lower():
        relevance = f"{relevance}. Relevant to {profile_domain}."

    revised = CandidateTopic.model_validate(
        {
            "title": title or candidate.title,
            "angle": angle or candidate.angle,
            "why_now": why_now or candidate.why_now,
            "relevance_to_persona": relevance or candidate.relevance_to_persona,
            "source_urls": grounded_urls,
            "confidence": candidate.confidence,
        }
    )
    # Ensure revision did not invent URLs.
    for url in revised.source_urls:
        if (normalize_url(url) or url) not in allowed:
            raise ValueError("revision_invented_url")

    logger.info(
        "consistency_revision_success",
        stage="consistency",
        duration_ms=int((time.perf_counter() - started) * 1000),
        reason="deterministic_revision",
    )
    return revised


__all__ = [
    "ConsistencyGuard",
    "revise_candidate",
    "consistency_payload",
    "build_historical_style",
    "HistoricalStyleProfile",
]
