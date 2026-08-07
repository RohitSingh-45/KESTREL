"""PublishingService: sole application path that inserts into posts."""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.agents import AgentRepository
from app.db.repositories.posts import PostRepository
from app.logging import get_logger
from app.services.final_post.models import GeneratedPost, is_valid_http_url
from app.services.publishing.models import PublicationResult, PublishedPost
from app.services.research.base import normalize_url

logger = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_post_text(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text.strip().lower())


def compute_content_hash(text: str, source_urls: list[str]) -> str:
    """Deterministic SHA-256 over normalized text + sorted normalized URLs."""
    urls = sorted(
        {
            (normalize_url(url) or url.strip().lower())
            for url in source_urls
            if url and url.strip()
        }
    )
    payload = normalize_post_text(text) + "\n" + "\n".join(urls)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PublishingService:
    """Validate and persist GeneratedPost. Never calls LLM."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._agents = AgentRepository(session)
        self._posts = PostRepository(session)

    async def publish(
        self,
        *,
        agent_id: str,
        generated_post: GeneratedPost,
        grounded_urls: list[str] | set[str],
        topic_title: str | None = None,
        topic_id: str | None = None,
        consistency_approved: bool = False,
        final_validation_passed: bool = False,
        cycle_id: str | None = None,
        now: datetime | None = None,
    ) -> PublicationResult:
        started = time.perf_counter()
        stage = "publishing"
        logger.info(
            "publication_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            topic_id=topic_id,
            stage=stage,
        )

        try:
            if not consistency_approved:
                return self._reject(
                    agent_id,
                    cycle_id,
                    topic_id,
                    reason="consistency_not_approved",
                    started=started,
                )
            if not final_validation_passed:
                return self._reject(
                    agent_id,
                    cycle_id,
                    topic_id,
                    reason="final_validation_failed",
                    started=started,
                )

            text = (generated_post.text or "").strip()
            rationale = (generated_post.rationale or "").strip()
            if not text:
                return self._reject(
                    agent_id, cycle_id, topic_id, reason="empty_text", started=started
                )
            if not rationale:
                return self._reject(
                    agent_id,
                    cycle_id,
                    topic_id,
                    reason="empty_rationale",
                    started=started,
                )

            agent = await self._agents.get_by_id(agent_id)
            if agent is None:
                return self._reject(
                    agent_id, cycle_id, topic_id, reason="agent_not_found", started=started
                )

            allowed = {
                (normalize_url(url) or url.strip())
                for url in grounded_urls
                if url and str(url).strip()
            }
            sources: list[str] = []
            for raw in generated_post.source_urls:
                if not is_valid_http_url(raw):
                    return self._reject(
                        agent_id,
                        cycle_id,
                        topic_id,
                        reason="invalid_source_url",
                        started=started,
                    )
                key = normalize_url(raw) or raw.strip()
                if key not in allowed:
                    return self._reject(
                        agent_id,
                        cycle_id,
                        topic_id,
                        reason="ungrounded_source_url",
                        started=started,
                    )
                # Prefer original grounded form when available.
                sources.append(raw.strip())
            sources = list(dict.fromkeys(sources))
            if not sources:
                return self._reject(
                    agent_id,
                    cycle_id,
                    topic_id,
                    reason="no_grounded_sources",
                    started=started,
                )

            content_hash = compute_content_hash(text, sources)
            existing = await self._posts.get_by_agent_content_hash(agent_id, content_hash)
            if existing is not None:
                logger.info(
                    "publication_skipped",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    post_id=existing.id,
                    content_hash=content_hash,
                    stage=stage,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    reason="duplicate",
                )
                logger.info(
                    "publication_duplicate",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    post_id=existing.id,
                    content_hash=content_hash,
                    stage=stage,
                    reason="duplicate",
                )
                return PublicationResult(
                    status="duplicate",
                    content_hash=content_hash,
                    reason="duplicate",
                    post_id=existing.id,
                    post=PublishedPost(
                        id=existing.id,
                        agent_id=existing.agent_id,
                        created_at=existing.created_at,
                        text=existing.text,
                        rationale=existing.rationale,
                        sources=json.loads(existing.sources_json or "[]"),
                        topic=existing.topic,
                        content_hash=existing.content_hash,
                    ),
                )

            stamp = now or datetime.now(timezone.utc)
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            else:
                stamp = stamp.astimezone(timezone.utc)

            post_id = str(uuid.uuid4())
            try:
                async with self._session.begin_nested():
                    row = await self._posts.create_post(
                        post_id=post_id,
                        agent_id=agent_id,
                        created_at=stamp,
                        text=text,
                        rationale=rationale,
                        sources_json=json.dumps(sources, ensure_ascii=True),
                        content_hash=content_hash,
                        topic=topic_title,
                    )
                    await self._session.flush()
            except IntegrityError:
                # Concurrent duplicate insert — treat as idempotent duplicate.
                existing = await self._posts.get_by_agent_content_hash(
                    agent_id, content_hash
                )
                logger.info(
                    "publication_skipped",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    content_hash=content_hash,
                    stage=stage,
                    reason="duplicate",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                logger.info(
                    "publication_duplicate",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=topic_id,
                    content_hash=content_hash,
                    stage=stage,
                    reason="duplicate_integrity",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                return PublicationResult(
                    status="duplicate",
                    content_hash=content_hash,
                    reason="duplicate",
                    post_id=existing.id if existing else None,
                )

            published = PublishedPost(
                id=row.id,
                agent_id=row.agent_id,
                created_at=row.created_at,
                text=row.text,
                rationale=row.rationale,
                sources=sources,
                topic=row.topic,
                content_hash=row.content_hash,
            )
            logger.info(
                "publication_success",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                post_id=published.id,
                content_hash=content_hash,
                stage=stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return PublicationResult(
                status="published",
                post=published,
                content_hash=content_hash,
                post_id=published.id,
            )
        except Exception as exc:
            logger.exception(
                "publication_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                topic_id=topic_id,
                stage=stage,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return PublicationResult(status="failed", reason=str(exc))

    def _reject(
        self,
        agent_id: str | None,
        cycle_id: str | None,
        topic_id: str | None,
        *,
        reason: str,
        started: float,
    ) -> PublicationResult:
        logger.info(
            "publication_skipped",
            agent_id=agent_id,
            cycle_id=cycle_id,
            topic_id=topic_id,
            stage="publishing",
            reason=reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        logger.info(
            "publication_rejected",
            agent_id=agent_id,
            cycle_id=cycle_id,
            topic_id=topic_id,
            stage="publishing",
            reason=reason,
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return PublicationResult(status="rejected", reason=reason)
