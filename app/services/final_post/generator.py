"""Final post generation via LLMClient (no publishing)."""

from __future__ import annotations

import time
from typing import Any

from app.logging import get_logger
from app.services.consistency.models import ConsistencyResult
from app.services.debate import DebateResult
from app.services.final_post.models import (
    FinalGenerationOutcome,
    FinalPostContext,
    GeneratedPost,
)
from app.services.final_post.prompts import (
    FINAL_POST_SYSTEM_PROMPT,
    build_final_post_user_prompt,
    build_repair_user_prompt,
)
from app.services.final_post.validator import FinalPostValidator
from app.services.llm.base import LLMClient
from app.services.memory.models import MemoryRecord, memories_for_prompt
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate

logger = get_logger(__name__)


class FinalPostGenerator:
    """Generates and validates a GeneratedPost. Never inserts into posts."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        validator: FinalPostValidator | None = None,
        max_repairs: int = 1,
    ) -> None:
        self._llm = llm
        self._validator = validator or FinalPostValidator()
        self._max_repairs = max(0, max_repairs)
        self.call_count = 0

    @property
    def available(self) -> bool:
        return bool(getattr(self._llm, "available", False))

    def build_context(
        self,
        *,
        persona_name: str,
        persona_domain: str,
        scored: ScoredCandidate,
        debate: DebateResult,
        consistency: ConsistencyResult,
        memories: list[MemoryRecord],
        research_items: list[ResearchItem],
        alternatives: list[ScoredCandidate],
        previous_titles: list[str] | None = None,
        intelligence: dict[str, Any] | None = None,
    ) -> FinalPostContext:
        relevant = [
            item
            for item in research_items
            if (normalize_url(item.url) or item.url)
            in {(normalize_url(u) or u) for u in scored.candidate.source_urls}
        ]
        if not relevant:
            relevant = research_items[:5]
        grounded_urls = [item.url for item in relevant]
        # Also allow debate-grounded URLs present in research.
        research_norm = {
            (normalize_url(item.url) or item.url): item.url for item in research_items
        }
        for url in debate.source_urls:
            key = normalize_url(url) or url
            if key in research_norm and research_norm[key] not in grounded_urls:
                grounded_urls.append(research_norm[key])

        return FinalPostContext(
            persona_name=persona_name,
            persona_domain=persona_domain,
            topic={
                "title": scored.title,
                "angle": scored.candidate.angle,
                "why_now": scored.candidate.why_now,
                "relevance_to_persona": scored.candidate.relevance_to_persona,
                "final_score": scored.scores.final_score,
                "rank": scored.rank,
                "source_urls": list(scored.candidate.source_urls),
            },
            debate={
                "decision": debate.decision.value
                if hasattr(debate.decision, "value")
                else str(debate.decision),
                "confidence": debate.confidence,
                "for_argument": debate.for_argument,
                "against_argument": debate.against_argument,
                "evidence_points": debate.evidence_points,
                "weaknesses": debate.weaknesses,
                "source_urls": debate.source_urls,
            },
            consistency={
                "decision": consistency.decision.value
                if hasattr(consistency.decision, "value")
                else str(consistency.decision),
                "overall_score": consistency.overall_score,
                "signals": consistency.signals.model_dump(),
                "drift_flags": consistency.drift_flags,
                "belief_change_accepted": consistency.belief_change_accepted,
                "reasons": consistency.reasons,
            },
            memories=memories_for_prompt(memories),
            alternatives=[
                {
                    "title": alt.title,
                    "rank": alt.rank,
                    "final_score": alt.scores.final_score,
                    "decision": alt.decision,
                }
                for alt in alternatives[:5]
            ],
            research=[
                {
                    "title": item.title,
                    "summary": (item.summary or "")[:240],
                    "url": item.url,
                    "published_at": item.published_at.isoformat()
                    if item.published_at
                    else None,
                }
                for item in relevant[:8]
            ],
            grounded_urls=grounded_urls,
            previous_titles=list(previous_titles or [])[:10],
            intelligence=dict(intelligence or {}),
        )

    async def generate(
        self,
        context: FinalPostContext,
        *,
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> FinalGenerationOutcome:
        started = time.perf_counter()
        stage = "final_generation"
        logger.info(
            "final_generation_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage=stage,
            source_count=len(context.grounded_urls),
        )

        if not self.available:
            logger.info(
                "final_generation_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage=stage,
                reason="llm_unavailable",
            )
            return FinalGenerationOutcome(
                status="skipped",
                error="llm_unavailable",
                llm_calls=0,
            )

        if not context.grounded_urls:
            return FinalGenerationOutcome(
                status="rejected",
                error="no_grounded_urls",
                validation_errors=["no_grounded_source"],
                llm_calls=0,
            )

        grounded_set = set(context.grounded_urls)
        url_by_norm = {
            (normalize_url(url) or url): url for url in context.grounded_urls
        }

        try:
            draft = await self._call_llm(
                system_prompt=FINAL_POST_SYSTEM_PROMPT,
                user_prompt=build_final_post_user_prompt(context),
            )
            draft = self._apply_grounding(draft, grounded_set, url_by_norm)
            errors = self._validator.validate(
                draft,
                grounded_urls=grounded_set,
                previous_titles=context.previous_titles,
                url_by_normalized=url_by_norm,
            )
            repaired = False
            if errors:
                logger.info(
                    "final_generation_validation_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage=stage,
                    reason=",".join(errors),
                )
                if self._max_repairs < 1:
                    return FinalGenerationOutcome(
                        status="rejected",
                        validation_errors=errors,
                        llm_calls=self.call_count,
                        error="validation_failed",
                    )
                logger.info(
                    "final_generation_repair_started",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage=stage,
                )
                try:
                    draft = await self._call_llm(
                        system_prompt=FINAL_POST_SYSTEM_PROMPT,
                        user_prompt=build_repair_user_prompt(
                            context,
                            previous=draft.model_dump(),
                            validation_errors=errors,
                        ),
                    )
                    draft = self._apply_grounding(draft, grounded_set, url_by_norm)
                    errors = self._validator.validate(
                        draft,
                        grounded_urls=grounded_set,
                        previous_titles=context.previous_titles,
                        url_by_normalized=url_by_norm,
                    )
                    repaired = True
                    if errors:
                        logger.info(
                            "final_generation_repair_failed",
                            agent_id=agent_id,
                            cycle_id=cycle_id,
                            stage=stage,
                            reason=",".join(errors),
                        )
                        return FinalGenerationOutcome(
                            status="rejected",
                            validation_errors=errors,
                            repaired=True,
                            llm_calls=self.call_count,
                            error="validation_failed_after_repair",
                        )
                    logger.info(
                        "final_generation_repair_success",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                    )
                except Exception as exc:
                    logger.exception(
                        "final_generation_repair_failed",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                    )
                    return FinalGenerationOutcome(
                        status="rejected",
                        validation_errors=errors,
                        repaired=False,
                        llm_calls=self.call_count,
                        error=str(exc),
                    )

            logger.info(
                "final_generation_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage=stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
                source_count=len(draft.source_urls),
            )
            return FinalGenerationOutcome(
                status="success",
                generated_post=draft,
                repaired=repaired,
                llm_calls=self.call_count,
            )
        except Exception as exc:
            logger.exception(
                "final_generation_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage=stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return FinalGenerationOutcome(
                status="failed",
                error=str(exc),
                llm_calls=self.call_count,
            )

    async def _call_llm(self, *, system_prompt: str, user_prompt: str) -> GeneratedPost:
        self.call_count += 1
        raw = await self._llm.structured_output(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=GeneratedPost,
            temperature=0.3,
        )
        if isinstance(raw, GeneratedPost):
            return raw
        return GeneratedPost.model_validate(raw)

    def _apply_grounding(
        self,
        post: GeneratedPost,
        grounded: set[str],
        url_by_norm: dict[str, str],
    ) -> GeneratedPost:
        cleaned = self._validator.ground_urls(post.source_urls, grounded, url_by_norm)
        return post.model_copy(update={"source_urls": cleaned})


def outcome_meta(outcome: FinalGenerationOutcome) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": outcome.status,
        "repaired": outcome.repaired,
        "validation_errors": outcome.validation_errors,
        "error": outcome.error,
        "llm_calls": outcome.llm_calls,
    }
    if outcome.generated_post is not None:
        payload["generated_post"] = outcome.generated_post.model_dump()
    return payload
