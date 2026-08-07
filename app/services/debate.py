"""Self-debate engine: adversarial internal reasoning before publication.

Does not overwrite Module 5 deterministic scores. Does not publish posts.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.logging import get_logger
from app.services.llm.base import CandidateTopic, LLMClient
from app.services.llm.prompts import (
    DEBATE_CORRECTION_PROMPT,
    DEBATE_SYSTEM_PROMPT,
    build_debate_user_prompt,
)
from app.services.memory.models import MemoryRecord
from app.services.memory.models import memories_for_prompt as _memories_for_prompt
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate

logger = get_logger(__name__)

DebateDecision = Literal["proceed", "reject", "uncertain"]

_STRAWMAN_PHRASES = (
    "some people may disagree",
    "others might think",
    "it depends",
    "everyone has an opinion",
    "hard to say",
)


class DebateDecisionEnum(str, Enum):
    PROCEED = "proceed"
    REJECT = "reject"
    UNCERTAIN = "uncertain"


class DebateLLMResponse(BaseModel):
    """Raw structured LLM debate payload (before grounding/guards)."""

    for_argument: str = Field(..., min_length=1)
    against_argument: str = Field(..., min_length=1)
    evidence_points: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    decision: DebateDecisionEnum
    confidence: float
    source_urls: list[str] = Field(default_factory=list)

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except Exception as exc:
            raise ValueError("confidence must be numeric") from exc
        return max(0.0, min(1.0, number))


class DebateResult(BaseModel):
    topic_id: str
    for_argument: str
    against_argument: str
    evidence_points: list[str]
    weaknesses: list[str]
    decision: DebateDecisionEnum
    confidence: float
    source_urls: list[str]
    error: str | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: object) -> float:
        try:
            number = float(value)  # type: ignore[arg-type]
        except Exception:
            return 0.0
        return max(0.0, min(1.0, number))


def ground_debate_urls(
    urls: list[str],
    allowed: set[str],
    url_by_normalized: dict[str, str],
) -> list[str]:
    grounded: list[str] = []
    for raw in urls:
        key = normalize_url(raw) or raw.strip()
        if key in allowed:
            grounded.append(url_by_normalized.get(key, raw))
        else:
            logger.info("debate_grounding_rejected", url=raw)
    return list(dict.fromkeys(grounded))


def is_strawman_against(against_argument: str) -> bool:
    text = against_argument.strip().lower()
    if len(text) < 40:
        return True
    return any(phrase in text for phrase in _STRAWMAN_PHRASES)


def apply_debate_guards(
    *,
    decision: DebateDecisionEnum,
    confidence: float,
    grounded_urls: list[str],
    final_score: float,
    against_argument: str,
    min_confidence: float,
    min_score_for_proceed: float,
) -> DebateDecisionEnum:
    """Deterministic post-LLM decision constraints."""
    result = decision
    if not grounded_urls:
        return DebateDecisionEnum.REJECT
    if result == DebateDecisionEnum.PROCEED and final_score < min_score_for_proceed:
        result = DebateDecisionEnum.UNCERTAIN
    if result == DebateDecisionEnum.PROCEED and confidence < min_confidence:
        result = DebateDecisionEnum.UNCERTAIN
    if result == DebateDecisionEnum.PROCEED and is_strawman_against(against_argument):
        result = DebateDecisionEnum.UNCERTAIN
    return result


def debate_payload(result: DebateResult, *, final_score: float, rank: int) -> dict[str, Any]:
    return {
        "version": 1,
        "for_argument": result.for_argument,
        "against_argument": result.against_argument,
        "evidence_points": result.evidence_points,
        "weaknesses": result.weaknesses,
        "decision": result.decision.value,
        "confidence": result.confidence,
        "source_urls": result.source_urls,
        "final_score": final_score,
        "rank": rank,
        "error": result.error,
    }


class SelfDebateService:
    """Runs structured self-debate for top-K selected topics only."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        min_confidence: float = 0.65,
        min_score_for_proceed: float = 0.60,
    ) -> None:
        self._llm = llm
        self._min_confidence = min_confidence
        self._min_score_for_proceed = min_score_for_proceed
        self.call_count = 0

    @property
    def available(self) -> bool:
        return bool(getattr(self._llm, "available", False))

    async def debate_topic(
        self,
        scored: ScoredCandidate,
        *,
        persona_name: str,
        persona_domain: str,
        research_items: list[ResearchItem],
        previous_titles: list[str],
        relevant_memories: list[MemoryRecord] | None = None,
        intelligence_context: dict | None = None,
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> DebateResult:
        started = time.perf_counter()
        topic_id = scored.topic_id
        logger.info(
            "debate_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            topic_id=topic_id,
            rank=scored.rank,
            final_score=scored.scores.final_score,
        )

        relevant = _research_for_candidate(scored.candidate, research_items)
        allowed = {(normalize_url(item.url) or item.url) for item in relevant}
        url_by_normalized = {
            (normalize_url(item.url) or item.url): item.url for item in relevant
        }

        if not self.available:
            logger.info(
                "debate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                reason="llm_unavailable",
            )
            return DebateResult(
                topic_id=topic_id,
                for_argument="",
                against_argument="",
                evidence_points=[],
                weaknesses=["LLM unavailable; debate not generated"],
                decision=DebateDecisionEnum.UNCERTAIN,
                confidence=0.0,
                source_urls=[],
                error="llm_unavailable",
            )

        if not allowed:
            return DebateResult(
                topic_id=topic_id,
                for_argument="",
                against_argument="No grounded research sources available for this candidate.",
                evidence_points=[],
                weaknesses=["missing_grounded_sources"],
                decision=DebateDecisionEnum.REJECT,
                confidence=0.0,
                source_urls=[],
                error="no_grounded_sources",
            )

        user_prompt = build_debate_user_prompt(
            persona_name=persona_name,
            persona_domain=persona_domain,
            candidate=scored.candidate,
            scores=scored.scores,
            rank=scored.rank,
            research_items=relevant,
            previous_titles=previous_titles,
            relevant_memories=_memories_for_prompt(relevant_memories or []),
            intelligence_context=intelligence_context,
        )

        try:
            self.call_count += 1
            raw = await self._llm.structured_output(
                system_prompt=DEBATE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=DebateLLMResponse,
                temperature=0.2,
            )
            if not isinstance(raw, DebateLLMResponse):
                raw = DebateLLMResponse.model_validate(raw)

            grounded_urls = ground_debate_urls(raw.source_urls, allowed, url_by_normalized)
            # Fall back to candidate grounded URLs if model omitted citations.
            if not grounded_urls:
                grounded_urls = ground_debate_urls(
                    scored.candidate.source_urls,
                    allowed,
                    url_by_normalized,
                )

            decision = apply_debate_guards(
                decision=raw.decision,
                confidence=raw.confidence,
                grounded_urls=grounded_urls,
                final_score=scored.scores.final_score,
                against_argument=raw.against_argument,
                min_confidence=self._min_confidence,
                min_score_for_proceed=self._min_score_for_proceed,
            )
            weaknesses = list(raw.weaknesses)
            if is_strawman_against(raw.against_argument):
                weaknesses.append("against_argument_appeared_weak_or_strawman")

            result = DebateResult(
                topic_id=topic_id,
                for_argument=raw.for_argument.strip(),
                against_argument=raw.against_argument.strip(),
                evidence_points=[p.strip() for p in raw.evidence_points if p.strip()],
                weaknesses=[w.strip() for w in weaknesses if w.strip()],
                decision=decision,
                confidence=raw.confidence,
                source_urls=grounded_urls,
            )
            logger.info(
                "debate_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                decision=result.decision.value,
                confidence=result.confidence,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            logger.info(
                "debate_decision",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                rank=scored.rank,
                final_score=scored.scores.final_score,
                decision=result.decision.value,
                confidence=result.confidence,
            )
            return result
        except Exception as exc:
            logger.exception(
                "debate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return DebateResult(
                topic_id=topic_id,
                for_argument="",
                against_argument="",
                evidence_points=[],
                weaknesses=[],
                decision=DebateDecisionEnum.UNCERTAIN,
                confidence=0.0,
                source_urls=[],
                error=str(exc),
            )


def _research_for_candidate(
    candidate: CandidateTopic,
    research_items: list[ResearchItem],
) -> list[ResearchItem]:
    wanted = {(normalize_url(url) or url) for url in candidate.source_urls}
    return [
        item
        for item in research_items
        if (normalize_url(item.url) or item.url) in wanted
    ]
