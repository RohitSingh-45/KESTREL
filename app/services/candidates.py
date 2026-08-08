"""Candidate topic generation and source grounding."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from app.db.repositories.topics import TopicRepository
from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.ai_security.domains import is_valid_domain
from app.services.ai_security.filter import assign_domain_from_research
from app.services.llm.base import CandidateTopic, CandidateTopicList, LLMClient
from app.services.llm.prompts import (
    SYSTEM_PROMPT,
    build_candidate_user_prompt,
    build_system_prompt,
)
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem

logger = get_logger(__name__)


class CandidateGenerationService:
    """Turns research items into grounded CandidateTopic objects via LLM."""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    @property
    def llm_available(self) -> bool:
        return bool(getattr(self._llm, "available", False))

    async def generate(
        self,
        *,
        persona_name: str,
        persona_domain: str,
        research_items: list[ResearchItem],
        agent_id: str | None = None,
        cycle_id: str | None = None,
        max_candidates: int = 5,
    ) -> list[CandidateTopic]:
        if not research_items:
            return []
        if not self.llm_available:
            raise RuntimeError("LLM unavailable")

        started = time.perf_counter()
        logger.info(
            "llm_candidate_generation_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            item_count=len(research_items),
        )

        allowed = {
            normalize_url(item.url) or item.url
            for item in research_items
        }
        # Preserve original URL forms for grounding replacement.
        url_by_normalized = {
            (normalize_url(item.url) or item.url): item.url for item in research_items
        }

        user_prompt = build_candidate_user_prompt(
            persona_name=persona_name,
            persona_domain=persona_domain,
            research_items=research_items,
            max_candidates=max_candidates,
        )

        try:
            system_prompt = build_system_prompt(persona_name, persona_domain)
            parsed = await self._llm.structured_output(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                schema=CandidateTopicList,
                temperature=0.2,
            )
            grounded = ground_candidates(
                parsed.candidates,
                allowed,
                url_by_normalized,
                research_items=research_items,
            )
            logger.info(
                "llm_candidate_generation_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                item_count=len(grounded),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return grounded
        except Exception:
            logger.exception(
                "llm_candidate_generation_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            raise


def ground_candidates(
    candidates: list[CandidateTopic],
    allowed_normalized_urls: set[str],
    url_by_normalized: dict[str, str],
    *,
    research_items: list[ResearchItem] | None = None,
) -> list[CandidateTopic]:
    """Ensure every candidate URL exists in research input and has an AI-security domain."""
    research_items = research_items or []
    grounded: list[CandidateTopic] = []
    for candidate in candidates:
        valid_urls: list[str] = []
        for raw in candidate.source_urls:
            normalized = normalize_url(raw) or raw.strip()
            if normalized in allowed_normalized_urls:
                valid_urls.append(url_by_normalized.get(normalized, raw))
            else:
                logger.info(
                    "candidate_grounding_rejected",
                    url=raw,
                    title=candidate.title,
                )
        if not valid_urls:
            logger.info(
                "candidate_grounding_rejected",
                reason="no_valid_sources",
                title=candidate.title,
            )
            continue
        domain = candidate.ai_security_domain
        if not is_valid_domain(domain):
            domain = assign_domain_from_research(valid_urls, research_items)
        if not is_valid_domain(domain):
            logger.info(
                "candidate_grounding_rejected",
                reason="no_ai_security_domain",
                title=candidate.title,
            )
            continue
        grounded.append(
            candidate.model_copy(
                update={
                    "source_urls": list(dict.fromkeys(valid_urls)),
                    "ai_security_domain": domain,
                }
            )
        )
    return grounded


async def persist_candidates(
    topics: TopicRepository,
    *,
    agent_id: str,
    candidates: list[CandidateTopic],
) -> list[str]:
    """Persist candidates into topics table with status=candidate."""
    ids: list[str] = []
    now = utc_now()
    for candidate in candidates:
        topic_id = str(uuid.uuid4())
        scores: dict[str, Any] = {
            "confidence": candidate.confidence,
            "angle": candidate.angle,
            "why_now": candidate.why_now,
            "relevance_to_persona": candidate.relevance_to_persona,
            "source_urls": candidate.source_urls,
            "ai_security_domain": candidate.ai_security_domain,
        }
        await topics.create_topic(
            topic_id=topic_id,
            agent_id=agent_id,
            title=candidate.title[:512],
            status="candidate",
            created_at=now,
            scores_json=json.dumps(scores),
            debate_json=None,
            embedding_json=None,
            decided_at=None,
            ai_security_domain=candidate.ai_security_domain,
        )
        ids.append(topic_id)
    return ids
