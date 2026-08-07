"""Deterministic story continuity tracker (Module 12)."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime
from typing import Any

from app.logging import get_logger
from app.services.llm.base import CandidateTopic
from app.services.research.base import normalize_url
from app.services.scoring import jaccard_similarity, tokenize
from app.services.story_tracking.models import StoryCluster, StoryMatchResult

logger = get_logger(__name__)

_UPDATE_MARKERS = (
    "bypass",
    "mitigation",
    "patch",
    "fix",
    "update",
    "workaround",
    "defense",
    "exploit",
    "disclosure",
    "advisory",
)


class StoryTracker:
    """Match candidates against prior topic/story history without embeddings."""

    def __init__(self, *, match_threshold: float = 0.70, lookback: int = 20) -> None:
        self._threshold = max(0.0, min(1.0, match_threshold))
        self._lookback = max(1, lookback)

    def clusters_from_topics(self, topics: list[object]) -> list[StoryCluster]:
        """Rebuild story clusters from prior topics.scores_json meta."""
        clusters: dict[str, StoryCluster] = {}
        for topic in topics[: self._lookback]:
            title = str(getattr(topic, "title", "") or "")
            scores_raw = getattr(topic, "scores_json", None)
            meta: dict[str, Any] = {}
            if isinstance(scores_raw, str) and scores_raw.strip():
                try:
                    payload = json.loads(scores_raw)
                    meta = (payload.get("meta") or {}).get("story") or {}
                except Exception:
                    meta = {}
            story_meta = meta if isinstance(meta, dict) else {}
            nested = story_meta.get("story") if isinstance(story_meta.get("story"), dict) else {}
            story_id = str(nested.get("id") or story_meta.get("id") or "")
            if not story_id:
                # Fall back to title-hash cluster id for continuity across cycles.
                story_id = self._stable_id(title or str(getattr(topic, "id", uuid.uuid4())))
            canonical = str(
                nested.get("canonical_topic")
                or story_meta.get("canonical_topic")
                or title
            )
            urls = list(story_meta.get("source_urls") or [])
            created = getattr(topic, "created_at", None)
            existing = clusters.get(story_id)
            if existing is None:
                clusters[story_id] = StoryCluster(
                    id=story_id,
                    title=canonical,
                    canonical_topic=canonical,
                    source_urls=urls,
                    first_seen_at=created if isinstance(created, datetime) else None,
                    last_seen_at=created if isinstance(created, datetime) else None,
                    update_count=int(nested.get("update_count") or 1),
                    latest_summary=str(story_meta.get("previous_context") or title)[:400],
                    status="active",
                )
            else:
                existing.update_count += 1
                if isinstance(created, datetime):
                    existing.last_seen_at = created
                for url in urls:
                    if url not in existing.source_urls:
                        existing.source_urls.append(url)
                existing.latest_summary = str(
                    story_meta.get("new_evidence") or story_meta.get("previous_context") or title
                )[:400]
        return list(clusters.values())

    def match_candidate(
        self,
        candidate: CandidateTopic,
        *,
        clusters: list[StoryCluster],
        previous_titles: list[str],
        research_summaries: dict[str, str] | None = None,
        agent_id: str | None = None,
        cycle_id: str | None = None,
        now: datetime | None = None,
    ) -> StoryMatchResult:
        started = time.perf_counter()
        logger.info(
            "story_matching_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="story_tracking",
            cluster_count=len(clusters),
        )
        try:
            best_score = 0.0
            best: StoryCluster | None = None
            for cluster in clusters:
                score = self._similarity(candidate, cluster, previous_titles)
                if score > best_score:
                    best_score = score
                    best = cluster
            # Also compare against bare previous titles when no cluster metadata.
            for title in previous_titles[: self._lookback]:
                score = jaccard_similarity(candidate.title, title)
                concepts = self._concept_overlap(candidate.title, title)
                score = max(score, 0.6 * score + 0.4 * concepts)
                if score > best_score:
                    best_score = score
                    best = StoryCluster(
                        id=self._stable_id(title),
                        title=title,
                        canonical_topic=title,
                        latest_summary=title,
                        update_count=1,
                        status="active",
                    )

            grounded = [
                u for u in candidate.source_urls if normalize_url(u) or u.startswith("http")
            ]
            summaries = research_summaries or {}
            new_evidence = " ".join(
                summaries.get(normalize_url(u) or u, "") for u in grounded
            ).strip()
            if not new_evidence:
                new_evidence = f"{candidate.why_now}. {candidate.angle}".strip()

            if best is None or best_score < (self._threshold - 0.15):
                result = StoryMatchResult(
                    classification="new_story",
                    match_score=best_score,
                    previous_context="",
                    new_evidence=new_evidence[:400],
                    what_changed="",
                    story=StoryCluster(
                        id=str(uuid.uuid4()),
                        title=candidate.title,
                        canonical_topic=candidate.title,
                        source_urls=grounded,
                        first_seen_at=now,
                        last_seen_at=now,
                        update_count=1,
                        latest_summary=new_evidence[:400],
                        status="active",
                    ),
                    source_urls=grounded,
                )
                logger.info(
                    "story_new_story",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="story_tracking",
                    match_score=best_score,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                return result

            if best_score >= self._threshold:
                what_changed = self._detect_change(best.latest_summary, new_evidence, candidate)
                updated = best.model_copy(
                    update={
                        "update_count": best.update_count + 1,
                        "last_seen_at": now or best.last_seen_at,
                        "latest_summary": new_evidence[:400] or best.latest_summary,
                        "source_urls": list(dict.fromkeys(best.source_urls + grounded)),
                    }
                )
                result = StoryMatchResult(
                    classification="continuing_story",
                    match_score=best_score,
                    previous_context=best.latest_summary or best.canonical_topic,
                    new_evidence=new_evidence[:400],
                    what_changed=what_changed,
                    story=updated,
                    source_urls=grounded,
                )
                logger.info(
                    "story_match_found",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="story_tracking",
                    match_score=best_score,
                    classification="continuing_story",
                )
                logger.info(
                    "story_update_detected",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="story_tracking",
                    what_changed=what_changed[:160],
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                return result

            result = StoryMatchResult(
                classification="uncertain",
                match_score=best_score,
                previous_context=(best.latest_summary if best else "")[:400],
                new_evidence=new_evidence[:400],
                what_changed="",
                story=best,
                source_urls=grounded,
            )
            logger.info(
                "story_match_found",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="story_tracking",
                match_score=best_score,
                classification="uncertain",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return result
        except Exception:
            logger.exception(
                "story_matching_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="story_tracking",
            )
            return StoryMatchResult(
                classification="uncertain",
                match_score=0.0,
                new_evidence=candidate.why_now,
                source_urls=list(candidate.source_urls),
            )

    def _similarity(
        self,
        candidate: CandidateTopic,
        cluster: StoryCluster,
        previous_titles: list[str],
    ) -> float:
        title_score = jaccard_similarity(candidate.title, cluster.canonical_topic)
        concept = self._concept_overlap(candidate.title, cluster.canonical_topic)
        summary_score = jaccard_similarity(
            f"{candidate.angle} {candidate.why_now}",
            cluster.latest_summary or cluster.title,
        )
        domain_bonus = 0.0
        cand_hosts = {self._host(u) for u in candidate.source_urls}
        story_hosts = {self._host(u) for u in cluster.source_urls}
        if cand_hosts & story_hosts:
            domain_bonus = 0.08
        score = 0.55 * title_score + 0.25 * concept + 0.20 * summary_score + domain_bonus
        # Soft boost if any previous title is very similar.
        for title in previous_titles[:10]:
            score = max(score, jaccard_similarity(candidate.title, title))
        return max(0.0, min(1.0, score))

    @staticmethod
    def _concept_overlap(a: str, b: str) -> float:
        ta = tokenize(a)
        tb = tokenize(b)
        if not ta or not tb:
            return 0.0
        # Prefer meaningful tokens length >= 4
        ta = {t for t in ta if len(t) >= 4}
        tb = {t for t in tb if len(t) >= 4}
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    @staticmethod
    def _detect_change(previous: str, new_evidence: str, candidate: CandidateTopic) -> str:
        prev_tokens = tokenize(previous)
        new_tokens = tokenize(f"{new_evidence} {candidate.why_now} {candidate.angle}")
        novel = sorted(t for t in (new_tokens - prev_tokens) if len(t) >= 4)
        markers = [m for m in _UPDATE_MARKERS if m in new_tokens and m not in prev_tokens]
        if markers:
            return (
                f"New development emphasizes {', '.join(markers[:3])}; "
                f"evidence advances the prior story."
            )
        if novel:
            return f"Updated evidence introduces new details ({', '.join(novel[:5])})."
        return "Additional reporting on the same story with limited novel detail."

    @staticmethod
    def _stable_id(text: str) -> str:
        digest = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()[:16]
        return f"story-{digest}"

    @staticmethod
    def _host(url: str) -> str | None:
        from app.services.source_diversity.analyzer import extract_domain

        return extract_domain(url)
