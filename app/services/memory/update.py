"""Memory extraction, grounding, and belief/evidence updates."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.llm.base import LLMClient
from app.services.llm.prompts import (
    MEMORY_EXTRACTION_SYSTEM_PROMPT,
    build_memory_extraction_user_prompt,
)

if TYPE_CHECKING:
    from app.services.debate import DebateResult
from app.services.memory.models import (
    MemoryCandidate,
    MemoryExtractionResponse,
    MemoryKindEnum,
    MemoryRecord,
    MemoryValue,
    clamp_unit,
    normalize_memory_key,
)
from app.services.memory.retrieval import compute_salience
from app.services.memory.store import MemoryStore
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate

logger = get_logger(__name__)

_MAX_CONFIDENCE_DELTA = 0.15


def ground_memory_urls(
    urls: list[str],
    allowed: set[str],
    url_by_normalized: dict[str, str],
    *,
    max_urls: int = 10,
) -> list[str]:
    grounded: list[str] = []
    for raw in urls:
        key = normalize_url(raw) or raw.strip()
        if key in allowed:
            grounded.append(url_by_normalized.get(key, raw))
        else:
            logger.info(
                "memory_grounding_rejected",
                stage="memory_grounding",
                url=raw,
            )
    return list(dict.fromkeys(grounded))[:max_urls]


def apply_evidence_update(
    value: MemoryValue,
    *,
    relation: str,
    new_confidence: float,
    source_urls: list[str],
    now: datetime,
    candidate_text: str,
) -> MemoryValue:
    """Bounded deterministic belief/evidence update with history retention."""
    old_confidence = value.confidence
    supporting = list(value.supporting_source_urls)
    contradicting = list(value.contradicting_source_urls)
    history = list(value.history)

    quality = min(1.0, 0.4 + 0.15 * len(source_urls))
    signal = 0.08 * quality * new_confidence
    if relation == "contradicting":
        signal = 0.12 * quality * new_confidence
        delta = -min(_MAX_CONFIDENCE_DELTA, signal)
        for url in source_urls:
            if url not in contradicting:
                contradicting.append(url)
        logger.info(
            "memory_contradicting_evidence",
            stage="memory_update",
            memory_kind=value.kind.value,
            item_count=len(source_urls),
        )
        event = "contradicting"
    elif relation == "supporting":
        delta = min(_MAX_CONFIDENCE_DELTA, signal)
        for url in source_urls:
            if url not in supporting:
                supporting.append(url)
        logger.info(
            "memory_supporting_evidence",
            stage="memory_update",
            memory_kind=value.kind.value,
            item_count=len(source_urls),
        )
        event = "supporting"
    else:
        delta = 0.0
        for url in source_urls:
            if url not in supporting:
                supporting.append(url)
        event = "neutral"

    updated_confidence = clamp_unit(old_confidence + delta)
    history.append(
        {
            "at": now.isoformat(),
            "event": event,
            "old_confidence": old_confidence,
            "new_confidence": updated_confidence,
            "urls": source_urls,
            "text": candidate_text[:240],
        }
    )
    # Keep history bounded but retain evolution trail.
    history = history[-20:]

    text = value.text
    if candidate_text and len(candidate_text) > len(text):
        text = candidate_text

    return value.model_copy(
        update={
            "text": text,
            "confidence": updated_confidence,
            "supporting_source_urls": supporting[:20],
            "contradicting_source_urls": contradicting[:20],
            "evidence_count": int(value.evidence_count) + max(1, len(source_urls)),
            "history": history,
        }
    )


class MemoryUpdateService:
    """Extract, ground, and upsert memories (at most one LLM call per cycle)."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        min_confidence: float = 0.55,
        max_source_urls: int = 10,
        extraction_enabled: bool = True,
    ) -> None:
        self._llm = llm
        self._min_confidence = min_confidence
        self._max_source_urls = max_source_urls
        self._extraction_enabled = extraction_enabled
        self.extraction_call_count = 0

    @property
    def available(self) -> bool:
        return bool(getattr(self._llm, "available", False))

    async def extract_and_upsert(
        self,
        store: MemoryStore,
        *,
        agent_id: str,
        persona_name: str,
        persona_domain: str,
        debated: list[tuple[ScoredCandidate, "DebateResult"]],
        research_items: list[ResearchItem],
        existing_source_urls: list[str] | None = None,
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._extraction_enabled:
            return {"status": "skipped", "reason": "disabled", "created": 0, "updated": 0}
        if not debated:
            return {"status": "skipped", "reason": "no_debated_topics", "created": 0, "updated": 0}
        if not self.available:
            logger.info(
                "memory_extraction_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_extraction",
                reason="llm_unavailable",
            )
            return {
                "status": "skipped",
                "reason": "llm_unavailable",
                "created": 0,
                "updated": 0,
            }

        started = time.perf_counter()
        logger.info(
            "memory_extraction_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="memory_extraction",
            item_count=len(debated),
        )

        allowed_raw = [item.url for item in research_items] + list(existing_source_urls or [])
        allowed = {(normalize_url(url) or url) for url in allowed_raw if url}
        url_by_normalized = {
            (normalize_url(url) or url): url for url in allowed_raw if url
        }

        try:
            self.extraction_call_count += 1
            raw = await self._llm.structured_output(
                system_prompt=MEMORY_EXTRACTION_SYSTEM_PROMPT,
                user_prompt=build_memory_extraction_user_prompt(
                    persona_name=persona_name,
                    persona_domain=persona_domain,
                    debated=debated,
                    research_items=research_items,
                    allowed_urls=sorted(url_by_normalized.values()),
                ),
                schema=MemoryExtractionResponse,
                temperature=0.2,
            )
            if not isinstance(raw, MemoryExtractionResponse):
                raw = MemoryExtractionResponse.model_validate(raw)
        except Exception as exc:
            logger.exception(
                "memory_extraction_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_extraction",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return {
                "status": "failed",
                "reason": str(exc),
                "created": 0,
                "updated": 0,
            }

        now = utc_now()
        created = 0
        updated = 0
        rejected = 0

        for candidate in raw.memories:
            try:
                grounded = ground_memory_urls(
                    candidate.source_urls,
                    allowed,
                    url_by_normalized,
                    max_urls=self._max_source_urls,
                )
                if candidate.source_urls and not grounded:
                    rejected += 1
                    continue
                if candidate.confidence < self._min_confidence and not grounded:
                    rejected += 1
                    continue

                key = normalize_memory_key(candidate.key)
                if not key:
                    rejected += 1
                    continue

                existing = await store.find_by_key(
                    agent_id,
                    kind=candidate.kind,
                    key=key,
                )
                if existing is None:
                    value = _candidate_to_value(candidate, grounded)
                    salience = compute_salience(
                        confidence=value.confidence,
                        evidence_count=value.evidence_count,
                        source_count=len(value.supporting_source_urls),
                        updated_at=now,
                        now=now,
                    )
                    await store.create(
                        agent_id=agent_id,
                        kind=candidate.kind,
                        key=key,
                        value=value,
                        salience=salience,
                        now=now,
                    )
                    created += 1
                    logger.info(
                        "memory_created",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage="memory_update",
                        memory_kind=candidate.kind.value,
                        memory_key=key,
                    )
                else:
                    value = apply_evidence_update(
                        existing.value,
                        relation=candidate.relation,
                        new_confidence=candidate.confidence,
                        source_urls=grounded,
                        now=now,
                        candidate_text=candidate.value,
                    )
                    if candidate.stance:
                        value = value.model_copy(update={"stance": candidate.stance})
                    if candidate.kind == MemoryKindEnum.BELIEF:
                        value = value.model_copy(
                            update={
                                "subject": candidate.subject or value.subject,
                                "predicate": candidate.predicate or value.predicate,
                                "object": candidate.object or value.object,
                            }
                        )
                    salience = compute_salience(
                        confidence=value.confidence,
                        evidence_count=value.evidence_count,
                        source_count=len(value.supporting_source_urls)
                        + len(value.contradicting_source_urls),
                        updated_at=now,
                        now=now,
                    )
                    await store.update(
                        existing.id,
                        value=value,
                        salience=salience,
                        now=now,
                    )
                    updated += 1
                    logger.info(
                        "memory_updated",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage="memory_update",
                        memory_kind=candidate.kind.value,
                        memory_key=key,
                    )
            except Exception:
                logger.exception(
                    "memory_extraction_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="memory_update",
                    memory_key=getattr(candidate, "key", None),
                )
                rejected += 1

        logger.info(
            "memory_extraction_success",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="memory_extraction",
            item_count=created + updated,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return {
            "status": "success",
            "created": created,
            "updated": updated,
            "rejected": rejected,
            "extraction_llm_calls": 1,
        }


def _candidate_to_value(candidate: MemoryCandidate, grounded: list[str]) -> MemoryValue:
    supporting = grounded if candidate.relation != "contradicting" else []
    contradicting = grounded if candidate.relation == "contradicting" else []
    return MemoryValue(
        kind=candidate.kind,
        text=candidate.value.strip(),
        subject=candidate.subject,
        predicate=candidate.predicate,
        object=candidate.object,
        stance=candidate.stance,
        confidence=candidate.confidence,
        supporting_source_urls=supporting,
        contradicting_source_urls=contradicting,
        evidence_count=max(1, len(grounded)),
        history=[
            {
                "at": utc_now().isoformat(),
                "event": "created",
                "old_confidence": None,
                "new_confidence": candidate.confidence,
                "urls": grounded,
                "text": candidate.value[:240],
            }
        ],
    )

