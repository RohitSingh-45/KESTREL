"""ObservabilityService: non-authoritative explainability sidecar (0 LLM calls)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.logging import get_logger
from app.services.observability.collector import EventCollector
from app.services.observability.events import priority_for
from app.services.observability.models import (
    CycleExplanation,
    EventAction,
    EventStatus,
    EventType,
    ObservabilityEvent,
    PublicationExplanation,
)
from app.services.observability.sanitizer import sanitize_mapping
from app.services.observability.trace import build_stage_trace
from app.services.reliability.secrets import sanitize_error, sanitize_text

logger = get_logger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _domain(url: str) -> str | None:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


class ObservabilityService:
    """Record and explain pipeline decisions. Never publishes or mutates modules."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_events: int = 100,
        max_reason_length: int = 300,
        max_evidence_items: int = 10,
    ) -> None:
        self.enabled = enabled
        self.max_events = max(1, int(max_events))
        self.max_reason_length = max(40, int(max_reason_length))
        self.max_evidence_items = max(1, int(max_evidence_items))
        self.llm_calls = 0
        self._agent_id = "unknown"
        self._cycle_id: str | None = None
        self._collector = EventCollector(max_events=self.max_events)
        self._publication_explanation: PublicationExplanation | None = None

    def begin(self, *, agent_id: str, cycle_id: str | None = None) -> None:
        self._agent_id = agent_id
        self._cycle_id = cycle_id
        self._collector = EventCollector(max_events=self.max_events)
        self._publication_explanation = None
        self.llm_calls = 0

    def record(
        self,
        event_type: EventType,
        *,
        stage: str,
        action: EventAction = "observe",
        status: EventStatus = "info",
        reason: str = "",
        evidence: dict[str, Any] | None = None,
        source_urls: list[str] | None = None,
        related_topic_id: str | None = None,
        related_post_id: str | None = None,
        decision_gate: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObservabilityEvent | None:
        if not self.enabled:
            return None
        evidence_clean = sanitize_mapping(evidence or {})
        evidence_clean = dict(list(evidence_clean.items())[: self.max_evidence_items])
        meta_clean = sanitize_mapping(metadata or {})
        reason_clean = sanitize_text(str(reason))[: self.max_reason_length]
        urls_clean = [
            sanitize_text(str(u))[:240] for u in list(source_urls or [])[:10]
        ]
        event = ObservabilityEvent(
            agent_id=self._agent_id,
            cycle_id=self._cycle_id,
            timestamp=_utc_now_iso(),
            stage=stage[:64],
            event_type=event_type,
            action=action,
            status=status,
            reason=reason_clean,
            evidence=evidence_clean,
            source_urls=urls_clean,
            related_topic_id=related_topic_id,
            related_post_id=related_post_id,
            decision_gate=decision_gate,
            metadata=meta_clean,
            priority=priority_for(event_type),
        )
        return self._collector.add(event)

    def safe_record(self, *args: Any, **kwargs: Any) -> ObservabilityEvent | None:
        try:
            return self.record(*args, **kwargs)
        except Exception as exc:
            logger.exception(
                "observability_record_failed",
                agent_id=self._agent_id,
                cycle_id=self._cycle_id,
                stage="observability",
                reason=sanitize_error(exc),
            )
            return None

    def record_decision_gate(
        self,
        gate: str,
        *,
        action: str,
        reason: str,
        evidence: dict[str, Any] | None = None,
        blocking: bool = False,
    ) -> None:
        blocked = action in {"skip", "stop"} or blocking
        self.safe_record(
            "decision_gate_blocked" if blocked else "decision_gate_passed",
            stage="decision",
            action="stop" if action == "stop" else ("skip" if blocked else "proceed"),
            status="blocked" if blocked else "ok",
            reason=reason,
            evidence=evidence or {},
            decision_gate=gate,
        )

    def explain_publication(
        self,
        *,
        publication: dict[str, Any] | None,
        final_generation: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        consistency: list[Any] | None = None,
        cycle_status: str | None = None,
        cycle_reason: str | None = None,
    ) -> PublicationExplanation:
        """Derive why published / why not from actual pipeline meta only."""
        pub = publication or {}
        final = final_generation or {}
        dec = decision or {}
        pub_status = pub.get("status")

        if pub_status == "published":
            explanation = PublicationExplanation(
                published=True,
                final_reason="published",
                stage="publishing",
                explanation=(
                    "Final post passed grounding, consistency, validation, "
                    "decision, and duplicate checks."
                ),
                evidence={
                    "published_post_id": pub.get("published_post_id"),
                    "topic_id": pub.get("topic_id"),
                    "decision": dec.get("final_action"),
                },
            )
            self._publication_explanation = explanation
            return explanation

        # Not published — pick the most specific actual reason.
        if cycle_reason == "decision_unavailable" or dec.get("status") == "unavailable":
            explanation = PublicationExplanation(
                published=False,
                final_reason="decision_unavailable",
                stage="decision",
                explanation="Decision orchestration was unavailable; publishing was not attempted.",
            )
        elif pub_status == "duplicate":
            explanation = PublicationExplanation(
                published=False,
                final_reason="duplicate",
                stage="publishing",
                explanation="Publishing skipped because identical content was already published.",
                evidence={"content_hash": pub.get("content_hash")},
            )
        elif pub_status == "rejected":
            explanation = PublicationExplanation(
                published=False,
                final_reason=str(pub.get("reason") or "validation_failed")[:120],
                stage="publishing",
                explanation=str(
                    pub.get("reason") or "Final post failed publishing validation."
                )[:300],
            )
        elif pub_status == "failed":
            explanation = PublicationExplanation(
                published=False,
                final_reason="publication_failed",
                stage="publishing",
                explanation=str(pub.get("reason") or "PublishingService failed.")[:300],
            )
        elif final.get("status") in {"skipped", "failed", "rejected"}:
            reason = str(final.get("reason") or final.get("status") or "final_skipped")
            stage = "generation"
            final_reason = reason
            text = f"Final generation did not produce a publishable post ({reason})."
            if "decision_gate" in reason or "decision" in reason:
                stage = "decision"
                final_reason = "decision_gate_blocked"
                text = "A decision gate blocked final generation before publishing."
            if "consistency" in reason or "no_approved_finalist" in reason:
                stage = "consistency"
                final_reason = "consistency_gate_blocked"
                text = "No finalist satisfied the minimum consistency requirement."
            if "debate" in reason:
                stage = "debate"
                final_reason = "debate_rejected"
                text = "Debate did not approve a finalist for final generation."
            explanation = PublicationExplanation(
                published=False,
                final_reason=final_reason[:120],
                stage=stage,
                explanation=text[:300],
                evidence={"final_generation_status": final.get("status")},
            )
        elif consistency and all(
            (item.get("decision") if isinstance(item, dict) else None) == "reject"
            for item in consistency
        ):
            explanation = PublicationExplanation(
                published=False,
                final_reason="consistency_gate_blocked",
                stage="consistency",
                explanation="No finalist satisfied the minimum consistency requirement.",
            )
        elif cycle_status == "skipped":
            explanation = PublicationExplanation(
                published=False,
                final_reason=str(cycle_reason or "cycle_skipped")[:120],
                stage="cycle",
                explanation=f"Cycle skipped before publish ({cycle_reason or 'skipped'}).",
            )
        elif pub_status == "skipped" or pub.get("reason"):
            explanation = PublicationExplanation(
                published=False,
                final_reason=str(pub.get("reason") or "publishing_skipped")[:120],
                stage="publishing",
                explanation=str(
                    pub.get("reason") or "Publishing was skipped by the pipeline."
                )[:300],
            )
        else:
            explanation = PublicationExplanation(
                published=False,
                final_reason="not_published",
                stage="publishing",
                explanation="No post was published in this cycle.",
            )
        self._publication_explanation = explanation
        return explanation

    def explain_why_published(
        self,
        *,
        selected: dict[str, Any] | None = None,
        debate: dict[str, Any] | None = None,
        consistency: dict[str, Any] | None = None,
        intelligence: dict[str, Any] | None = None,
        adaptive: dict[str, Any] | None = None,
        learning: dict[str, Any] | None = None,
        final_generation: dict[str, Any] | None = None,
        publication: dict[str, Any] | None = None,
        evaluation: dict[str, Any] | None = None,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compact post-publish explanation from structured module outputs."""
        urls = list(sources or [])[:10]
        domains = [d for u in urls if (d := _domain(u))]
        intel = intelligence or {}
        return sanitize_mapping(
            {
                "selected_topic": (selected or {}).get("title"),
                "selection_rank": (selected or {}).get("rank"),
                "score": (selected or {}).get("final_score"),
                "debate_decision": (debate or {}).get("decision"),
                "consistency_result": (consistency or {}).get("decision"),
                "consistency_score": (consistency or {}).get("overall_score"),
                "source_grounding": {
                    "source_count": len(urls),
                    "source_domains": domains[:10],
                    "grounded_url_count": len(urls),
                    "source_diversity_score": intel.get("source_diversity_score"),
                    "source_repetition": intel.get("source_repetition"),
                },
                "novelty_story": {
                    "story_type": intel.get("story_type"),
                    "story_continuity_score": intel.get("story_continuity_score"),
                },
                "adaptive": {
                    "used": bool(adaptive),
                    "status": (adaptive or {}).get("status"),
                    "signals": (adaptive or {}).get("signal_count")
                    or len((adaptive or {}).get("overused_topics") or []),
                },
                "learning": {
                    "used": bool(learning),
                    "status": (learning or {}).get("status"),
                    "strategies": len((learning or {}).get("active_strategies") or []),
                },
                "final_validation": (final_generation or {}).get("status"),
                "publishing_decision": (publication or {}).get("status"),
                "evaluation_result": (evaluation or {}).get("status"),
            }
        )

    def source_explanation(
        self,
        *,
        source_urls: list[str] | None = None,
        intelligence: dict[str, Any] | None = None,
        unknown_urls_rejected: bool | None = None,
    ) -> dict[str, Any]:
        urls = list(source_urls or [])[:10]
        domains = [d for u in urls if (d := _domain(u))]
        intel = intelligence or {}
        return sanitize_mapping(
            {
                "source_count": len(urls),
                "source_domains": domains[: self.max_evidence_items],
                "source_diversity_score": intel.get("source_diversity_score"),
                "grounded_url_count": len(urls),
                "unknown_urls_rejected": unknown_urls_rejected,
                "source_repetition_detected": bool(intel.get("source_repetition")),
            }
        )

    def memory_explanation(
        self,
        *,
        memory_meta: dict[str, Any] | None = None,
        keys: list[str] | None = None,
        items_considered: int = 0,
    ) -> dict[str, Any]:
        mem = memory_meta or {}
        relevant = list(keys or mem.get("keys") or [])[: self.max_evidence_items]
        count = items_considered or int(mem.get("retrieved_count") or mem.get("count") or 0)
        return sanitize_mapping(
            {
                "memory_used": count > 0 or bool(relevant),
                "memory_items_considered": count,
                "relevant_keys": [str(k)[:80] for k in relevant],
            }
        )

    def learning_explanation(
        self,
        *,
        adaptive_meta: dict[str, Any] | None = None,
        learning_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        adaptive = adaptive_meta or {}
        learning = learning_meta or {}
        return sanitize_mapping(
            {
                "adaptive": {
                    "used": adaptive.get("status") not in {None, "skipped", "disabled"},
                    "available": bool(adaptive),
                    "signals": adaptive.get("signal_count")
                    or len(adaptive.get("overused_topics") or []),
                },
                "learning": {
                    "used": learning.get("status") not in {None, "skipped", "disabled"},
                    "available": bool(learning),
                    "strategies": len(learning.get("active_strategies") or []),
                },
            }
        )

    def finalize(
        self,
        *,
        cycle_status: str,
        cycle_reason: str | None = None,
        publication: dict[str, Any] | None = None,
        final_generation: dict[str, Any] | None = None,
        decision: dict[str, Any] | None = None,
        consistency: list[Any] | None = None,
        evaluation: dict[str, Any] | None = None,
        why_published: dict[str, Any] | None = None,
        memory: dict[str, Any] | None = None,
        learning: dict[str, Any] | None = None,
        sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build bounded observability payload for pipeline_runs.meta_json."""
        if not self.enabled:
            return {
                "version": 1,
                "enabled": False,
                "event_count": 0,
                "events": [],
                "published": False,
                "final_reason": "observability_disabled",
            }

        why = self.explain_publication(
            publication=publication,
            final_generation=final_generation,
            decision=decision,
            consistency=consistency,
            cycle_status=cycle_status,
            cycle_reason=cycle_reason,
        )

        if cycle_status == "failed":
            self.safe_record(
                "cycle_failed",
                stage="cycle",
                action="stop",
                status="failed",
                reason=cycle_reason or "cycle_failed",
            )
        elif cycle_status == "skipped" or not why.published:
            if cycle_status == "skipped" or publication:
                self.safe_record(
                    "cycle_skipped" if cycle_status == "skipped" else "cycle_completed",
                    stage="cycle",
                    action="skip" if cycle_status == "skipped" else "complete",
                    status="skipped" if cycle_status == "skipped" else "ok",
                    reason=why.final_reason,
                )
        else:
            self.safe_record(
                "cycle_completed",
                stage="cycle",
                action="complete",
                status="ok",
                reason="published" if why.published else why.final_reason,
            )

        events_meta = [e.to_meta() for e in self._collector.events]
        payload = {
            "version": 1,
            "enabled": True,
            "event_count": len(events_meta),
            "dropped_events": self._collector.dropped,
            "events": events_meta,
            "trace": build_stage_trace(self._collector.events),
            "published": why.published,
            "final_reason": why.final_reason,
            "why": why.to_meta(),
            "why_published": why_published or {},
            "memory": memory or {},
            "learning": learning or {},
            "sources": sources or {},
            "evaluation_status": (evaluation or {}).get("status"),
            "decision_final_action": (decision or {}).get("final_action"),
        }
        return sanitize_mapping(payload, max_str=self.max_reason_length)

    def meta_payload(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.finalize(**kwargs)
        except Exception as exc:
            logger.exception(
                "observability_finalize_failed",
                agent_id=self._agent_id,
                cycle_id=self._cycle_id,
                stage="observability",
            )
            return {
                "version": 1,
                "enabled": self.enabled,
                "status": "unavailable",
                "reason": sanitize_error(exc),
                "event_count": 0,
                "events": [],
                "published": False,
                "final_reason": "observability_unavailable",
            }

    async def get_cycle_explanation(
        self,
        session: AsyncSession,
        *,
        agent_id: str,
        cycle_id: str,
    ) -> CycleExplanation | None:
        """Internal debug helper — reads persisted pipeline_runs meta only."""
        from app.db.repositories.pipeline_runs import PipelineRunRepository

        runs = PipelineRunRepository(session)
        run = await runs.get_by_id(cycle_id)
        if run is None or run.agent_id != agent_id:
            return None
        meta = json.loads(run.meta_json or "{}")
        obs = meta.get("observability") or {}
        why = obs.get("why") or {}
        return CycleExplanation(
            cycle_id=cycle_id,
            agent_id=agent_id,
            result=(
                "published"
                if obs.get("published")
                else str(obs.get("final_reason") or run.status)
            ),
            stages=list(obs.get("trace") or [])[:20],
            decision_summary=dict(meta.get("decision") or {}),
            publication=dict(meta.get("publication") or {}),
            evaluation=dict(meta.get("evaluation") or {}),
            why=dict(why),
        )
