"""Persist post evaluations via memory_items notes (no migration).

Keys: evaluation:{post_id}
Also stores quality_feedback adaptive-compatible notes via AdaptiveSignalStore.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.adaptive_learning.store import AdaptiveSignalStore
from app.services.evaluation.feedback import feedback_to_adaptive_value
from app.services.evaluation.models import PostEvaluation, QualityFeedback
from app.services.memory.models import MemoryKindEnum, MemoryValue, normalize_memory_key
from app.services.memory.store import MemoryStore

logger = get_logger(__name__)

_EVAL_PREFIX = "evaluation:"


def evaluation_memory_key(post_id: str) -> str:
    return normalize_memory_key(f"{_EVAL_PREFIX}{post_id}")[:255]


class EvaluationStore:
    """Agent-scoped evaluation persistence using MemoryStore notes."""

    def __init__(self, session: AsyncSession) -> None:
        self._store = MemoryStore(session)
        self._adaptive = AdaptiveSignalStore(session)

    async def save_evaluation(
        self,
        evaluation: PostEvaluation,
        *,
        feedback: QualityFeedback | None = None,
        now: datetime | None = None,
    ) -> PostEvaluation:
        stamp = now or utc_now()
        evaluation.evaluated_at = evaluation.evaluated_at or stamp
        if feedback is not None:
            evaluation.feedback = dict(feedback.actions)

        bag: dict[str, Any] = evaluation.model_dump(mode="json")
        value = MemoryValue(
            kind=MemoryKindEnum.NOTE,
            text=f"evaluation:{evaluation.post_id}",
            subject="post_evaluation",
            object=evaluation.post_id,
            stance=json.dumps(bag, ensure_ascii=True, default=str),
            confidence=evaluation.overall_score,
            supporting_source_urls=list(evaluation.source_urls)[:10],
            evidence_count=1,
            history=[
                {
                    "at": stamp.isoformat(),
                    "event": "evaluated",
                    "overall_score": evaluation.overall_score,
                    "quality_class": evaluation.quality_class,
                }
            ],
        )
        mem_key = evaluation_memory_key(evaluation.post_id)
        existing = await self._store.find_by_key(
            evaluation.agent_id,
            kind=MemoryKindEnum.NOTE,
            key=mem_key,
        )
        if existing is None:
            await self._store.create(
                agent_id=evaluation.agent_id,
                kind=MemoryKindEnum.NOTE,
                key=mem_key,
                value=value,
                salience=min(1.0, 0.35 + evaluation.overall_score * 0.4),
                now=stamp,
            )
        else:
            await self._store.update(
                existing.id,
                value=value,
                salience=min(1.0, 0.35 + evaluation.overall_score * 0.4),
                now=stamp,
            )

        if feedback is not None:
            await self._adaptive.upsert_signal(
                evaluation.agent_id,
                AdaptiveSignal(
                    signal_type="quality_feedback",
                    key="latest",
                    value=feedback_to_adaptive_value(feedback),
                    confidence=feedback.confidence,
                    evidence_count=feedback.evidence_count,
                    first_observed_at=stamp,
                    last_observed_at=stamp,
                    source_urls=list(evaluation.source_urls)[:5],
                    history=[
                        {
                            "at": stamp.isoformat(),
                            "post_id": evaluation.post_id,
                            "actions": dict(feedback.actions),
                            "overall_score": evaluation.overall_score,
                        }
                    ],
                ),
                now=stamp,
            )
            # Also persist per-dimension signals for decay aggregation.
            for action_key, action in list(feedback.actions.items())[:10]:
                await self._adaptive.upsert_signal(
                    evaluation.agent_id,
                    AdaptiveSignal(
                        signal_type="quality_feedback",
                        key=action_key,
                        value={"action": action, "post_id": evaluation.post_id},
                        confidence=feedback.confidence,
                        evidence_count=1,
                        first_observed_at=stamp,
                        last_observed_at=stamp,
                    ),
                    now=stamp,
                )
        return evaluation

    async def get_evaluation(
        self, agent_id: str, post_id: str
    ) -> PostEvaluation | None:
        record = await self._store.find_by_key(
            agent_id,
            kind=MemoryKindEnum.NOTE,
            key=evaluation_memory_key(post_id),
        )
        if record is None:
            return None
        return self._record_to_evaluation(record)

    async def list_evaluations(self, agent_id: str) -> list[PostEvaluation]:
        notes = await self._store.find_by_kind(agent_id, MemoryKindEnum.NOTE)
        out: list[PostEvaluation] = []
        for record in notes:
            key = record.key or ""
            if not key.startswith("evaluation"):
                continue
            if (record.value.subject or "") != "post_evaluation" and not key.startswith(
                "evaluation"
            ):
                continue
            parsed = self._record_to_evaluation(record)
            if parsed is not None:
                out.append(parsed)
        return out

    async def list_quality_feedback(self, agent_id: str) -> list[QualityFeedback]:
        signals = await self._adaptive.list_signals(agent_id)
        items: list[QualityFeedback] = []
        for signal in signals:
            if signal.signal_type != "quality_feedback":
                continue
            actions = {}
            if signal.key == "latest":
                actions = dict((signal.value or {}).get("actions") or {})
            elif signal.value.get("action"):
                actions = {signal.key: signal.value.get("action")}
            if not actions:
                continue
            items.append(
                QualityFeedback(
                    actions=actions,  # type: ignore[arg-type]
                    confidence=signal.confidence,
                    evidence_count=signal.evidence_count,
                    last_observed_at=signal.last_observed_at,
                    influence=signal.influence,
                )
            )
        return items

    def _record_to_evaluation(self, record: object) -> PostEvaluation | None:
        value = getattr(record, "value", None)
        if value is None:
            return None
        stance = getattr(value, "stance", None) or ""
        if not isinstance(stance, str) or not stance.startswith("{"):
            return None
        try:
            bag = json.loads(stance)
            return PostEvaluation.model_validate(bag)
        except Exception:
            return None
