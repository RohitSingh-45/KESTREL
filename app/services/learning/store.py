"""Persist learning signals via memory_items notes (no migration).

Keys: learning:{signal_type}:{key}
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.learning.models import LearningSignal
from app.services.learning.policy import validate_learning_signal
from app.services.memory.models import MemoryKindEnum, MemoryValue, normalize_memory_key
from app.services.memory.store import MemoryStore

logger = get_logger(__name__)

_PREFIX = "learning:"


def learning_memory_key(signal_type: str, key: str) -> str:
    return normalize_memory_key(f"{_PREFIX}{signal_type}:{key}")[:255]


class LearningStore:
    """Agent-scoped learning signal persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._store = MemoryStore(session)

    async def list_signals(self, agent_id: str) -> list[LearningSignal]:
        notes = await self._store.find_by_kind(agent_id, MemoryKindEnum.NOTE)
        signals: list[LearningSignal] = []
        for record in notes:
            key = record.key or ""
            if not key.startswith("learning"):
                continue
            parsed = self._record_to_signal(record)
            if parsed is not None:
                signals.append(parsed)
        return signals

    async def upsert_signal(
        self,
        agent_id: str,
        signal: LearningSignal,
        *,
        now: datetime | None = None,
    ) -> LearningSignal | None:
        stamp = now or utc_now()
        validated = validate_learning_signal(signal)
        if validated is None:
            return None
        mem_key = learning_memory_key(validated.signal_type, validated.key)
        bag: dict[str, Any] = validated.model_dump(mode="json")
        value = MemoryValue(
            kind=MemoryKindEnum.NOTE,
            text=f"learning:{validated.signal_type}:{validated.key}",
            subject=validated.signal_type,
            object=validated.key,
            stance=json.dumps(bag, ensure_ascii=True, default=str),
            confidence=validated.confidence,
            supporting_source_urls=[],
            evidence_count=max(1, validated.evidence_count),
            history=[
                {
                    "at": stamp.isoformat(),
                    "event": "learning_upsert",
                    "state": validated.state,
                    "confidence": validated.confidence,
                    "support_count": validated.support_count,
                    "failure_count": validated.failure_count,
                }
            ],
        )
        existing = await self._store.find_by_key(
            agent_id, kind=MemoryKindEnum.NOTE, key=mem_key
        )
        if existing is None:
            await self._store.create(
                agent_id=agent_id,
                kind=MemoryKindEnum.NOTE,
                key=mem_key,
                value=value,
                salience=min(1.0, 0.3 + validated.confidence * 0.5),
                now=stamp,
            )
            logger.info(
                "learning_signal_created",
                agent_id=agent_id,
                signal_type=validated.signal_type,
                signal_key=validated.key,
                stage="controlled_learning",
            )
        else:
            # Preserve history (bounded).
            old_history = list(existing.value.history or [])
            history = (old_history + value.history)[-20:]
            value = value.model_copy(update={"history": history})
            await self._store.update(
                existing.id,
                value=value,
                salience=min(1.0, 0.3 + validated.confidence * 0.5),
                now=stamp,
            )
            logger.info(
                "learning_signal_updated",
                agent_id=agent_id,
                signal_type=validated.signal_type,
                signal_key=validated.key,
                stage="controlled_learning",
            )
        return validated

    def _record_to_signal(self, record: object) -> LearningSignal | None:
        value = getattr(record, "value", None)
        if value is None:
            return None
        stance = getattr(value, "stance", None) or ""
        if not isinstance(stance, str) or not stance.startswith("{"):
            return None
        try:
            bag = json.loads(stance)
            return validate_learning_signal(LearningSignal.model_validate(bag))
        except Exception:
            return None
