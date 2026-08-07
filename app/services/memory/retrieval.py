"""Deterministic memory retrieval (no LLM calls)."""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone

from app.logging import get_logger
from app.services.llm.base import CandidateTopic
from app.services.memory.models import MemoryRecord, clamp_unit
from app.services.scoring import ScoredCandidate

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 2}


def memory_text_blob(record: MemoryRecord) -> str:
    value = record.value
    parts = [
        record.key,
        value.text,
        value.subject or "",
        value.predicate or "",
        value.object or "",
        value.stance or "",
        " ".join(value.supporting_source_urls),
    ]
    return " ".join(parts)


def recency_factor(updated_at: datetime, *, now: datetime) -> float:
    stamp = updated_at
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (current - stamp).total_seconds() / 3600.0)
    if age_hours <= 24:
        return 1.0
    if age_hours <= 72:
        return 0.85
    if age_hours <= 168:
        return 0.7
    if age_hours <= 720:
        return 0.5
    return 0.35


def relevance_score(
    record: MemoryRecord,
    *,
    topic_tokens: set[str],
    topic_urls: set[str],
    now: datetime,
) -> float:
    mem_tokens = tokenize(memory_text_blob(record))
    if not topic_tokens:
        overlap = 0.0
    else:
        overlap = len(topic_tokens & mem_tokens) / len(topic_tokens)

    mem_urls = {
        u.strip().lower()
        for u in (
            record.value.supporting_source_urls + record.value.contradicting_source_urls
        )
        if u
    }
    url_overlap = 0.15 if topic_urls and (topic_urls & mem_urls) else 0.0
    recency = recency_factor(record.updated_at, now=now)
    return clamp_unit(
        0.50 * overlap
        + 0.20 * record.salience
        + 0.15 * recency
        + 0.15 * url_overlap
        + 0.10 * record.confidence
    )


def compute_salience(
    *,
    confidence: float,
    evidence_count: int,
    source_count: int,
    updated_at: datetime,
    now: datetime,
) -> float:
    """Deterministic salience separate from Module 5 topic scores."""
    evidence_signal = min(1.0, evidence_count / 5.0)
    source_signal = min(1.0, source_count / 4.0)
    recency = recency_factor(updated_at, now=now)
    return clamp_unit(
        0.35 * confidence
        + 0.25 * evidence_signal
        + 0.20 * source_signal
        + 0.20 * recency
    )


class MemoryRetrievalService:
    """Bounded deterministic retrieval of relevant memories for a topic."""

    def retrieve_for_topic(
        self,
        memories: list[MemoryRecord],
        *,
        scored: ScoredCandidate | None = None,
        candidate: CandidateTopic | None = None,
        title: str | None = None,
        max_items: int = 10,
        now: datetime | None = None,
        agent_id: str | None = None,
        cycle_id: str | None = None,
    ) -> list[MemoryRecord]:
        started = time.perf_counter()
        logger.info(
            "memory_retrieval_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="memory_retrieval",
            item_count=len(memories),
        )
        try:
            topic = scored.candidate if scored is not None else candidate
            topic_title = title or (scored.title if scored else "") or (
                topic.title if topic else ""
            )
            angle = topic.angle if topic else ""
            why_now = topic.why_now if topic else ""
            relevance = topic.relevance_to_persona if topic else ""
            urls = {u.strip().lower() for u in (topic.source_urls if topic else []) if u}
            topic_tokens = tokenize(f"{topic_title} {angle} {why_now} {relevance}")
            stamp = now or datetime.now(timezone.utc)

            ranked: list[tuple[float, MemoryRecord]] = []
            for record in memories:
                score = relevance_score(
                    record,
                    topic_tokens=topic_tokens,
                    topic_urls=urls,
                    now=stamp,
                )
                if score <= 0.05 and not (topic_tokens & tokenize(record.key)):
                    continue
                ranked.append((score, record))

            ranked.sort(
                key=lambda pair: (
                    pair[0],
                    pair[1].salience,
                    pair[1].updated_at.timestamp(),
                ),
                reverse=True,
            )
            selected = [record for _, record in ranked[: max(0, max_items)]]
            logger.info(
                "memory_retrieval_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_retrieval",
                item_count=len(selected),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return selected
        except Exception:
            logger.exception(
                "memory_retrieval_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_retrieval",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise
