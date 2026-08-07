"""Autonomous generation cycle: research + candidates + scoring + debate (no posts)."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.logging import get_logger
from app.orchestration.locks import (
    AgentLockManager,
    GenerationLeaseManager,
    new_lease_owner,
    utc_now,
)
from app.services.candidates import CandidateGenerationService, persist_candidates
from app.services.consistency import ConsistencyGuard, consistency_payload
from app.services.consistency.models import ConsistencyDecisionEnum
from app.services.adaptive_learning import AdaptiveLearningService
from app.services.contradictions import ContradictionDetector, challenge_payload
from app.services.debate import SelfDebateService, debate_payload
from app.services.decision import DecisionPolicy, DecisionService
from app.services.evaluation import PostEvaluator
from app.services.final_post import FinalPostGenerator, outcome_meta
from app.services.learning import ControlledLearningService
from app.services.llm.base import LLMClient, UnavailableLLMClient
from app.services.memory import MemoryService
from app.services.observability import ObservabilityService
from app.services.publishing import PublishingService
from app.services.reliability.secrets import sanitize_error
from app.services.research.base import normalize_url
from app.services.research.hub import ResearchHub
from app.services.scoring import (
    HistoricalTopic,
    ScoringContext,
    TopicScoringService,
    scores_payload,
)
from app.services.source_diversity import SourceDiversityAnalyzer
from app.services.story_tracking import StoryTracker

logger = get_logger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
STAGE_RESEARCH_CANDIDATES = "research_and_candidate_generation"
STAGE_RESEARCH_SCORING = "research_candidate_scoring"
STAGE_RESEARCH_SCORING_DEBATE = "research_candidate_scoring_debate"
STAGE_RESEARCH_SCORING_DEBATE_MEMORY = "research_candidate_scoring_debate_memory"
STAGE_RESEARCH_SCORING_DEBATE_MEMORY_CONSISTENCY = (
    "research_candidate_scoring_debate_memory_consistency"
)
STAGE_FINAL_GENERATION = (
    "research_candidate_scoring_debate_memory_consistency_final_generation"
)
STAGE_PUBLISHING = (
    "research_candidate_scoring_debate_memory_consistency_intelligence_"
    "adaptive_learning_final_generation_publishing_evaluation"
)


class GenerationCycleService:
    """Runs research, candidates, scoring, and self-debate. Never creates posts."""

    def __init__(
        self,
        session_factory: SessionFactory,
        lock_manager: AgentLockManager,
        lease_seconds: int,
        *,
        research_hub: ResearchHub | None = None,
        candidate_service: CandidateGenerationService | None = None,
        llm_client: LLMClient | None = None,
        scoring_service: TopicScoringService | None = None,
        debate_service: SelfDebateService | None = None,
        memory_service: MemoryService | None = None,
        consistency_guard: ConsistencyGuard | None = None,
        final_post_generator: FinalPostGenerator | None = None,
        cycle_hook: Callable[[], Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._locks = lock_manager
        self._lease_seconds = lease_seconds
        self._research_hub = research_hub or ResearchHub(connectors=[], max_items=20)
        self._llm = llm_client or UnavailableLLMClient()
        self._candidates = candidate_service or CandidateGenerationService(self._llm)
        self._scoring = scoring_service or TopicScoringService()
        settings = get_settings()
        self._debate = debate_service or SelfDebateService(
            self._llm,
            min_confidence=settings.debate_min_confidence,
            min_score_for_proceed=settings.debate_min_score_for_proceed,
        )
        self._memory = memory_service or MemoryService(
            self._llm,
            max_items=settings.memory_max_items,
            max_source_urls=settings.memory_max_source_urls,
            min_confidence=settings.memory_min_confidence,
            extraction_enabled=settings.memory_extraction_enabled,
        )
        self._consistency = consistency_guard or ConsistencyGuard(
            enabled=settings.consistency_enabled,
            approve_threshold=settings.consistency_approve_threshold,
            revise_threshold=settings.consistency_revise_threshold,
            max_revisions=settings.consistency_max_revisions,
            history_limit=settings.consistency_history_limit,
        )
        self._final_post = final_post_generator or FinalPostGenerator(self._llm)
        # Optional hook used by Module 3-style tests to inject failures / delays.
        self._cycle_hook = cycle_hook

    async def execute(self, agent_id: str) -> str:
        """Execute one cycle. Returns: success | skipped | failed."""
        started = time.perf_counter()
        cycle_id = str(uuid.uuid4())
        stage = (
            "lifecycle_check" if self._cycle_hook is not None else STAGE_PUBLISHING
        )

        logger.info(
            "cycle_started",
            agent_id=agent_id,
            cycle_id=cycle_id,
            stage=stage,
            status="starting",
        )

        memory_acquired = await self._locks.try_acquire(agent_id)
        if not memory_acquired:
            logger.info(
                "lease_skipped",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage=stage,
                status="skipped",
                reason="in_memory_lock",
                duration_ms=_elapsed_ms(started),
            )
            await self._record_skip(agent_id, reason="in_memory_lock", stage=stage)
            return "skipped"

        lease_owner = new_lease_owner()
        run_id: str | None = None

        try:
            async with self._session_factory() as session:
                agents = AgentRepository(session)
                runs = PipelineRunRepository(session)
                leases = GenerationLeaseManager(session, self._lease_seconds)

                agent = await agents.get_by_id(agent_id)
                if agent is None:
                    await session.commit()
                    logger.info(
                        "cycle_failed",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                        status="failed",
                        reason="agent_not_found",
                        duration_ms=_elapsed_ms(started),
                    )
                    return "failed"

                if agent.status != "running":
                    await session.commit()
                    logger.info(
                        "cycle_failed",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                        status="skipped",
                        reason="agent_not_running",
                        duration_ms=_elapsed_ms(started),
                    )
                    return "skipped"

                lease_ok = await leases.try_acquire(agent_id, lease_owner)
                if not lease_ok:
                    now = utc_now()
                    skip_id = str(uuid.uuid4())
                    await runs.create_run(
                        run_id=skip_id,
                        agent_id=agent_id,
                        started_at=now,
                        status="skipped",
                        stage=stage,
                        meta_json='{"reason":"lease_held"}',
                    )
                    await runs.mark_skipped(
                        skip_id,
                        finished_at=now,
                        stage=stage,
                        error="lease_held",
                    )
                    await session.commit()
                    logger.info(
                        "lease_skipped",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                        status="skipped",
                        duration_ms=_elapsed_ms(started),
                    )
                    return "skipped"

                try:
                    now = utc_now()
                    run_id = cycle_id
                    await runs.create_run(
                        run_id=run_id,
                        agent_id=agent_id,
                        started_at=now,
                        status="running",
                        stage=stage,
                    )
                    await session.commit()

                    if self._cycle_hook is not None:
                        result = self._cycle_hook()
                        if hasattr(result, "__await__"):
                            await result  # type: ignore[misc]
                        final_stage = "lifecycle_check"
                        meta: dict[str, Any] = {"mode": "hook"}
                    else:
                        outcome = await self._run_research_and_candidates(
                            session=session,
                            agent_id=agent_id,
                            agent_name=agent.name,
                            agent_domain=agent.domain,
                            cycle_id=cycle_id,
                        )
                        if outcome["status"] == "skipped":
                            finished = utc_now()
                            await runs.mark_skipped(
                                run_id,
                                finished_at=finished,
                                stage=outcome["stage"],
                                error=outcome.get("reason"),
                            )
                            await runs.update_run(
                                run_id,
                                meta_json=json.dumps(outcome.get("meta") or {}),
                            )
                            await session.commit()
                            logger.info(
                                "cycle_skipped",
                                agent_id=agent_id,
                                cycle_id=cycle_id,
                                stage=outcome["stage"],
                                status="skipped",
                                reason=outcome.get("reason"),
                                duration_ms=_elapsed_ms(started),
                            )
                            return "skipped"
                        if outcome["status"] == "failed":
                            finished = utc_now()
                            await runs.mark_failed(
                                run_id,
                                finished_at=finished,
                                error=outcome.get("reason") or "pipeline_failed",
                                stage=outcome["stage"],
                            )
                            await runs.update_run(
                                run_id,
                                meta_json=json.dumps(outcome.get("meta") or {}),
                            )
                            await session.commit()
                            logger.info(
                                "cycle_failed",
                                agent_id=agent_id,
                                cycle_id=cycle_id,
                                stage=outcome["stage"],
                                status="failed",
                                reason=outcome.get("reason"),
                                duration_ms=_elapsed_ms(started),
                            )
                            return "failed"
                        final_stage = outcome["stage"]
                        meta = outcome.get("meta") or {}

                    finished = utc_now()
                    await agents.record_cycle_success(
                        agent_id,
                        last_cycle_at=finished,
                        updated_at=finished,
                    )
                    await runs.mark_success(
                        run_id,
                        finished_at=finished,
                        stage=final_stage,
                    )
                    await runs.update_run(run_id, meta_json=json.dumps(meta))
                    await session.commit()

                    logger.info(
                        "cycle_success",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=final_stage,
                        status="success",
                        duration_ms=_elapsed_ms(started),
                    )
                    return "success"
                except Exception as exc:
                    finished = utc_now()
                    if run_id is not None:
                        await runs.mark_failed(
                            run_id,
                            finished_at=finished,
                            error=str(exc),
                            stage=stage,
                        )
                    await session.commit()
                    logger.exception(
                        "cycle_failed",
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        stage=stage,
                        status="failed",
                        duration_ms=_elapsed_ms(started),
                    )
                    return "failed"
                finally:
                    async with self._session_factory() as release_session:
                        release_leases = GenerationLeaseManager(
                            release_session,
                            self._lease_seconds,
                        )
                        await release_leases.release(agent_id, lease_owner)
                        await release_session.commit()
        finally:
            await self._locks.release(agent_id)

    async def _run_research_and_candidates(
        self,
        *,
        session: AsyncSession,
        agent_id: str,
        agent_name: str,
        agent_domain: str,
        cycle_id: str,
    ) -> dict[str, Any]:
        settings = get_settings()
        decision = DecisionService(
            DecisionPolicy(
                enabled=settings.decision_enabled,
                min_research_items=settings.decision_min_research_items,
                min_grounded_items=settings.decision_min_grounded_items,
                min_candidates=settings.decision_min_candidates,
                min_selected=settings.decision_min_selected,
                min_consistency_score=settings.decision_min_consistency_score,
                allow_uncertain_debate=settings.decision_allow_uncertain_debate,
                max_trace_items=settings.decision_max_trace_items,
            )
        )
        decision.begin(agent_id=agent_id, cycle_id=cycle_id)
        decision_meta: dict[str, Any] = {"status": "running"}

        # Module 17 — non-authoritative observability sidecar (0 LLM; non-fatal).
        obs = ObservabilityService(
            enabled=settings.observability_enabled,
            max_events=settings.observability_max_events,
            max_reason_length=settings.observability_max_reason_length,
            max_evidence_items=settings.observability_max_evidence_items,
        )
        try:
            obs.begin(agent_id=agent_id, cycle_id=cycle_id)
        except Exception:
            logger.exception(
                "observability_begin_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="observability",
            )

        def _decision_payload() -> dict[str, Any]:
            try:
                payload = decision.meta_payload()
            except Exception as exc:
                logger.exception(
                    "decision_gate_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="decision",
                )
                payload = {
                    "final_action": "skip",
                    "status": "unavailable",
                    "reason": sanitize_error(exc),
                    "gates_passed": 0,
                    "gates_skipped": 0,
                    "gates_blocked": 0,
                    "trace": [],
                }
            # Preserve outer evaluate_gate crash marker (monkeypatch / unexpected raise).
            if decision_meta.get("status") == "unavailable":
                payload = {
                    **payload,
                    "status": "unavailable",
                    "reason": decision_meta.get("reason") or payload.get("reason"),
                    "final_action": "skip",
                }
            return payload

        def _obs_payload(**kwargs: Any) -> dict[str, Any]:
            try:
                return obs.meta_payload(**kwargs)
            except Exception as exc:
                logger.exception(
                    "observability_finalize_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="observability",
                )
                return {
                    "version": 1,
                    "status": "unavailable",
                    "reason": sanitize_error(exc),
                    "event_count": 0,
                    "events": [],
                    "published": False,
                    "final_reason": "observability_unavailable",
                }

        def _with_obs(
            meta: dict[str, Any],
            *,
            cycle_status: str,
            cycle_reason: str | None = None,
            **extra: Any,
        ) -> dict[str, Any]:
            try:
                meta["observability"] = _obs_payload(
                    cycle_status=cycle_status,
                    cycle_reason=cycle_reason,
                    decision=meta.get("decision") or _decision_payload(),
                    publication=meta.get("publication"),
                    final_generation=meta.get("final_generation"),
                    consistency=meta.get("consistency"),
                    evaluation=meta.get("evaluation"),
                    **extra,
                )
            except Exception:
                logger.exception(
                    "observability_attach_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="observability",
                )
            return meta

        obs.safe_record(
            "research_started",
            stage="research",
            action="observe",
            status="info",
            reason="Research gather started",
        )
        research_items = await self._research_hub.gather(
            query=agent_domain,
            agent_id=agent_id,
            cycle_id=cycle_id,
        )
        try:
            research_ctx = decision.build_context(
                agent_id=agent_id,
                cycle_id=cycle_id,
                research_items=research_items,
            )
            research_decision = decision.evaluate_gate("research", research_ctx)
            obs.record_decision_gate(
                "research",
                action=research_decision.action,
                reason=research_decision.reason,
                evidence=dict(research_decision.evidence),
                blocking=research_decision.blocking,
            )
        except Exception as exc:
            logger.exception(
                "decision_gate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                gate="research",
                stage="decision",
            )
            research_decision = None
            decision_meta = {
                "status": "unavailable",
                "reason": sanitize_error(exc),
            }

        obs.safe_record(
            "research_completed",
            stage="research",
            action="proceed" if research_items else "skip",
            status="ok" if research_items else "skipped",
            reason=(
                f"{len(research_items)} research items gathered"
                if research_items
                else "No research items available"
            ),
            evidence={"research_count": len(research_items)},
        )

        # Decision gate failure (research_decision is None) must skip — never continue.
        if (
            not research_items
            or research_decision is None
            or research_decision.action == "skip"
        ):
            skip_reason = (
                "decision_unavailable"
                if research_decision is None and research_items
                else "no_research_items"
            )
            return {
                "status": "skipped",
                "stage": "research",
                "reason": skip_reason,
                "meta": _with_obs(
                    {
                        "research_count": 0 if not research_items else len(research_items),
                        "decision": _decision_payload(),
                    },
                    cycle_status="skipped",
                    cycle_reason=skip_reason,
                ),
            }

        if not self._candidates.llm_available:
            return {
                "status": "skipped",
                "stage": "candidate_generation",
                "reason": "llm_unavailable",
                "meta": _with_obs(
                    {
                        "research_count": len(research_items),
                        "decision": _decision_payload(),
                    },
                    cycle_status="skipped",
                    cycle_reason="llm_unavailable",
                ),
            }

        candidates = await self._candidates.generate(
            persona_name=agent_name,
            persona_domain=agent_domain,
            research_items=research_items,
            agent_id=agent_id,
            cycle_id=cycle_id,
        )
        obs.safe_record(
            "candidate_generated",
            stage="candidate",
            action="proceed" if candidates else "skip",
            status="ok" if candidates else "skipped",
            reason=f"{len(candidates)} grounded candidates generated",
            evidence={"candidate_count": len(candidates)},
        )
        try:
            cand_decision = decision.evaluate_gate(
                "candidate",
                decision.build_context(
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    research_items=research_items,
                    candidates=candidates,
                ),
            )
            obs.record_decision_gate(
                "candidate",
                action=cand_decision.action,
                reason=cand_decision.reason,
                evidence=dict(cand_decision.evidence),
                blocking=cand_decision.blocking,
            )
        except Exception:
            logger.exception(
                "decision_gate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                gate="candidate",
                stage="decision",
            )
            cand_decision = None

        if (
            not candidates
            or cand_decision is None
            or cand_decision.action == "skip"
        ):
            cand_reason = (
                "decision_unavailable"
                if cand_decision is None and candidates
                else "no_grounded_candidates"
            )
            return {
                "status": "skipped",
                "stage": "candidate_generation",
                "reason": cand_reason,
                "meta": _with_obs(
                    {
                        "research_count": len(research_items),
                        "decision": _decision_payload(),
                    },
                    cycle_status="skipped",
                    cycle_reason=cand_reason,
                ),
            }

        topics_repo = TopicRepository(session)
        posts_repo = PostRepository(session)
        topic_ids = await persist_candidates(
            topics_repo,
            agent_id=agent_id,
            candidates=candidates,
        )

        previous_topics = [
            HistoricalTopic(topic_id=t.id, title=t.title)
            for t in await topics_repo.list_by_agent(agent_id)
            if t.id not in set(topic_ids)
        ]
        previous_posts = await posts_repo.list_by_agent_newest_first(agent_id)
        previous_titles = [t.title for t in previous_topics] + [
            p.topic or p.text[:120] for p in previous_posts
        ]
        context = ScoringContext(
            persona_name=agent_name,
            persona_domain=agent_domain,
            now=utc_now(),
            research_items=research_items,
            previous_topics=previous_topics,
            previous_post_titles=[p.topic or p.text[:120] for p in previous_posts],
            weights=settings.score_weights,
            top_k=settings.topic_funnel_top_k,
            tier1_domains=tuple(settings.domain_list(settings.source_quality_tier1_domains)),
            tier2_domains=tuple(settings.domain_list(settings.source_quality_tier2_domains)),
            tier3_domains=tuple(settings.domain_list(settings.source_quality_tier3_domains)),
        )

        ranked = self._scoring.rank_candidates(
            list(zip(topic_ids, candidates, strict=True)),
            context,
        )

        # Module 12: annotate with source diversity + story continuity (non-fatal).
        intelligence_by_topic: dict[str, dict[str, Any]] = {}
        try:
            diversity = SourceDiversityAnalyzer(
                lookback=settings.source_diversity_lookback
            )
            recent_domains = diversity.recent_domains_from_posts(previous_posts)
            story_tracker = StoryTracker(
                match_threshold=settings.story_match_threshold,
                lookback=settings.story_lookback,
            )
            prior_topics = [
                t
                for t in await topics_repo.list_by_agent(agent_id)
                if t.id not in set(topic_ids)
            ]
            clusters = story_tracker.clusters_from_topics(prior_topics)
            research_summaries = {
                (normalize_url(item.url) or item.url): (item.summary or item.title)
                for item in research_items
            }
            for item in ranked:
                div = diversity.analyze_candidate(
                    item.candidate,
                    recent_domains=recent_domains,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                )
                story = story_tracker.match_candidate(
                    item.candidate,
                    clusters=clusters,
                    previous_titles=previous_titles,
                    research_summaries=research_summaries,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    now=utc_now(),
                )
                intelligence_by_topic[item.topic_id] = {
                    "source_diversity_score": div.source_diversity,
                    "source_repetition": div.source_repetition,
                    "source_domain_count": div.source_domain_count,
                    "recent_source_overlap": div.recent_source_overlap,
                    "source_diversity": div.to_meta(),
                    "story_type": story.classification,
                    "story_continuity_score": story.match_score,
                    "story_update_strength": story.story_update_strength,
                    "match_score": story.match_score,
                    "previous_context": story.previous_context,
                    "what_changed": story.what_changed,
                    "new_evidence": story.new_evidence,
                    "story": story.to_meta(),
                    "contradiction": {"detected": False},
                }
        except Exception:
            logger.exception(
                "intelligence_annotation_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="intelligence",
            )

        decided_at = utc_now()
        selected_ids: list[str] = []
        rejected_ids: list[str] = []
        selected_scored = []
        for item in ranked:
            payload = scores_payload(
                item,
                weights=settings.score_weights,
                meta=intelligence_by_topic.get(item.topic_id),
            )
            await topics_repo.update_topic(
                item.topic_id,
                status=item.decision,
                scores_json=json.dumps(payload),
                decided_at=decided_at,
            )
            if item.decision == "selected":
                selected_ids.append(item.topic_id)
                selected_scored.append(item)
                obs.safe_record(
                    "candidate_selected",
                    stage="selection",
                    action="select",
                    status="ok",
                    reason="Highest weighted score among grounded candidates"
                    if item.rank == 1
                    else "Selected within top-k funnel",
                    evidence={
                        "final_score": item.scores.final_score,
                        "rank": item.rank,
                        "persona_fit": item.scores.persona_fit,
                        "recency": item.scores.recency,
                    },
                    related_topic_id=item.topic_id,
                    source_urls=list(getattr(item.candidate, "source_urls", []) or [])[
                        :5
                    ],
                )
            else:
                rejected_ids.append(item.topic_id)
                obs.safe_record(
                    "candidate_rejected",
                    stage="selection",
                    action="reject",
                    status="skipped",
                    reason="Outside top-k selection funnel",
                    evidence={
                        "final_score": item.scores.final_score,
                        "rank": item.rank,
                    },
                    related_topic_id=item.topic_id,
                )

        try:
            selection_decision = decision.evaluate_gate(
                "selection",
                decision.build_context(
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    research_items=research_items,
                    candidates=candidates,
                    selected_count=len(selected_ids),
                    rejected_count=len(rejected_ids),
                ),
            )
            obs.record_decision_gate(
                "selection",
                action=selection_decision.action,
                reason=selection_decision.reason,
                evidence=dict(selection_decision.evidence),
                blocking=selection_decision.blocking,
            )
        except Exception:
            logger.exception(
                "decision_gate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                gate="selection",
                stage="decision",
            )
            selection_decision = None

        if (
            not selected_ids
            or selection_decision is None
            or selection_decision.action == "skip"
        ):
            sel_reason = (
                "decision_unavailable"
                if selection_decision is None and selected_ids
                else "no_selected_candidates"
            )
            return {
                "status": "skipped",
                "stage": STAGE_RESEARCH_SCORING,
                "reason": sel_reason,
                "meta": _with_obs(
                    {
                        "research_count": len(research_items),
                        "candidate_count": len(candidates),
                        "selected_ids": selected_ids,
                        "rejected_ids": rejected_ids,
                        "decision": _decision_payload(),
                    },
                    cycle_status="skipped",
                    cycle_reason=sel_reason,
                ),
            }

        debate_limit = min(settings.debate_top_k, len(selected_scored))
        debated_ids: list[str] = []
        debate_errors: list[str] = []
        debate_calls_before = self._debate.call_count
        debated_pairs: list[tuple[Any, Any]] = []
        memory_retrieval_counts: list[int] = []
        retrieved_memories_all: list[Any] = []

        for scored in selected_scored[:debate_limit]:
            # Capture scores snapshot before debate (must remain unchanged).
            before_topic = await topics_repo.get_by_id(scored.topic_id)
            scores_before = before_topic.scores_json if before_topic else None

            relevant_memories = await self._memory.retrieve_for_topic(
                session,
                agent_id=agent_id,
                scored=scored,
                cycle_id=cycle_id,
            )
            memory_retrieval_counts.append(len(relevant_memories))
            retrieved_memories_all.extend(relevant_memories)

            result = await self._debate.debate_topic(
                scored,
                persona_name=agent_name,
                persona_domain=agent_domain,
                research_items=research_items,
                previous_titles=previous_titles,
                relevant_memories=relevant_memories,
                intelligence_context=intelligence_by_topic.get(scored.topic_id),
                agent_id=agent_id,
                cycle_id=cycle_id,
            )
            payload = debate_payload(
                result,
                final_score=scored.scores.final_score,
                rank=scored.rank,
            )
            await topics_repo.update_topic(
                scored.topic_id,
                debate_json=json.dumps(payload),
            )
            debated_ids.append(scored.topic_id)
            debated_pairs.append((scored, result))
            if result.error:
                debate_errors.append(scored.topic_id)
            else:
                debate_decision = getattr(result.decision, "value", result.decision)
                obs.safe_record(
                    "debate_completed",
                    stage="debate",
                    action="proceed" if debate_decision == "proceed" else "reject",
                    status="ok" if debate_decision == "proceed" else "blocked",
                    reason=f"Debate decision: {debate_decision}",
                    evidence={
                        "decision": debate_decision,
                        "confidence": result.confidence,
                        "rank": scored.rank,
                        "final_score": scored.scores.final_score,
                    },
                    related_topic_id=scored.topic_id,
                )

            after_topic = await topics_repo.get_by_id(scored.topic_id)
            if after_topic is not None and scores_before is not None:
                if after_topic.scores_json != scores_before:
                    raise RuntimeError(
                        "Invariant violated: debate mutated scores_json"
                    )
                if after_topic.status != "selected":
                    raise RuntimeError(
                        "Invariant violated: debate changed selected status"
                    )

        memory_meta: dict[str, Any] = {
            "status": "skipped",
            "created": 0,
            "updated": 0,
            "retrieval_counts": memory_retrieval_counts,
        }
        try:
            successful_debates = [
                (scored, result)
                for scored, result in debated_pairs
                if not result.error
            ]
            memory_meta = await self._memory.extract_and_upsert(
                session,
                agent_id=agent_id,
                persona_name=agent_name,
                persona_domain=agent_domain,
                debated=successful_debates,
                research_items=research_items,
                cycle_id=cycle_id,
            )
            memory_meta["retrieval_counts"] = memory_retrieval_counts
            retrieved_total = sum(memory_retrieval_counts) if memory_retrieval_counts else 0
            obs.safe_record(
                "memory_retrieved",
                stage="memory",
                action="observe",
                status="ok",
                reason=f"Memory retrieval considered {retrieved_total} items",
                evidence={"memory_items_considered": retrieved_total},
            )
            if int(memory_meta.get("created") or 0) or int(memory_meta.get("updated") or 0):
                obs.safe_record(
                    "memory_updated",
                    stage="memory",
                    action="observe",
                    status="ok",
                    reason="Memory graph updated from debate outcomes",
                    evidence={
                        "created": memory_meta.get("created"),
                        "updated": memory_meta.get("updated"),
                    },
                )
        except Exception as exc:
            logger.exception(
                "memory_extraction_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="memory_extraction",
            )
            memory_meta = {
                "status": "failed",
                "reason": str(exc),
                "created": 0,
                "updated": 0,
                "retrieval_counts": memory_retrieval_counts,
            }

        # Module 12: bounded contradiction analysis → existing MemoryService update path.
        contradiction_meta: dict[str, Any] = {"detected": False, "status": "skipped"}
        try:
            # Deduplicate retrieved memories by id for analysis.
            by_id = {m.id: m for m in retrieved_memories_all}
            if not by_id:
                # Fall back to agent memories so contradictions can still fire.
                from app.services.memory.store import MemoryStore

                for record in await MemoryStore(session).list_for_agent(agent_id):
                    by_id[record.id] = record
            detector = ContradictionDetector(
                self._llm,
                enabled=settings.contradiction_enabled,
                min_confidence=settings.contradiction_min_confidence,
                max_items=settings.contradiction_max_items,
            )
            analysis = await detector.analyze(
                memories=list(by_id.values()),
                research_items=research_items,
                agent_id=agent_id,
                cycle_id=cycle_id,
            )
            contradiction_meta = analysis.to_meta()
            if contradiction_meta.get("detected") or analysis.signals:
                obs.safe_record(
                    "contradiction_detected",
                    stage="intelligence",
                    action="observe",
                    status="ok" if contradiction_meta.get("detected") else "info",
                    reason="Contradiction analysis completed",
                    evidence={
                        "detected": bool(contradiction_meta.get("detected")),
                        "signal_count": len(analysis.signals or []),
                    },
                )
            if analysis.signals:
                challenges = challenge_payload(analysis.signals)
                challenge_result = await self._memory.apply_contradiction_challenges(
                    session,
                    challenges=challenges,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                )
                contradiction_meta["belief_challenge"] = challenge_result
                # Attach top contradiction onto each selected topic intelligence blob.
                top = analysis.to_meta()
                for topic_id, intel in intelligence_by_topic.items():
                    intel["contradiction"] = top
                    intel["contradiction_signal"] = top
                    # Refresh scores_json meta without mutating Module 5 score fields.
                    topic_row = await topics_repo.get_by_id(topic_id)
                    if topic_row and topic_row.scores_json:
                        try:
                            existing = json.loads(topic_row.scores_json)
                            existing["meta"] = intel
                            await topics_repo.update_topic(
                                topic_id,
                                scores_json=json.dumps(existing),
                            )
                        except Exception:
                            pass
        except Exception:
            logger.exception(
                "contradiction_stage_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="contradiction",
            )
            contradiction_meta = {"detected": False, "status": "failed"}

        consistency_meta: list[dict[str, Any]] = []
        consistency_triples: list[tuple[Any, Any, Any, list[Any]]] = []
        historical_topics = [
            t
            for t in await topics_repo.list_by_agent(agent_id)
            if t.id not in set(topic_ids)
        ][: settings.consistency_history_limit]

        for scored, debate_result in debated_pairs:
            before_topic = await topics_repo.get_by_id(scored.topic_id)
            scores_before = before_topic.scores_json if before_topic else None
            debate_before = before_topic.debate_json if before_topic else None

            try:
                memories = await self._memory.retrieve_for_topic(
                    session,
                    agent_id=agent_id,
                    scored=scored,
                    cycle_id=cycle_id,
                )
                consistency_result, _updated = self._consistency.evaluate(
                    scored,
                    debate=None if debate_result.error else debate_result,
                    persona_name=agent_name,
                    persona_domain=agent_domain,
                    research_items=research_items,
                    memories=memories,
                    historical_topics=historical_topics,
                    previous_titles=previous_titles,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                )
            except Exception as exc:
                logger.exception(
                    "consistency_analysis_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    topic_id=scored.topic_id,
                    stage="consistency",
                )
                from app.services.consistency.models import (
                    ConsistencyResult,
                    ConsistencySignals,
                )

                memories = []
                consistency_result = ConsistencyResult(
                    topic_id=scored.topic_id,
                    decision=ConsistencyDecisionEnum.REJECT,
                    overall_score=0.0,
                    signals=ConsistencySignals(),
                    reasons=["consistency_analysis_failed"],
                    hard_reject=True,
                    error=str(exc),
                )

            payload = consistency_payload(consistency_result)
            await topics_repo.update_topic(
                scored.topic_id,
                consistency_json=json.dumps(payload),
            )
            consistency_meta.append(
                {
                    "topic_id": scored.topic_id,
                    "decision": consistency_result.decision.value,
                    "overall_score": consistency_result.overall_score,
                }
            )
            consistency_triples.append(
                (scored, debate_result, consistency_result, memories)
            )
            if consistency_result.decision == ConsistencyDecisionEnum.REJECT:
                obs.safe_record(
                    "consistency_rejected",
                    stage="consistency",
                    action="reject",
                    status="blocked",
                    reason=(
                        "; ".join(consistency_result.reasons[:2])
                        if consistency_result.reasons
                        else "Consistency guard rejected candidate"
                    ),
                    evidence={"overall_score": consistency_result.overall_score},
                    related_topic_id=scored.topic_id,
                )
            else:
                obs.safe_record(
                    "consistency_checked",
                    stage="consistency",
                    action="proceed",
                    status="ok",
                    reason=f"Consistency decision: {consistency_result.decision.value}",
                    evidence={
                        "decision": consistency_result.decision.value,
                        "overall_score": consistency_result.overall_score,
                    },
                    related_topic_id=scored.topic_id,
                )

            after_topic = await topics_repo.get_by_id(scored.topic_id)
            if after_topic is not None:
                if scores_before is not None and after_topic.scores_json != scores_before:
                    raise RuntimeError(
                        "Invariant violated: consistency mutated scores_json"
                    )
                if debate_before is not None and after_topic.debate_json != debate_before:
                    raise RuntimeError(
                        "Invariant violated: consistency mutated debate_json"
                    )

        # Module 16: mid-pipeline decision gates (inspect-only; modules remain authoritative).
        decision_block_generation = False
        try:
            mid_ctx = decision.build_context(
                agent_id=agent_id,
                cycle_id=cycle_id,
                research_items=research_items,
                candidates=candidates,
                selected_count=len(selected_ids),
                rejected_count=len(rejected_ids),
                debated_pairs=debated_pairs,
                consistency_triples=consistency_triples,
                memory_meta=memory_meta,
                contradiction_meta=contradiction_meta,
                intelligence_by_topic=intelligence_by_topic,
            )
            for gate_name in ("debate", "memory", "consistency", "intelligence"):
                gate_result = decision.evaluate_gate(gate_name, mid_ctx)
                obs.record_decision_gate(
                    gate_name,
                    action=gate_result.action,
                    reason=gate_result.reason,
                    evidence=dict(gate_result.evidence),
                    blocking=gate_result.blocking,
                )
                if gate_result.action in {"skip", "stop"} or gate_result.blocking:
                    decision_block_generation = True
            if intelligence_by_topic:
                sample = next(iter(intelligence_by_topic.values()))
                obs.safe_record(
                    "intelligence_analyzed",
                    stage="intelligence",
                    action="observe",
                    status="ok",
                    reason="Source diversity and story continuity annotated",
                    evidence={
                        "topics_annotated": len(intelligence_by_topic),
                        "source_diversity_score": sample.get("source_diversity_score"),
                        "story_type": sample.get("story_type"),
                    },
                )
        except Exception:
            logger.exception(
                "decision_gate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="decision",
            )
            # Prefer skip over publish when decision orchestration is uncertain.
            decision_block_generation = True
            decision_meta = {"status": "unavailable"}

        # Module 13: adaptive learning sidecar (non-fatal; never replaces Module 5).
        adaptive_meta: dict[str, Any] = {"status": "skipped"}
        adaptive_context_payload: dict[str, Any] = {}
        try:
            adaptive = AdaptiveLearningService(
                enabled=settings.adaptive_learning_enabled,
                lookback_posts=settings.adaptive_lookback_posts,
                max_signals=settings.adaptive_max_signals,
                max_context_items=settings.adaptive_max_context_items,
                stale_days=settings.adaptive_stale_days,
                decay_days=settings.adaptive_decay_days,
            )
            all_topics_for_agent = await topics_repo.list_by_agent(agent_id)
            adaptive_ctx = await adaptive.run(
                session,
                agent_id=agent_id,
                posts=previous_posts,
                topics=all_topics_for_agent,
                intelligence_by_topic=intelligence_by_topic,
                contradiction_meta=contradiction_meta,
                selected_candidates=[s.candidate for s in selected_scored[:3]],
                cycle_id=cycle_id,
            )
            adaptive_context_payload = adaptive_ctx.to_meta()
            adaptive_meta = {
                "status": adaptive_ctx.status,
                "reason": adaptive_ctx.reason,
                "llm_calls": adaptive.llm_calls,
                "overused_topics": adaptive_ctx.overused_topics[:5],
                "undercovered_topics": adaptive_ctx.undercovered_topics[:5],
                "source_dependencies": adaptive_ctx.source_dependencies[:5],
                "stale_stories": adaptive_ctx.stale_stories[:5],
                "revived_stories": adaptive_ctx.revived_stories[:5],
                "quality_feedback": dict(
                    list(adaptive_ctx.quality_feedback.items())[:10]
                ),
            }
            obs.safe_record(
                "adaptive_guidance_applied",
                stage="adaptive",
                action="observe",
                status="ok" if adaptive_ctx.status != "failed" else "failed",
                reason=str(adaptive_ctx.reason or adaptive_ctx.status)[:300],
                evidence={"status": adaptive_ctx.status},
            )
        except Exception as exc:
            logger.exception(
                "adaptive_learning_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="adaptive_learning",
            )
            adaptive_meta = {"status": "failed", "reason": sanitize_error(exc)}

        # Module 15: controlled learning (guidance only; never rewrites persona/weights).
        learning_meta: dict[str, Any] = {"status": "skipped"}
        learning_context_payload: dict[str, Any] = {}
        try:
            learner = ControlledLearningService(
                enabled=settings.learning_enabled,
                min_observations=settings.learning_min_observations,
                min_confidence=settings.learning_min_confidence,
                max_signals=settings.learning_max_signals,
                max_context_items=settings.learning_max_context_items,
                decay_days=settings.learning_decay_days,
                lookback_evaluations=settings.learning_lookback_evaluations,
            )
            learning_ctx = await learner.run(
                session,
                agent_id=agent_id,
                adaptive_meta=adaptive_meta,
                quality_feedback=adaptive_meta.get("quality_feedback")
                if isinstance(adaptive_meta.get("quality_feedback"), dict)
                else {},
                cycle_id=cycle_id,
            )
            learning_context_payload = learning_ctx.to_meta()
            learning_meta = {
                "status": learning_ctx.status,
                "reason": learning_ctx.reason,
                "llm_calls": learner.llm_calls,
                "active_strategies": learning_ctx.active_strategies[:5],
                "recommendations": learning_ctx.recommendations[:5],
                "signal_count": len(learning_ctx.relevant_signals),
            }
            obs.safe_record(
                "learning_guidance_applied",
                stage="learning",
                action="observe",
                status="ok" if learning_ctx.status != "failed" else "failed",
                reason=str(learning_ctx.reason or learning_ctx.status)[:300],
                evidence={
                    "status": learning_ctx.status,
                    "strategies": len(learning_ctx.active_strategies or []),
                },
            )
        except Exception as exc:
            logger.exception(
                "controlled_learning_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="controlled_learning",
            )
            learning_meta = {"status": "failed", "reason": sanitize_error(exc)}

        # Module 9: final post generation for strongest approved finalist only.
        posts_before_final = len(await posts_repo.list_by_agent_newest_first(agent_id))
        final_meta: dict[str, Any] = {
            "status": "skipped",
            "reason": "no_approved_finalist",
        }
        publication_meta: dict[str, Any] = {
            "status": "skipped",
            "reason": "no_generated_post",
        }
        approved = [
            (scored, debate, consistency, memories)
            for scored, debate, consistency, memories in consistency_triples
            if (
                not debate.error
                and consistency.decision == ConsistencyDecisionEnum.APPROVE
            )
        ]
        selected_finalist = None
        final_context = None
        final_outcome = None

        try:
            adaptive_gate = decision.evaluate_gate(
                "adaptive",
                decision.build_context(
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    research_items=research_items,
                    candidates=candidates,
                    selected_count=len(selected_ids),
                    rejected_count=len(rejected_ids),
                    debated_pairs=debated_pairs,
                    consistency_triples=consistency_triples,
                    adaptive_meta=adaptive_meta,
                    learning_meta=learning_meta,
                    intelligence_by_topic=intelligence_by_topic,
                ),
            )
            obs.record_decision_gate(
                "adaptive",
                action=adaptive_gate.action,
                reason=adaptive_gate.reason,
                evidence=dict(adaptive_gate.evidence),
                blocking=adaptive_gate.blocking,
            )
            gen_decision = decision.evaluate_gate(
                "generation",
                decision.build_context(
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    research_items=research_items,
                    candidates=candidates,
                    selected_count=len(selected_ids),
                    rejected_count=len(rejected_ids),
                    debated_pairs=debated_pairs,
                    consistency_triples=consistency_triples,
                    adaptive_meta=adaptive_meta,
                    learning_meta=learning_meta,
                ),
            )
            obs.record_decision_gate(
                "generation",
                action=gen_decision.action,
                reason=gen_decision.reason,
                evidence=dict(gen_decision.evidence),
                blocking=gen_decision.blocking,
            )
            if gen_decision.action != "proceed" or decision_block_generation:
                decision_block_generation = True
        except Exception:
            logger.exception(
                "decision_gate_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                gate="generation",
                stage="decision",
            )
            decision_block_generation = True

        if decision_block_generation:
            final_meta = {
                "status": "skipped",
                "reason": "decision_gate_blocked_generation",
            }
        elif not approved:
            final_meta = {"status": "skipped", "reason": "no_approved_finalist"}
        elif not self._final_post.available:
            final_meta = {"status": "skipped", "reason": "llm_unavailable"}
        else:
            obs.safe_record(
                "final_generation_started",
                stage="generation",
                action="proceed",
                status="info",
                reason="Final post generation started for approved finalist",
            )
            approved.sort(
                key=lambda item: (item[0].rank, -item[0].scores.final_score)
            )
            scored, debate, consistency, memories = approved[0]
            selected_finalist = (scored, debate, consistency, memories)
            alternatives = [
                other
                for other in ranked
                if other.topic_id != scored.topic_id
            ]
            try:
                final_intel = intelligence_by_topic.get(scored.topic_id) or {}
                guidance = (adaptive_context_payload.get("candidate_guidance") or {}).get(
                    scored.title[:80]
                ) or {}
                final_context = self._final_post.build_context(
                    persona_name=agent_name,
                    persona_domain=agent_domain,
                    scored=scored,
                    debate=debate,
                    consistency=consistency,
                    memories=memories,
                    research_items=research_items,
                    alternatives=alternatives,
                    previous_titles=previous_titles,
                    intelligence={
                        "story_type": final_intel.get("story_type"),
                        "what_changed": final_intel.get("what_changed"),
                        "previous_context": final_intel.get("previous_context"),
                        "contradiction_detected": bool(
                            (final_intel.get("contradiction") or {}).get("detected")
                        ),
                        "belief_context": (
                            (final_intel.get("contradiction") or {}).get("memory_key")
                        ),
                        "source_diversity_score": final_intel.get(
                            "source_diversity_score"
                        ),
                        "adaptive": {
                            "overused_topics": (
                                adaptive_context_payload.get("overused_topics") or []
                            )[:5],
                            "undercovered_topics": (
                                adaptive_context_payload.get("undercovered_topics") or []
                            )[:5],
                            "quality_feedback": (
                                adaptive_context_payload.get("quality_feedback") or {}
                            ),
                            "guidance": guidance,
                        },
                        "learning": {
                            "recommendations": (
                                learning_context_payload.get("recommendations") or []
                            )[:5],
                            "active_strategies": (
                                learning_context_payload.get("active_strategies") or []
                            )[:3],
                            "novelty_preferences": (
                                learning_context_payload.get("novelty_preferences") or []
                            )[:3],
                            "evidence_preferences": (
                                learning_context_payload.get("evidence_preferences") or []
                            )[:3],
                        },
                    },
                )
                final_outcome = await self._final_post.generate(
                    final_context,
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                )
                final_meta = outcome_meta(final_outcome)
                final_meta["topic_id"] = scored.topic_id
                if final_outcome.status == "success":
                    obs.safe_record(
                        "final_generation_completed",
                        stage="generation",
                        action="proceed",
                        status="ok",
                        reason="Final post generated successfully",
                        related_topic_id=scored.topic_id,
                        source_urls=list(
                            getattr(final_outcome.generated_post, "source_urls", [])
                            or []
                        )[:5]
                        if final_outcome.generated_post
                        else [],
                    )
                else:
                    obs.safe_record(
                        "final_validation_failed",
                        stage="generation",
                        action="reject",
                        status="failed",
                        reason=str(final_meta.get("reason") or final_outcome.status),
                        related_topic_id=scored.topic_id,
                    )
            except Exception as exc:
                logger.exception(
                    "final_generation_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="final_generation",
                )
                final_meta = {
                    "status": "failed",
                    "reason": str(exc),
                    "topic_id": scored.topic_id,
                }
                obs.safe_record(
                    "final_validation_failed",
                    stage="generation",
                    action="reject",
                    status="failed",
                    reason=sanitize_error(exc),
                )

        # Final generation must not insert posts; publishing is the sole insert path.
        posts_after_final = len(await posts_repo.list_by_agent_newest_first(agent_id))
        if posts_after_final != posts_before_final:
            raise RuntimeError(
                "Invariant violated: posts changed during final generation"
            )

        # Module 10: publish validated GeneratedPost via PublishingService only.
        # Module 16 publishing gate must approve first (never publish on decision failure).
        allow_publish = False
        if (
            final_outcome is not None
            and final_outcome.status == "success"
            and final_outcome.generated_post is not None
            and selected_finalist is not None
            and final_context is not None
        ):
            try:
                pub_decision = decision.evaluate_gate(
                    "publishing",
                    decision.build_context(
                        agent_id=agent_id,
                        cycle_id=cycle_id,
                        research_items=research_items,
                        candidates=candidates,
                        selected_count=len(selected_ids),
                        debated_pairs=debated_pairs,
                        consistency_triples=consistency_triples,
                        final_meta=final_meta,
                        generated_post=final_outcome.generated_post,
                        final_validation_passed=True,
                    ),
                )
                obs.record_decision_gate(
                    "publishing",
                    action=pub_decision.action,
                    reason=pub_decision.reason,
                    evidence=dict(pub_decision.evidence),
                    blocking=pub_decision.blocking,
                )
                allow_publish = pub_decision.action == "proceed"
            except Exception:
                logger.exception(
                    "decision_gate_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="decision",
                    gate="publishing",
                )
                allow_publish = False
                publication_meta = {
                    "status": "skipped",
                    "reason": "decision_unavailable_prefer_skip",
                }

        if (
            allow_publish
            and final_outcome is not None
            and final_outcome.status == "success"
            and final_outcome.generated_post is not None
            and selected_finalist is not None
            and final_context is not None
        ):
            scored, _debate, consistency, _memories = selected_finalist
            try:
                obs.safe_record(
                    "publishing_started",
                    stage="publishing",
                    action="publish",
                    status="info",
                    reason="PublishingService invoked for validated post",
                    related_topic_id=scored.topic_id,
                )
                publisher = PublishingService(session)
                pub = await publisher.publish(
                    agent_id=agent_id,
                    generated_post=final_outcome.generated_post,
                    grounded_urls=final_context.grounded_urls,
                    topic_title=scored.title,
                    topic_id=scored.topic_id,
                    consistency_approved=(
                        consistency.decision == ConsistencyDecisionEnum.APPROVE
                    ),
                    final_validation_passed=True,
                    cycle_id=cycle_id,
                )
                publication_meta = {
                    "status": pub.status,
                    "reason": pub.reason,
                    "published_post_id": pub.post_id,
                    "content_hash": pub.content_hash,
                    "topic_id": scored.topic_id,
                }
                if pub.post is not None:
                    publication_meta["created_at"] = pub.post.created_at.strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                if pub.status == "published":
                    obs.safe_record(
                        "publishing_completed",
                        stage="publishing",
                        action="publish",
                        status="ok",
                        reason="Post published via PublishingService",
                        related_topic_id=scored.topic_id,
                        related_post_id=pub.post_id,
                    )
                else:
                    obs.safe_record(
                        "publishing_skipped",
                        stage="publishing",
                        action="skip",
                        status="skipped",
                        reason=str(pub.reason or pub.status),
                        related_topic_id=scored.topic_id,
                    )
                # Record duplicate skip in decision trace when publisher reports it.
                if pub.status == "duplicate":
                    try:
                        decision.evaluate_gate(
                            "publishing",
                            decision.build_context(
                                agent_id=agent_id,
                                cycle_id=cycle_id,
                                publication_meta=publication_meta,
                                generated_post=final_outcome.generated_post,
                                final_validation_passed=True,
                                consistency_triples=consistency_triples,
                            ),
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception(
                    "publication_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="publishing",
                )
                publication_meta = {
                    "status": "failed",
                    "reason": sanitize_error(exc),
                    "topic_id": scored.topic_id,
                }
                obs.safe_record(
                    "publishing_skipped",
                    stage="publishing",
                    action="skip",
                    status="failed",
                    reason=sanitize_error(exc),
                )
        elif publication_meta.get("status") == "skipped" and publication_meta.get(
            "reason"
        ) == "decision_unavailable_prefer_skip":
            obs.safe_record(
                "publishing_skipped",
                stage="publishing",
                action="skip",
                status="skipped",
                reason="decision_unavailable_prefer_skip",
            )
        elif not allow_publish and final_meta.get("status") == "success":
            publication_meta = {
                "status": "skipped",
                "reason": "decision_gate_blocked_publishing",
            }
            obs.safe_record(
                "publishing_skipped",
                stage="publishing",
                action="skip",
                status="blocked",
                reason="decision_gate_blocked_publishing",
            )
        elif final_meta.get("status") == "skipped":
            publication_meta = {
                "status": "skipped",
                "reason": final_meta.get("reason") or "final_generation_skipped",
            }
            obs.safe_record(
                "publishing_skipped",
                stage="publishing",
                action="skip",
                status="skipped",
                reason=str(publication_meta.get("reason")),
            )
        elif final_meta.get("status") != "success":
            publication_meta = {
                "status": "skipped",
                "reason": "final_generation_not_successful",
            }
            obs.safe_record(
                "publishing_skipped",
                stage="publishing",
                action="skip",
                status="skipped",
                reason="final_generation_not_successful",
            )

        # Module 14: evaluate published post (non-fatal; never undoes publication).
        evaluation_meta: dict[str, Any] = {"status": "skipped"}
        if publication_meta.get("status") == "published" and publication_meta.get(
            "published_post_id"
        ):
            try:
                posts_now = await posts_repo.list_by_agent_newest_first(agent_id)
                published = next(
                    (
                        p
                        for p in posts_now
                        if p.id == publication_meta.get("published_post_id")
                    ),
                    None,
                )
                if published is not None:
                    prior_for_eval = [
                        p
                        for p in posts_now
                        if p.id != published.id
                    ][: settings.evaluation_lookback_posts]
                    cons_for_eval: dict[str, Any] = {"decision": "approve"}
                    intel_for_eval: dict[str, Any] = {}
                    if selected_finalist is not None:
                        scored_f, _d_f, consistency_f, _m_f = selected_finalist
                        cons_for_eval = {
                            "decision": consistency_f.decision.value
                            if hasattr(consistency_f.decision, "value")
                            else str(consistency_f.decision),
                            "overall_score": consistency_f.overall_score,
                        }
                        intel_for_eval = intelligence_by_topic.get(scored_f.topic_id) or {}
                    grounded = (
                        list(final_context.grounded_urls)
                        if final_context is not None
                        else []
                    )
                    research_urls = [item.url for item in research_items if item.url]
                    evaluator = PostEvaluator(
                        self._llm,
                        enabled=settings.evaluation_enabled,
                        reflection_enabled=settings.evaluation_reflection_enabled,
                        excellent_threshold=settings.evaluation_excellent_threshold,
                        good_threshold=settings.evaluation_good_threshold,
                        acceptable_threshold=settings.evaluation_acceptable_threshold,
                        weak_threshold=settings.evaluation_weak_threshold,
                    )
                    evaluation = await evaluator.evaluate(
                        session,
                        agent_id=agent_id,
                        post=published,
                        cycle_id=cycle_id,
                        grounded_urls=grounded or research_urls,
                        research_urls=research_urls,
                        previous_posts=prior_for_eval,
                        consistency_meta=cons_for_eval,
                        intelligence_meta=intel_for_eval,
                        persona_domain=agent_domain,
                        persist=True,
                    )
                    evaluation_meta = evaluation.to_meta()
                    evaluation_meta["llm_calls"] = evaluator.llm_calls
                    obs.safe_record(
                        "evaluation_completed",
                        stage="evaluation",
                        action="complete",
                        status="ok" if evaluation.status == "success" else "failed",
                        reason=str(
                            evaluation_meta.get("reason")
                            or evaluation.status
                            or "evaluation_completed"
                        )[:300],
                        related_post_id=publication_meta.get("published_post_id"),
                        evidence={"status": evaluation.status},
                    )
                    # Cycle N: publish → evaluate → learn (for cycle N+1 retrieval).
                    if evaluation.status == "success":
                        try:
                            post_learner = ControlledLearningService(
                                enabled=settings.learning_enabled,
                                min_observations=settings.learning_min_observations,
                                min_confidence=settings.learning_min_confidence,
                                max_signals=settings.learning_max_signals,
                                max_context_items=settings.learning_max_context_items,
                                decay_days=settings.learning_decay_days,
                                lookback_evaluations=settings.learning_lookback_evaluations,
                            )
                            post_learn_ctx = await post_learner.observe_evaluation(
                                session,
                                agent_id=agent_id,
                                evaluation=evaluation,
                                adaptive_meta=adaptive_meta,
                                cycle_id=cycle_id,
                            )
                            learning_meta["post_evaluation_update"] = {
                                "status": post_learn_ctx.status,
                                "signal_count": len(post_learn_ctx.relevant_signals),
                            }
                        except Exception as learn_exc:
                            logger.exception(
                                "controlled_learning_failed",
                                agent_id=agent_id,
                                cycle_id=cycle_id,
                                stage="controlled_learning",
                            )
                            learning_meta["post_evaluation_update"] = {
                                "status": "failed",
                                "reason": sanitize_error(learn_exc),
                            }
                else:
                    evaluation_meta = {
                        "status": "skipped",
                        "reason": "published_post_missing",
                    }
            except Exception as exc:
                logger.exception(
                    "evaluation_failed",
                    agent_id=agent_id,
                    cycle_id=cycle_id,
                    stage="evaluation",
                )
                evaluation_meta = {
                    "status": "failed",
                    "reason": sanitize_error(exc),
                }

        pub_status = publication_meta.get("status")
        if pub_status == "published":
            cycle_status = "success"
            cycle_reason = None
        elif pub_status == "duplicate":
            cycle_status = "skipped"
            cycle_reason = "duplicate"
        elif pub_status == "rejected":
            cycle_status = "skipped"
            cycle_reason = publication_meta.get("reason") or "validation_failed"
        elif pub_status == "failed":
            cycle_status = "failed"
            cycle_reason = publication_meta.get("reason") or "publication_failed"
        else:
            # Final generation skipped/rejected: pipeline stages completed without a post.
            cycle_status = "success"
            cycle_reason = None

        memory_items_retrieved = sum(
            len(memories) for _s, _d, _c, memories in consistency_triples
        )
        consistency_rejected = sum(
            1 for item in consistency_meta if item.get("decision") == "reject"
        )
        decision_meta = _decision_payload()
        summary = {
            "research_items": len(research_items),
            "candidate_count": len(candidates),
            "selected_count": len(selected_ids),
            "debated_count": len(debated_ids),
            "memory_items_retrieved": memory_items_retrieved,
            "consistency_rejected": consistency_rejected,
            "final_generation": final_meta.get("status"),
            "publication": publication_meta.get("status"),
            "contradiction_detected": bool(contradiction_meta.get("detected")),
            "adaptive_learning": adaptive_meta.get("status"),
            "controlled_learning": learning_meta.get("status"),
            "evaluation": evaluation_meta.get("status"),
            "decision": decision_meta.get("final_action"),
        }

        why_published_detail: dict[str, Any] = {}
        try:
            if publication_meta.get("status") == "published" and selected_finalist:
                scored_w, debate_w, cons_w, _mem_w = selected_finalist
                intel_w = intelligence_by_topic.get(scored_w.topic_id) or {}
                src_urls = (
                    list(final_context.grounded_urls)
                    if final_context is not None
                    else list(getattr(scored_w.candidate, "source_urls", []) or [])
                )
                why_published_detail = obs.explain_why_published(
                    selected={
                        "title": scored_w.title,
                        "rank": scored_w.rank,
                        "final_score": scored_w.scores.final_score,
                    },
                    debate={
                        "decision": getattr(debate_w, "decision", None),
                        "confidence": getattr(debate_w, "confidence", None),
                    },
                    consistency={
                        "decision": cons_w.decision.value
                        if hasattr(cons_w.decision, "value")
                        else str(cons_w.decision),
                        "overall_score": cons_w.overall_score,
                    },
                    intelligence=intel_w,
                    adaptive=adaptive_meta,
                    learning=learning_meta,
                    final_generation=final_meta,
                    publication=publication_meta,
                    evaluation=evaluation_meta,
                    sources=src_urls,
                )
        except Exception:
            logger.exception(
                "observability_why_published_failed",
                agent_id=agent_id,
                cycle_id=cycle_id,
                stage="observability",
            )

        mem_explain = obs.memory_explanation(
            memory_meta=memory_meta,
            items_considered=memory_items_retrieved,
        )
        learn_explain = obs.learning_explanation(
            adaptive_meta=adaptive_meta,
            learning_meta=learning_meta,
        )
        source_explain = obs.source_explanation(
            source_urls=(
                list(final_context.grounded_urls)
                if final_context is not None
                else []
            ),
            intelligence=(
                intelligence_by_topic.get(selected_finalist[0].topic_id)
                if selected_finalist
                else None
            ),
        )

        meta_body: dict[str, Any] = {
            "summary": summary,
            "research_count": len(research_items),
            "candidate_count": len(candidates),
            "topic_ids": topic_ids,
            "selected_ids": selected_ids,
            "rejected_ids": rejected_ids,
            "debated_ids": debated_ids,
            "debate_errors": debate_errors,
            "top_k": settings.topic_funnel_top_k,
            "debate_top_k": settings.debate_top_k,
            "debate_llm_calls": self._debate.call_count - debate_calls_before,
            "memory": memory_meta,
            "memory_extraction_llm_calls": self._memory.extraction_call_count,
            "consistency": consistency_meta,
            "intelligence": {
                "topics_annotated": len(intelligence_by_topic),
                "contradiction": contradiction_meta,
            },
            "adaptive_learning": adaptive_meta,
            "controlled_learning": learning_meta,
            "final_generation": final_meta,
            "publication": publication_meta,
            "evaluation": evaluation_meta,
            "decision": decision_meta,
        }
        meta_body = _with_obs(
            meta_body,
            cycle_status=cycle_status,
            cycle_reason=cycle_reason,
            why_published=why_published_detail,
            memory=mem_explain,
            learning=learn_explain,
            sources=source_explain,
        )

        result: dict[str, Any] = {
            "status": cycle_status,
            "stage": STAGE_PUBLISHING,
            "meta": meta_body,
        }
        if cycle_reason is not None:
            result["reason"] = cycle_reason
        return result

    async def _record_skip(
        self,
        agent_id: str,
        *,
        reason: str,
        stage: str,
    ) -> None:
        try:
            async with self._session_factory() as session:
                runs = PipelineRunRepository(session)
                now = utc_now()
                run_id = str(uuid.uuid4())
                await runs.create_run(
                    run_id=run_id,
                    agent_id=agent_id,
                    started_at=now,
                    status="skipped",
                    stage=stage,
                    meta_json=f'{{"reason":"{reason}"}}',
                )
                await runs.mark_skipped(
                    run_id,
                    finished_at=now,
                    stage=stage,
                    error=reason,
                )
                await session.commit()
        except Exception:
            logger.exception("skip_record_failed", agent_id=agent_id, reason=reason)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
