"""ControlledLearningService: bounded strategy guidance from historical evidence."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.adaptive_learning.store import AdaptiveSignalStore
from app.services.evaluation.models import PostEvaluation
from app.services.evaluation.store import EvaluationStore
from app.services.learning.analyzer import LearningAnalyzer
from app.services.learning.models import LearningContext, LearningSignal
from app.services.learning.policy import assert_guidance_only
from app.services.learning.store import LearningStore
from app.services.learning.strategy import aggregate_observations
from app.services.reliability.secrets import sanitize_error

logger = get_logger(__name__)


class ControlledLearningService:
    """Derive bounded learning strategies. Never mutates persona/weights/safety."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        min_observations: int = 3,
        min_confidence: float = 0.65,
        max_signals: int = 20,
        max_context_items: int = 20,
        decay_days: int = 30,
        lookback_evaluations: int = 20,
    ) -> None:
        self._enabled = enabled
        self._min_observations = max(1, min_observations)
        self._min_confidence = max(0.0, min(1.0, min_confidence))
        self._max_signals = max(1, max_signals)
        self._max_context = max(1, max_context_items)
        self._decay_days = max(1, decay_days)
        self._lookback = max(1, lookback_evaluations)
        self._analyzer = LearningAnalyzer(lookback=self._lookback)
        self.llm_calls = 0  # deterministic only

    async def run(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        adaptive_meta: dict[str, Any] | None = None,
        quality_feedback: dict[str, str] | None = None,
        cycle_id: str | None = None,
        now: datetime | None = None,
    ) -> LearningContext:
        started = time.perf_counter()
        stamp = now or utc_now()
        logger.info(
            "controlled_learning_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="controlled_learning",
        )
        if not self._enabled:
            return LearningContext(status="skipped", reason="disabled")

        try:
            eval_store = EvaluationStore(session)
            learning_store = LearningStore(session)
            adaptive_store = AdaptiveSignalStore(session)

            evaluations = await eval_store.list_evaluations(agent_id)
            evaluations.sort(
                key=lambda e: e.evaluated_at or stamp,
                reverse=True,
            )
            adaptive_signals = await adaptive_store.list_signals(agent_id)
            existing = await learning_store.list_signals(agent_id)

            observations = self._analyzer.analyze(
                evaluations=evaluations[: self._lookback],
                adaptive_signals=adaptive_signals,
                quality_feedback=quality_feedback
                or (adaptive_meta or {}).get("quality_feedback")
                or {},
                adaptive_meta=adaptive_meta or {},
            )
            signals = aggregate_observations(
                observations,
                existing=existing,
                min_observations=self._min_observations,
                min_confidence=self._min_confidence,
                decay_days=self._decay_days,
                max_signals=self._max_signals,
                now=stamp,
            )
            persisted: list[LearningSignal] = []
            for signal in signals:
                saved = await learning_store.upsert_signal(
                    agent_id, signal, now=stamp
                )
                if saved is not None:
                    persisted.append(saved)
                    if saved.state == "active":
                        logger.info(
                            "learning_strategy_activated",
                            agent_id=agent_id,
                            cycle_id=cycle_id,
                            signal_type=saved.signal_type,
                            signal_key=saved.key,
                            stage="controlled_learning",
                        )

            context = self._build_context(persisted)
            logger.info(
                "controlled_learning_completed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="controlled_learning",
                item_count=len(persisted),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return context
        except Exception as exc:
            logger.exception(
                "controlled_learning_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="controlled_learning",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return LearningContext(status="failed", reason=sanitize_error(exc))

    async def observe_evaluation(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        evaluation: PostEvaluation,
        adaptive_meta: dict[str, Any] | None = None,
        cycle_id: str | None = None,
        now: datetime | None = None,
    ) -> LearningContext:
        """Post-evaluation learning update for next-cycle retrieval."""
        stamp = now or utc_now()
        try:
            learning_store = LearningStore(session)
            existing = await learning_store.list_signals(agent_id)
            observations = self._analyzer.analyze(
                evaluations=[evaluation],
                adaptive_signals=[],
                quality_feedback=dict(evaluation.feedback or {}),
                adaptive_meta=adaptive_meta or {},
            )
            signals = aggregate_observations(
                observations,
                existing=existing,
                min_observations=self._min_observations,
                min_confidence=self._min_confidence,
                decay_days=self._decay_days,
                max_signals=self._max_signals,
                now=stamp,
            )
            for signal in signals:
                await learning_store.upsert_signal(agent_id, signal, now=stamp)
            logger.info(
                "learning_observation_recorded",
                agent_id=agent_id,
                cycle_id=cycle_id,
                post_id=evaluation.post_id,
                stage="controlled_learning",
                item_count=len(signals),
            )
            return self._build_context(signals)
        except Exception as exc:
            logger.exception(
                "controlled_learning_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="controlled_learning",
            )
            return LearningContext(status="failed", reason=sanitize_error(exc))

    def _build_context(self, signals: list[LearningSignal]) -> LearningContext:
        limit = self._max_context
        active = [
            s
            for s in signals
            if s.state == "active" and s.influence > 0
        ][:limit]
        emerging_or_active = [
            s for s in signals if s.state in {"active", "emerging", "weakening"}
        ]

        def _by_type(signal_type: str) -> list[dict[str, Any]]:
            return [
                s.to_meta()
                for s in emerging_or_active
                if s.signal_type == signal_type
            ][:limit]

        recommendations: list[str] = []
        for signal in active + [
            s for s in emerging_or_active if s.state == "emerging"
        ]:
            rec = str((signal.value or {}).get("recommendation") or "").strip()
            if rec and rec not in recommendations:
                recommendations.append(rec)
            if len(recommendations) >= limit:
                break

        ctx = LearningContext(
            active_strategies=[s.to_meta() for s in active if s.signal_type == "strategy"],
            topic_preferences=_by_type("topic_preference"),
            source_preferences=_by_type("source_preference"),
            angle_preferences=_by_type("angle_preference"),
            novelty_preferences=_by_type("novelty_preference"),
            evidence_preferences=_by_type("evidence_preference"),
            recommendations=recommendations[:limit],
            relevant_signals=[s.to_meta() for s in emerging_or_active[:limit]],
            status="success",
        )
        # Ensure meta remains guidance-only.
        assert_guidance_only(ctx.to_meta())
        return ctx
