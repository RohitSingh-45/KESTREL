"""AdaptiveLearningService: deterministic sidecar learning from history."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.orchestration.locks import utc_now
from app.services.adaptive_learning.coverage import (
    TopicCoverageAnalyzer,
    classify_angle,
    normalize_concept,
)
from app.services.adaptive_learning.decay import decay_influence
from app.services.adaptive_learning.models import (
    AdaptiveContext,
    AdaptiveSignal,
    TopicGuidance,
)
from app.services.adaptive_learning.store import AdaptiveSignalStore
from app.services.llm.base import CandidateTopic
from app.services.scoring import jaccard_similarity

logger = get_logger(__name__)


class AdaptiveLearningService:
    """Learn patterns from posts/topics/memory. Never publishes or mutates scores."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        lookback_posts: int = 20,
        max_signals: int = 20,
        max_context_items: int = 20,
        stale_days: int = 30,
        decay_days: int = 30,
    ) -> None:
        self._enabled = enabled
        self._lookback = max(1, lookback_posts)
        self._max_signals = max(1, max_signals)
        self._max_context = max(1, max_context_items)
        self._stale_days = max(1, stale_days)
        self._decay_days = max(1, decay_days)
        self._coverage = TopicCoverageAnalyzer(lookback_posts=self._lookback)
        self.llm_calls = 0  # always 0 — deterministic only

    async def run(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        posts: list[object],
        topics: list[object],
        intelligence_by_topic: dict[str, dict[str, Any]] | None = None,
        contradiction_meta: dict[str, Any] | None = None,
        selected_candidates: list[CandidateTopic] | None = None,
        cycle_id: str | None = None,
        now: datetime | None = None,
    ) -> AdaptiveContext:
        started = time.perf_counter()
        stamp = now or utc_now()
        logger.info(
            "adaptive_learning_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage="adaptive_learning",
            item_count=len(posts),
        )
        if not self._enabled:
            return AdaptiveContext(status="skipped", reason="disabled")

        try:
            analysis = self._coverage.analyze_posts(posts, topics=topics, now=stamp)
            signals = self._build_signals(
                analysis,
                topics=topics,
                intelligence_by_topic=intelligence_by_topic or {},
                contradiction_meta=contradiction_meta or {},
                now=stamp,
            )
            store = AdaptiveSignalStore(session)
            persisted: list[AdaptiveSignal] = []
            for signal in signals[: self._max_signals]:
                saved = await store.upsert_signal(agent_id, signal, now=stamp)
                saved.influence = decay_influence(
                    saved.confidence,
                    last_observed_at=saved.last_observed_at,
                    now=stamp,
                    decay_days=self._decay_days,
                )
                persisted.append(saved)

            # Load prior signals for decay ranking / context.
            prior = await store.list_signals(agent_id)
            for signal in prior:
                signal.influence = decay_influence(
                    signal.confidence,
                    last_observed_at=signal.last_observed_at,
                    now=stamp,
                    decay_days=self._decay_days,
                )

            context = self._build_context(
                analysis=analysis,
                signals=sorted(prior, key=lambda s: s.influence, reverse=True),
                intelligence_by_topic=intelligence_by_topic or {},
                selected_candidates=selected_candidates or [],
                now=stamp,
            )
            logger.info(
                "adaptive_learning_completed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="adaptive_learning",
                item_count=len(persisted),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return context
        except Exception as exc:
            logger.exception(
                "adaptive_learning_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="adaptive_learning",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            return AdaptiveContext(status="failed", reason=str(exc))

    def _build_signals(
        self,
        analysis: dict[str, Any],
        *,
        topics: list[object],
        intelligence_by_topic: dict[str, dict[str, Any]],
        contradiction_meta: dict[str, Any],
        now: datetime,
    ) -> list[AdaptiveSignal]:
        signals: list[AdaptiveSignal] = []

        for concept, payload in (analysis.get("coverage") or {}).items():
            freq = float(payload.get("recent_frequency") or 0.0)
            signals.append(
                AdaptiveSignal(
                    signal_type="topic_pattern",
                    key=concept,
                    value={
                        "count": payload.get("count"),
                        "recent_frequency": freq,
                        "last_seen_at": payload.get("last_seen_at"),
                    },
                    confidence=min(1.0, 0.4 + freq),
                    evidence_count=int(payload.get("count") or 0),
                    first_observed_at=now,
                    last_observed_at=now,
                )
            )
            if freq >= 0.25:
                logger.info(
                    "adaptive_pattern_detected",
                    stage="adaptive_learning",
                    signal_type="topic_pattern",
                    signal_key=concept,
                    item_count=payload.get("count"),
                )

        for angle, freq in (analysis.get("angles") or {}).items():
            signals.append(
                AdaptiveSignal(
                    signal_type="angle_pattern",
                    key=angle,
                    value={"recent_frequency": freq},
                    confidence=min(1.0, 0.35 + float(freq)),
                    evidence_count=1,
                    first_observed_at=now,
                    last_observed_at=now,
                )
            )

        for domain, payload in (analysis.get("domains") or {}).items():
            dependency = str(payload.get("dependency") or "low")
            signals.append(
                AdaptiveSignal(
                    signal_type="source_pattern",
                    key=domain,
                    value={
                        "recent_count": payload.get("recent_count"),
                        "share": payload.get("share"),
                        "dependency": dependency,
                    },
                    confidence=0.8 if dependency == "high" else 0.55,
                    evidence_count=int(payload.get("recent_count") or 0),
                    first_observed_at=now,
                    last_observed_at=now,
                )
            )
            if dependency == "high":
                logger.info(
                    "adaptive_source_dependency_detected",
                    stage="adaptive_learning",
                    signal_type="source_pattern",
                    signal_key=domain,
                    item_count=payload.get("recent_count"),
                )

        # Story aging / revival from topic intelligence meta + prior topics.
        for topic in topics:
            scores_raw = getattr(topic, "scores_json", None)
            meta: dict[str, Any] = {}
            if isinstance(scores_raw, str) and scores_raw.strip():
                try:
                    meta = (json.loads(scores_raw).get("meta") or {})
                except Exception:
                    meta = {}
            story = meta.get("story") if isinstance(meta.get("story"), dict) else {}
            story_type = str(meta.get("story_type") or story.get("classification") or "")
            title = str(getattr(topic, "title", "") or "")
            created = getattr(topic, "created_at", None)
            status = self._story_status(
                created_at=created if isinstance(created, datetime) else None,
                story_type=story_type,
                now=now,
            )
            if status in {"stale", "aging", "revived", "active"} and title:
                key = normalize_concept(title)
                signals.append(
                    AdaptiveSignal(
                        signal_type="story_pattern",
                        key=key,
                        value={
                            "status": status,
                            "story_type": story_type,
                            "title": title[:160],
                        },
                        confidence=0.7,
                        evidence_count=1,
                        first_observed_at=created if isinstance(created, datetime) else now,
                        last_observed_at=now,
                    )
                )
                if status == "stale":
                    logger.info(
                        "adaptive_story_stale",
                        stage="adaptive_learning",
                        signal_key=key,
                    )
                if status == "revived":
                    logger.info(
                        "adaptive_story_revived",
                        stage="adaptive_learning",
                        signal_key=key,
                    )

        # Rejection patterns from consistency / debate metadata.
        rejection_counts: dict[str, int] = {}
        for topic in topics:
            for field_name in ("consistency_json", "debate_json"):
                raw = getattr(topic, field_name, None)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                reasons = payload.get("reasons") or []
                if isinstance(reasons, list):
                    for reason in reasons:
                        key = str(reason).strip().lower().replace(" ", "_")[:64]
                        if key:
                            rejection_counts[key] = rejection_counts.get(key, 0) + 1
                decision = str(payload.get("decision") or "")
                if decision in {"reject", "rejected"}:
                    rejection_counts["decision_reject"] = (
                        rejection_counts.get("decision_reject", 0) + 1
                    )
        for reason, count in rejection_counts.items():
            signals.append(
                AdaptiveSignal(
                    signal_type="rejection_pattern",
                    key=reason,
                    value={"count": count},
                    confidence=min(1.0, 0.3 + 0.1 * count),
                    evidence_count=count,
                    first_observed_at=now,
                    last_observed_at=now,
                )
            )

        # Contradiction-aware belief pattern (does not overwrite MemoryService).
        if contradiction_meta.get("detected"):
            mem_key = str(contradiction_meta.get("memory_key") or "belief")
            signals.append(
                AdaptiveSignal(
                    signal_type="belief_pattern",
                    key=normalize_concept(mem_key),
                    value={
                        "contradictions_seen": int(
                            contradiction_meta.get("signal_count") or 1
                        ),
                        "status": "contested",
                        "type": contradiction_meta.get("type"),
                    },
                    confidence=float(contradiction_meta.get("confidence") or 0.7),
                    evidence_count=int(contradiction_meta.get("signal_count") or 1),
                    source_urls=list(contradiction_meta.get("evidence_urls") or [])[:5],
                    first_observed_at=now,
                    last_observed_at=now,
                )
            )

        # Current-cycle intelligence repetition / freshness hints.
        for topic_id, intel in intelligence_by_topic.items():
            if float(intel.get("source_repetition") or 0) >= 0.6:
                signals.append(
                    AdaptiveSignal(
                        signal_type="repetition_pattern",
                        key="source_repetition",
                        value={
                            "source_repetition": intel.get("source_repetition"),
                            "topic_id": topic_id,
                        },
                        confidence=0.65,
                        evidence_count=1,
                        first_observed_at=now,
                        last_observed_at=now,
                    )
                )

        return signals

    def _story_status(
        self,
        *,
        created_at: datetime | None,
        story_type: str,
        now: datetime,
    ) -> str:
        now_aware = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        days = 0.0
        if created_at is not None:
            ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)
            days = max(0.0, (now_aware - ts.astimezone(timezone.utc)).total_seconds() / 86400.0)

        # New evidence on a previously aging/stale story → revived.
        if story_type == "continuing_story":
            if days >= (self._stale_days / 2):
                return "revived"
            return "active"
        if created_at is None:
            return "unknown"
        if days >= self._stale_days:
            return "stale"
        if days >= self._stale_days / 2:
            return "aging"
        return "active"

    def _build_context(
        self,
        *,
        analysis: dict[str, Any],
        signals: list[AdaptiveSignal],
        intelligence_by_topic: dict[str, dict[str, Any]],
        selected_candidates: list[CandidateTopic],
        now: datetime,
    ) -> AdaptiveContext:
        limit = self._max_context
        coverage = analysis.get("coverage") or {}
        recent_topics = [
            {"key": k, **v} for k, v in list(coverage.items())[:limit]
        ]
        source_deps = [
            {"domain": k, **v}
            for k, v in list((analysis.get("domains") or {}).items())[:limit]
            if str(v.get("dependency")) in {"high", "moderate"}
        ]
        rejection = [
            s.to_meta()
            for s in signals
            if s.signal_type == "rejection_pattern"
        ][:limit]
        story_signals = [s for s in signals if s.signal_type == "story_pattern"]
        active = [
            s.key for s in story_signals if (s.value or {}).get("status") == "active"
        ][:limit]
        stale = [
            s.key for s in story_signals if (s.value or {}).get("status") == "stale"
        ][:limit]
        revived = [
            s.key for s in story_signals if (s.value or {}).get("status") == "revived"
        ][:limit]

        guidance: dict[str, Any] = {}
        for candidate in selected_candidates[:5]:
            concept = normalize_concept(candidate.title)
            freq = float((coverage.get(concept) or {}).get("recent_frequency") or 0.0)
            coverage_label = (
                "high" if freq >= 0.25 else "moderate" if freq >= 0.1 else "low"
            )
            story_status = "new"
            for s in story_signals:
                if s.key == concept or jaccard_similarity(s.key, concept) > 0.5:
                    story_status = str((s.value or {}).get("status") or "active")
                    break
            angle = classify_angle(
                f"{candidate.angle} {candidate.title} {candidate.why_now}"
            )
            angle_freq = float((analysis.get("angles") or {}).get(angle) or 0.0)
            dep_need = "moderate"
            if source_deps and any(d.get("dependency") == "high" for d in source_deps):
                dep_need = "high"
            g = TopicGuidance(
                coverage=coverage_label,
                novelty_need="high" if coverage_label == "high" else "moderate",
                story_status=story_status,
                source_diversity_need=dep_need,
                notes=[
                    f"concept={concept}",
                    f"angle={angle}:{angle_freq}",
                ],
            )
            guidance[candidate.title[:80]] = g.to_meta()

        quality_feedback = self._quality_feedback_from_signals(signals, now=now)

        return AdaptiveContext(
            recent_topic_patterns=recent_topics[:limit],
            overused_topics=list(analysis.get("overused_topics") or [])[:limit],
            undercovered_topics=list(analysis.get("undercovered_topics") or [])[:limit],
            recent_angles=[
                {"angle": k, "recent_frequency": v}
                for k, v in list((analysis.get("angles") or {}).items())[:limit]
            ],
            overused_angles=list(analysis.get("overused_angles") or [])[:limit],
            source_dependencies=source_deps[:limit],
            active_stories=active,
            stale_stories=stale,
            revived_stories=revived,
            rejection_patterns=rejection,
            relevant_adaptive_signals=[s.to_meta() for s in signals[:limit]],
            candidate_guidance=guidance,
            quality_feedback=quality_feedback,
            status="success",
        )

    def _quality_feedback_from_signals(
        self,
        signals: list[AdaptiveSignal],
        *,
        now: datetime,
    ) -> dict[str, str]:
        """Bounded Module 14 quality feedback for future guidance only."""
        # Lazy import avoids circular import with evaluation.store → adaptive store.
        from app.services.evaluation.feedback import merge_feedback_history
        from app.services.evaluation.models import QualityFeedback

        recent: list[QualityFeedback] = []
        for signal in signals:
            if signal.signal_type != "quality_feedback":
                continue
            actions: dict[str, str] = {}
            if signal.key == "latest":
                actions = {
                    str(k): str(v)
                    for k, v in ((signal.value or {}).get("actions") or {}).items()
                }
            elif (signal.value or {}).get("action"):
                actions = {signal.key: str((signal.value or {}).get("action"))}
            if not actions:
                continue
            recent.append(
                QualityFeedback(
                    actions=actions,  # type: ignore[arg-type]
                    confidence=signal.confidence,
                    evidence_count=signal.evidence_count,
                    last_observed_at=signal.last_observed_at,
                )
            )
        if not recent:
            return {}
        return merge_feedback_history(
            recent,
            now=now,
            decay_days=self._decay_days,
            max_items=min(self._max_context, 10),
        )
