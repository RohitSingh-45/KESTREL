"""Persist adaptive signals via existing memory_items (kind=note).

Keys are namespaced as: adaptive:{signal_type}:{key}
No dedicated table / migration required.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.adaptive_learning.models import AdaptiveSignal
from app.services.memory.models import MemoryKindEnum, MemoryValue, normalize_memory_key
from app.services.memory.store import MemoryStore

logger = get_logger(__name__)

_KEY_PREFIX = "adaptive:"


def signal_memory_key(signal_type: str, key: str) -> str:
    raw = f"{_KEY_PREFIX}{signal_type}:{key}"
    return normalize_memory_key(raw)[:255]


class AdaptiveSignalStore:
    """Agent-scoped adaptive signal persistence using MemoryStore notes."""

    def __init__(self, session: AsyncSession) -> None:
        self._store = MemoryStore(session)

    async def list_signals(self, agent_id: str) -> list[AdaptiveSignal]:
        notes = await self._store.find_by_kind(agent_id, MemoryKindEnum.NOTE)
        signals: list[AdaptiveSignal] = []
        for record in notes:
            if not (record.key or "").startswith("adaptive ") and not (
                record.key or ""
            ).startswith("adaptive:"):
                # normalize_memory_key replaces ':' with space → "adaptive topic_pattern ..."
                if not (record.key or "").startswith("adaptive"):
                    continue
            value = record.value
            payload = {
                "signal_type": value.subject or "unknown",
                "key": value.object or record.key,
                "value": {},
                "confidence": value.confidence,
                "evidence_count": value.evidence_count,
                "first_observed_at": record.created_at,
                "last_observed_at": record.updated_at,
                "source_urls": list(value.supporting_source_urls),
                "history": list(value.history),
            }
            # Prefer structured fields embedded in text JSON-ish stance/history.
            if value.history:
                latest = value.history[-1]
                if isinstance(latest, dict) and "value" in latest:
                    payload["value"] = latest.get("value") or {}
                    payload["signal_type"] = latest.get("signal_type") or payload["signal_type"]
                    payload["key"] = latest.get("key") or payload["key"]
            # Also parse stance as JSON bag if present.
            if value.stance and value.stance.startswith("{"):
                import json

                try:
                    bag = json.loads(value.stance)
                    payload["value"] = bag.get("value") or payload["value"]
                    payload["signal_type"] = bag.get("signal_type") or payload["signal_type"]
                    payload["key"] = bag.get("key") or payload["key"]
                    if bag.get("first_observed_at"):
                        payload["first_observed_at"] = datetime.fromisoformat(
                            str(bag["first_observed_at"]).replace("Z", "+00:00")
                        )
                except Exception:
                    pass
            try:
                signals.append(AdaptiveSignal.model_validate(payload))
            except Exception:
                continue
        return signals

    async def upsert_signal(
        self,
        agent_id: str,
        signal: AdaptiveSignal,
        *,
        now: datetime | None = None,
    ) -> AdaptiveSignal:
        stamp = now or utc_now()
        import json

        mem_key = signal_memory_key(signal.signal_type, signal.key)
        existing = await self._store.find_by_key(
            agent_id,
            kind=MemoryKindEnum.NOTE,
            key=mem_key,
        )
        history = list(signal.history)
        history.append(
            {
                "at": stamp.isoformat(),
                "event": "observed",
                "signal_type": signal.signal_type,
                "key": signal.key,
                "value": signal.value,
                "confidence": signal.confidence,
            }
        )
        history = history[-20:]
        bag = {
            "signal_type": signal.signal_type,
            "key": signal.key,
            "value": signal.value,
            "first_observed_at": (
                (signal.first_observed_at or stamp).isoformat()
            ),
        }
        value = MemoryValue(
            kind=MemoryKindEnum.NOTE,
            text=f"adaptive:{signal.signal_type}:{signal.key}",
            subject=signal.signal_type,
            object=signal.key,
            stance=json.dumps(bag, ensure_ascii=True),
            confidence=signal.confidence,
            supporting_source_urls=list(signal.source_urls)[:10],
            evidence_count=max(1, signal.evidence_count),
            history=history,
        )
        if existing is None:
            record, _created = await self._store.upsert(
                agent_id=agent_id,
                kind=MemoryKindEnum.NOTE,
                key=mem_key,
                value=value,
                salience=min(1.0, 0.4 + 0.05 * signal.evidence_count),
                now=stamp,
            )
            logger.info(
                "adaptive_signal_created",
                agent_id=agent_id,
                signal_type=signal.signal_type,
                signal_key=signal.key,
                stage="adaptive_learning",
            )
        else:
            # Preserve first_observed via bag.
            try:
                old_bag = json.loads(existing.value.stance or "{}")
                if old_bag.get("first_observed_at"):
                    bag["first_observed_at"] = old_bag["first_observed_at"]
                    value = value.model_copy(
                        update={"stance": json.dumps(bag, ensure_ascii=True)}
                    )
            except Exception:
                pass
            merged_count = int(existing.value.evidence_count) + max(1, 1)
            value = value.model_copy(update={"evidence_count": merged_count})
            record = await self._store.update(
                existing.id,
                value=value,
                salience=min(1.0, 0.4 + 0.05 * merged_count),
                now=stamp,
            )
            logger.info(
                "adaptive_signal_updated",
                agent_id=agent_id,
                signal_type=signal.signal_type,
                signal_key=signal.key,
                stage="adaptive_learning",
            )
            assert record is not None
        return AdaptiveSignal(
            signal_type=signal.signal_type,
            key=signal.key,
            value=signal.value,
            confidence=signal.confidence,
            evidence_count=value.evidence_count,
            first_observed_at=signal.first_observed_at or stamp,
            last_observed_at=stamp,
            source_urls=signal.source_urls,
            history=history,
        )
