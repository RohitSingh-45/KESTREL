"""PostEvaluator: deterministic-first evaluation with optional LLM reflection."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.evaluation.feedback import build_quality_feedback
from app.services.evaluation.models import (
    DimensionScores,
    EvaluationLLMResponse,
    PostEvaluation,
)
from app.services.evaluation.rubric import (
    classify_quality,
    extract_post_urls,
    reuse_consistency_score,
    reuse_source_diversity_score,
    score_content_quality_adjustments,
    score_evidence_quality,
    score_novelty,
    score_rationale_quality,
    score_reasoning_quality,
    score_relevance,
    weighted_overall,
)
from app.services.evaluation.store import EvaluationStore
from app.services.llm.base import LLMClient, UnavailableLLMClient
from app.services.research.base import normalize_url
from app.services.reliability.secrets import sanitize_error

logger = get_logger(__name__)

EVALUATION_SYSTEM_PROMPT = """You are a critical evaluator for Kestrel, an AI Security persona.
You critique an already published post. You do NOT rewrite or publish anything.

Rules:
- Do not invent facts or sources.
- Only use the supplied post, rationale, source URLs, and deterministic scores.
- Do not change persona identity or domain.
- Do not override deterministic consistency/safety results.
- Be honest: praise only when earned; record weaknesses clearly.
- Answer: strongest part, weakest part, least-supported claim, novelty, evidence strength,
  overstated certainty, repeated story/angle, and concrete next improvements.
"""


class PostEvaluator:
    """Evaluate one published post. Never creates or deletes posts."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        *,
        enabled: bool = True,
        reflection_enabled: bool = True,
        excellent_threshold: float = 0.85,
        good_threshold: float = 0.70,
        acceptable_threshold: float = 0.55,
        weak_threshold: float = 0.40,
    ) -> None:
        self._llm = llm_client or UnavailableLLMClient()
        self._enabled = enabled
        self._reflection_enabled = reflection_enabled
        self._excellent = excellent_threshold
        self._good = good_threshold
        self._acceptable = acceptable_threshold
        self._weak = weak_threshold
        self.llm_calls = 0

    async def evaluate(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        post: object,
        cycle_id: str | None = None,
        grounded_urls: list[str] | set[str] | None = None,
        research_urls: list[str] | set[str] | None = None,
        previous_posts: list[object] | None = None,
        consistency_meta: dict[str, Any] | None = None,
        intelligence_meta: dict[str, Any] | None = None,
        persona_domain: str = "AI Security",
        persist: bool = True,
        now: datetime | None = None,
    ) -> PostEvaluation:
        started = time.perf_counter()
        stamp = now or utc_now()
        post_id = str(getattr(post, "id", "") or "")
        logger.info(
            "evaluation_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            post_id=post_id,
            stage="evaluation",
        )
        if not self._enabled:
            return PostEvaluation(
                post_id=post_id or "unknown",
                agent_id=agent_id,
                cycle_id=cycle_id,
                evaluated_at=stamp,
                status="skipped",
                reason="disabled",
                reflection_status="skipped",
            )
        try:
            text = str(getattr(post, "text", "") or "")
            rationale = str(getattr(post, "rationale", "") or "")
            topic = getattr(post, "topic", None)
            topic_s = str(topic) if topic else None
            post_urls = extract_post_urls(post)
            allowed = set(grounded_urls or post_urls)
            allowed_norm = {normalize_url(u) or u.strip() for u in allowed if str(u).strip()}

            # Reject invented / ungrounded URLs from the evaluation record.
            clean_urls: list[str] = []
            for raw in post_urls:
                key = normalize_url(raw) or raw.strip()
                if key in allowed_norm:
                    clean_urls.append(raw)
                else:
                    logger.info(
                        "evaluation_grounding_rejected",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        post_id=post_id,
                        stage="evaluation",
                        url=raw,
                    )

            evidence, s1, w1 = score_evidence_quality(
                post_urls=post_urls,
                grounded_urls=allowed,
                research_urls=research_urls or allowed,
            )
            relevance, s2, w2 = score_relevance(
                text=text, topic=topic_s, persona_domain=persona_domain
            )
            novelty, s3, w3 = score_novelty(
                text=text,
                topic=topic_s,
                previous_posts=list(previous_posts or []),
            )
            reasoning, s4, w4 = score_reasoning_quality(text=text, rationale=rationale)
            rationale_q, s5, w5 = score_rationale_quality(rationale=rationale, text=text)
            content_adj, s6, w6 = score_content_quality_adjustments(text=text)
            diversity = reuse_source_diversity_score(intelligence_meta)
            consistency = reuse_consistency_score(consistency_meta)

            # Apply content hygiene as soft dampener on reasoning/rationale.
            reasoning = max(0.0, min(1.0, reasoning * (0.7 + 0.3 * content_adj)))
            rationale_q = max(0.0, min(1.0, rationale_q * (0.7 + 0.3 * content_adj)))

            dims = DimensionScores(
                evidence_quality=evidence,
                relevance_score=relevance,
                novelty_score=novelty,
                reasoning_quality=reasoning,
                source_diversity_score=diversity,
                consistency_score=consistency,
                rationale_quality=rationale_q,
            )
            overall = weighted_overall(dims)
            quality_class = classify_quality(
                overall,
                excellent=self._excellent,
                good=self._good,
                acceptable=self._acceptable,
                weak=self._weak,
            )

            strengths = list(dict.fromkeys(s1 + s2 + s3 + s4 + s5 + s6))[:8]
            weaknesses = list(dict.fromkeys(w1 + w2 + w3 + w4 + w5 + w6))[:8]
            improvement_actions = self._deterministic_improvements(weaknesses, dims)

            reflection_status = "skipped"
            if self._reflection_enabled:
                reflection_status, refl = await self._reflect(
                    text=text,
                    rationale=rationale,
                    source_urls=clean_urls,
                    dims=dims,
                    overall=overall,
                    quality_class=quality_class,
                    strengths=strengths,
                    weaknesses=weaknesses,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    post_id=post_id,
                )
                if refl is not None:
                    strengths = list(
                        dict.fromkeys(strengths + list(refl.strengths)[:5])
                    )[:8]
                    weaknesses = list(
                        dict.fromkeys(weaknesses + list(refl.weaknesses)[:5])
                    )[:8]
                    improvement_actions = list(
                        dict.fromkeys(
                            improvement_actions + list(refl.improvement_actions)[:5]
                        )
                    )[:8]

            evaluation = PostEvaluation(
                post_id=post_id or "unknown",
                agent_id=agent_id,
                cycle_id=cycle_id,
                evaluated_at=stamp,
                overall_score=overall,
                quality_class=quality_class,
                evidence_quality=dims.evidence_quality,
                relevance_score=dims.relevance_score,
                novelty_score=dims.novelty_score,
                reasoning_quality=dims.reasoning_quality,
                source_diversity_score=dims.source_diversity_score,
                consistency_score=dims.consistency_score,
                rationale_quality=dims.rationale_quality,
                strengths=strengths,
                weaknesses=weaknesses,
                improvement_actions=improvement_actions,
                source_urls=clean_urls[:10],
                reflection_status=reflection_status,
                status="success",
            )
            feedback = build_quality_feedback(evaluation, now=stamp)
            evaluation.feedback = dict(feedback.actions)

            if persist:
                store = EvaluationStore(session)
                await store.save_evaluation(evaluation, feedback=feedback, now=stamp)

            logger.info(
                "evaluation_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                post_id=post_id,
                overall_score=overall,
                quality_class=quality_class,
                duration_ms=int((time.perf_counter() - started) * 1000),
                stage="evaluation",
            )
            return evaluation
        except Exception as exc:
            logger.exception(
                "evaluation_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                post_id=post_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                stage="evaluation",
            )
            return PostEvaluation(
                post_id=post_id or "unknown",
                agent_id=agent_id,
                cycle_id=cycle_id,
                evaluated_at=stamp,
                status="failed",
                reason=sanitize_error(exc),
                reflection_status="failed",
            )

    async def _reflect(
        self,
        *,
        text: str,
        rationale: str,
        source_urls: list[str],
        dims: DimensionScores,
        overall: float,
        quality_class: str,
        strengths: list[str],
        weaknesses: list[str],
        agent_id: str,
        cycle_id: str | None,
        post_id: str,
    ) -> tuple[str, EvaluationLLMResponse | None]:
        if not self._llm.available:
            return "unavailable", None

        logger.info(
            "evaluation_reflection_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            post_id=post_id,
            stage="evaluation",
        )
        user_prompt = (
            f"Published post:\n{text}\n\n"
            f"Rationale:\n{rationale}\n\n"
            f"Allowed source URLs:\n{source_urls}\n\n"
            f"Deterministic scores:\n{dims.to_dict()}\n"
            f"overall={overall:.3f} class={quality_class}\n"
            f"deterministic_strengths={strengths}\n"
            f"deterministic_weaknesses={weaknesses}\n\n"
            "Return structured critique only."
        )
        try:
            self.llm_calls += 1
            raw = await self._llm.structured_output(
                system_prompt=EVALUATION_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                schema=EvaluationLLMResponse,
                temperature=0.1,
            )
            if not isinstance(raw, EvaluationLLMResponse):
                raw = EvaluationLLMResponse.model_validate(raw)
            # Drop any invented source-looking lines from free text fields.
            grounded = set(source_urls)
            raw.strengths = [s for s in raw.strengths if not self._looks_like_url(s) or s in grounded][
                :5
            ]
            raw.weaknesses = raw.weaknesses[:5]
            raw.improvement_actions = raw.improvement_actions[:5]
            logger.info(
                "evaluation_reflection_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                post_id=post_id,
                stage="evaluation",
            )
            return "success", raw
        except Exception as exc:
            logger.exception(
                "evaluation_reflection_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                post_id=post_id,
                stage="evaluation",
                reason=sanitize_error(exc),
            )
            return "failed", None

    @staticmethod
    def _looks_like_url(text: str) -> bool:
        t = text.strip().lower()
        return t.startswith("http://") or t.startswith("https://")

    @staticmethod
    def _deterministic_improvements(
        weaknesses: list[str], dims: DimensionScores
    ) -> list[str]:
        actions: list[str] = []
        mapping = {
            "no_source_urls": "Include grounded research URLs in published posts.",
            "ungrounded_source_urls": "Only cite sources present in research/final grounding.",
            "domain_drift": "Keep focus on AI Security / agent safety topics.",
            "weak_security_relevance": "Tighten AI Security framing and agentic relevance.",
            "repeated_topic": "Prefer fresher AI Security angles over repeated topics.",
            "repeated_angle": "Vary analytical angle across cycles.",
            "repeated_phrasing": "Reduce phrasing overlap with recent posts.",
            "missing_rationale": "Always include a selection rationale.",
            "selection_not_explained": "Explain why this topic was selected over alternatives.",
            "evidence_not_referenced": "Reference evidence quality in the rationale.",
            "excessive_hype": "Reduce hype and absolute certainty language.",
            "unsupported_certainty": "Qualify claims when evidence is incomplete.",
        }
        for w in weaknesses:
            if w in mapping:
                actions.append(mapping[w])
        if dims.novelty_score < 0.65:
            actions.append("Increase novelty versus recent Kestrel posts.")
        if dims.source_diversity_score < 0.65:
            actions.append("Diversify source domains across research inputs.")
        if dims.evidence_quality < 0.65:
            actions.append("Strengthen evidence discipline and grounding.")
        return list(dict.fromkeys(actions))[:8]
