"""Persistent memory and belief evolution for Kestrel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.services.llm.base import LLMClient, UnavailableLLMClient
from app.services.memory.models import (
    MemoryCandidate,
    MemoryExtractionResponse,
    MemoryKindEnum,
    MemoryRecord,
    MemoryValue,
    memories_for_prompt,
    normalize_memory_key,
)
from app.services.memory.retrieval import MemoryRetrievalService, compute_salience
from app.services.memory.store import MemoryStore
from app.services.memory.update import MemoryUpdateService, apply_evidence_update
from app.orchestration.locks import utc_now
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate

if TYPE_CHECKING:
    from app.services.debate import DebateResult

logger = get_logger(__name__)


class MemoryService:
    """Facade: retrieval before debate + bounded extraction/update after debate."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        *,
        max_items: int = 10,
        max_source_urls: int = 10,
        min_confidence: float = 0.55,
        extraction_enabled: bool = True,
    ) -> None:
        self._llm = llm or UnavailableLLMClient()
        self._max_items = max_items
        self._retrieval = MemoryRetrievalService()
        self._update = MemoryUpdateService(
            self._llm,
            min_confidence=min_confidence,
            max_source_urls=max_source_urls,
            extraction_enabled=extraction_enabled,
        )

    def store_for(self, session: AsyncSession) -> MemoryStore:
        return MemoryStore(session)

    async def retrieve_for_topic(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        scored: ScoredCandidate,
        cycle_id: str | None = None,
    ) -> list[MemoryRecord]:
        try:
            store = self.store_for(session)
            all_memories = await store.list_for_agent(agent_id)
            return self._retrieval.retrieve_for_topic(
                all_memories,
                scored=scored,
                max_items=self._max_items,
                agent_id=agent_id,
                cycle_id=cycle_id,
            )
        except Exception:
            logger.exception(
                "memory_retrieval_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_retrieval",
            )
            return []

    async def extract_and_upsert(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        persona_name: str,
        persona_domain: str,
        debated: list[tuple[ScoredCandidate, DebateResult]],
        research_items: list[ResearchItem],
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            store = self.store_for(session)
            existing = await store.list_for_agent(agent_id)
            existing_urls: list[str] = []
            for record in existing:
                existing_urls.extend(record.value.supporting_source_urls)
                existing_urls.extend(record.value.contradicting_source_urls)
            return await self._update.extract_and_upsert(
                store,
                agent_id=agent_id,
                persona_name=persona_name,
                persona_domain=persona_domain,
                debated=debated,
                research_items=research_items,
                existing_source_urls=existing_urls,
                cycle_id=cycle_id,
            )
        except Exception as exc:
            logger.exception(
                "memory_extraction_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_extraction",
            )
            return {
                "status": "failed",
                "reason": str(exc),
                "created": 0,
                "updated": 0,
            }

    async def apply_contradiction_challenges(
        self,
        session: AsyncSession,
        *,
        challenges: list[dict[str, Any]],
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """Forward grounded contradiction evidence into existing belief-update logic.

        Does not overwrite beliefs. Uses Module 7 apply_evidence_update only.
        """
        if not challenges:
            return {"status": "skipped", "updated": 0, "forwarded": 0}
        store = self.store_for(session)
        updated = 0
        now = utc_now()
        for challenge in challenges:
            memory_id = str(challenge.get("memory_id") or "")
            if not memory_id:
                continue
            record = await store.get_by_id(memory_id)
            if record is None:
                continue
            urls = [str(u) for u in (challenge.get("source_urls") or []) if u]
            if not urls:
                continue
            new_value = apply_evidence_update(
                record.value,
                relation="contradicting",
                new_confidence=float(challenge.get("confidence") or 0.7),
                source_urls=urls,
                now=now,
                candidate_text=str(challenge.get("text") or ""),
            )
            await store.update(memory_id, value=new_value, now=now)
            updated += 1
            logger.info(
                "belief_challenge_forwarded",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_update",
                memory_id=memory_id,
                memory_key=record.key,
            )
        return {"status": "success", "updated": updated, "forwarded": len(challenges)}

    @property
    def extraction_call_count(self) -> int:
        return self._update.extraction_call_count


__all__ = [
    "MemoryService",
    "MemoryStore",
    "MemoryRetrievalService",
    "MemoryUpdateService",
    "MemoryRecord",
    "MemoryValue",
    "MemoryCandidate",
    "MemoryExtractionResponse",
    "MemoryKindEnum",
    "normalize_memory_key",
    "compute_salience",
    "memories_for_prompt",
]
