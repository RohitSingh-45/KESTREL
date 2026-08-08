"""Read-only dashboard aggregator. Never triggers generation or publishing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.services.observability.sanitizer import sanitize_mapping

PIPELINE_STAGES = (
    ("research", "Research"),
    ("ai_security", "AI-Security Filter"),
    ("candidate", "Candidates"),
    ("selection", "Score"),
    ("debate", "Debate"),
    ("memory", "Memory"),
    ("consistency", "Consistency"),
    ("intelligence", "Intelligence"),
    ("adaptive", "Adapt"),
    ("learning", "Learn"),
    ("decision", "Decide"),
    ("generation", "Generate"),
    ("publishing", "Publish"),
    ("evaluation", "Evaluate"),
)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url[:64]


def _load_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class DashboardService:
    """Operator-facing read models derived from persisted agent state."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        probed_llm_status: str | None = None,
    ) -> None:
        self._session = session
        self._agents = AgentRepository(session)
        self._posts = PostRepository(session)
        self._topics = TopicRepository(session)
        self._memory = MemoryRepository(session)
        self._runs = PipelineRunRepository(session)
        self._probed_llm_status = probed_llm_status

    async def list_agents(self) -> list[dict[str, Any]]:
        agents = await self._agents.list_all(limit=50)
        return [
            {
                "agentId": a.id,
                "name": a.name,
                "domain": a.domain,
                "status": a.status,
                "cycleCount": a.cycle_count,
                "lastCycleAt": _iso(a.last_cycle_at),
            }
            for a in agents
        ]

    async def get_state(self, agent_id: str) -> dict[str, Any] | None:
        agent = await self._agents.get_by_id(agent_id)
        if agent is None:
            return None

        settings = get_settings()
        posts = await self._posts.list_by_agent_newest_first(agent_id)
        topics = await self._topics.list_by_agent(agent_id)
        memories = await self._memory.list_for_agent(agent_id)
        runs = await self._runs.list_by_agent(agent_id)
        latest = runs[0] if runs else None
        meta = _load_json(latest.meta_json if latest else None)
        obs = meta.get("observability") if isinstance(meta.get("observability"), dict) else {}
        decision = meta.get("decision") if isinstance(meta.get("decision"), dict) else {}
        evaluation = meta.get("evaluation") if isinstance(meta.get("evaluation"), dict) else {}
        publication = meta.get("publication") if isinstance(meta.get("publication"), dict) else {}
        intelligence = meta.get("intelligence") if isinstance(meta.get("intelligence"), dict) else {}
        adaptive = (
            meta.get("adaptive_learning")
            if isinstance(meta.get("adaptive_learning"), dict)
            else {}
        )
        learning = (
            meta.get("controlled_learning")
            if isinstance(meta.get("controlled_learning"), dict)
            else {}
        )

        live_status = self._live_status(agent.status, latest.status if latest else None)
        llm_status = self._llm_status(
            settings.openai_api_key,
            meta,
            latest.status if latest else None,
            latest.error if latest else None,
            probed_status=getattr(self, "_probed_llm_status", None),
        )

        return sanitize_mapping(
            {
                "hero": {
                    "brand": agent.name or "Kestrel",
                    "tagline": "Autonomous AI Security Agent",
                    "supporting": "Researching. Reasoning. Learning. Publishing.",
                    "liveStatus": live_status,
                    "agentId": agent.id,
                    "domain": agent.domain,
                    "cycleCount": agent.cycle_count,
                    "currentCycleId": latest.id if latest else None,
                    "lastActivity": _iso(agent.last_cycle_at or agent.updated_at),
                    "llmStatus": llm_status,
                    "agentStatus": agent.status,
                },
                "pipeline": self._pipeline_nodes(meta, latest.status if latest else None),
                "activity": self._activity(obs, meta),
                "posts": [self._post_card(p, evaluation if i == 0 else None) for i, p in enumerate(posts[:20])],
                "memory": self._memory_cards(memories),
                "intelligence": self._intelligence(intelligence, adaptive, topics),
                "decision": self._decision(decision, obs),
                "evaluation": self._evaluation(evaluation),
                "learning": {
                    "adaptive": {
                        "status": adaptive.get("status"),
                        "overusedTopics": list(adaptive.get("overused_topics") or [])[:5],
                        "undercoveredTopics": list(adaptive.get("undercovered_topics") or [])[:5],
                    },
                    "controlled": {
                        "status": learning.get("status"),
                        "strategies": list(learning.get("active_strategies") or [])[:5],
                        "recommendations": list(learning.get("recommendations") or [])[:5],
                    },
                },
                "publication": {
                    "status": publication.get("status"),
                    "reason": publication.get("reason"),
                    "why": obs.get("why") or {},
                },
                "latestRun": {
                    "id": latest.id if latest else None,
                    "status": latest.status if latest else None,
                    "stage": latest.stage if latest else None,
                    "startedAt": _iso(latest.started_at) if latest else None,
                    "finishedAt": _iso(latest.finished_at) if latest else None,
                },
            }
        )

    @staticmethod
    def _live_status(agent_status: str, run_status: str | None) -> str:
        if run_status == "running":
            return "PROCESSING"
        if agent_status in {"running", "active"}:
            return "ONLINE"
        if agent_status in {"paused", "stopped"}:
            return "IDLE"
        return "ONLINE" if run_status in {"success", "skipped"} else "IDLE"

    @staticmethod
    def _llm_status(
        api_key: str | None,
        meta: dict[str, Any],
        run_status: str | None,
        run_error: str | None,
        *,
        probed_status: str | None = None,
    ) -> str:
        """Truthful LLM badge. READY only after a successful real probe."""
        llm_meta = meta.get("llm") if isinstance(meta.get("llm"), dict) else {}
        obs = meta.get("observability") if isinstance(meta.get("observability"), dict) else {}
        cycle_reason = str(
            llm_meta.get("reason")
            or obs.get("cycle_reason")
            or obs.get("final_reason")
            or run_error
            or ""
        ).lower()
        llm_meta_status = str(llm_meta.get("status") or "").lower()
        probed = (probed_status or "").strip().lower()

        if llm_meta_status == "authentication_failed" or "auth" in cycle_reason:
            return "authentication_failed"
        if probed == "authentication_failed":
            return "authentication_failed"
        if llm_meta_status == "unstable" or "llm_unstable" in cycle_reason:
            return "unstable"
        if probed == "unstable":
            return "unstable"
        if not api_key:
            return "unavailable"
        if llm_meta_status == "unavailable" or "llm_unavailable" in cycle_reason:
            return "unavailable"
        if run_status == "failed" and "llm" in cycle_reason:
            return "unstable"
        # Key present is not enough — require probe success for READY.
        if probed == "ready":
            return "ready"
        if probed in {"unavailable", ""}:
            return "unavailable"
        return "unavailable"

    def _pipeline_nodes(
        self,
        meta: dict[str, Any],
        run_status: str | None,
    ) -> list[dict[str, Any]]:
        obs = meta.get("observability") if isinstance(meta.get("observability"), dict) else {}
        events = list(obs.get("events") or [])
        decision = meta.get("decision") if isinstance(meta.get("decision"), dict) else {}
        trace = list(decision.get("trace") or [])
        gates = {str(item.get("gate")): item for item in trace if isinstance(item, dict)}
        cycle_reason = str(obs.get("cycle_reason") or obs.get("final_reason") or "").lower()
        llm_blocked = cycle_reason in {"llm_unavailable", "llm_unstable"}

        # Stages that require LLM (or depend on LLM outputs) after research.
        llm_dependent = {
            "candidate",
            "selection",
            "debate",
            "memory",
            "consistency",
            "intelligence",
            "adaptive",
            "learning",
            "generation",
            "publishing",
            "evaluation",
        }
        ai_filter_meta = (
            meta.get("ai_security_filter")
            if isinstance(meta.get("ai_security_filter"), dict)
            else {}
        )

        stage_hits: dict[str, dict[str, Any]] = {}
        for event in events:
            if not isinstance(event, dict):
                continue
            stage = str(event.get("stage") or "")
            stage_hits[stage] = event

        nodes: list[dict[str, Any]] = []
        for key, label in PIPELINE_STAGES:
            event = stage_hits.get(key)
            gate = gates.get(key) or gates.get(
                "learning" if key == "learning" else key
            )
            if key == "ai_security":
                if event:
                    estatus = str(event.get("status") or "")
                    status = (
                        "done"
                        if estatus in {"ok", "info"}
                        else ("skipped" if estatus == "skipped" else "blocked")
                    )
                    detail = {
                        "action": event.get("action"),
                        "reason": event.get("reason"),
                        "eventType": event.get("event_type"),
                    }
                elif ai_filter_meta:
                    accepted = int(ai_filter_meta.get("accepted_count") or 0)
                    status = "done" if accepted > 0 else "skipped"
                    detail = {
                        "acceptedCount": accepted,
                        "rejectedCount": ai_filter_meta.get("rejected_count"),
                        "reason": ai_filter_meta.get("reason"),
                    }
                elif meta.get("research_count") and not llm_blocked:
                    # Filter disabled or not recorded — treat as passed-through.
                    status = "done"
                    detail = {"status": "passthrough"}
                else:
                    status = "waiting"
                    detail = {}
            elif key == "decision":
                action = str(decision.get("final_action") or "")
                if action == "proceed" and not llm_blocked:
                    status = "done"
                elif action in {"skip", "stop"} or llm_blocked:
                    status = "blocked" if llm_blocked or action == "stop" else "skipped"
                elif action:
                    status = "done"
                else:
                    status = "waiting"
                detail = {
                    "action": decision.get("final_action"),
                    "reason": decision.get("reason"),
                }
            elif key == "selection":
                selected = meta.get("selected_ids") or []
                if selected:
                    status = "done"
                elif llm_blocked or meta:
                    status = "skipped"
                else:
                    status = "waiting"
                detail = {"selectedCount": len(selected) if isinstance(selected, list) else 0}
            elif event:
                estatus = str(event.get("status") or "")
                if estatus in {"ok", "info"}:
                    status = "done"
                elif estatus in {"blocked", "failed"}:
                    status = "blocked"
                elif estatus == "skipped":
                    status = "skipped"
                else:
                    status = "done"
                detail = {
                    "action": event.get("action"),
                    "reason": event.get("reason"),
                    "eventType": event.get("event_type"),
                }
            elif llm_blocked and key in llm_dependent:
                status = "skipped"
                detail = {"reason": cycle_reason}
            elif gate:
                action = str(gate.get("action") or "")
                status = (
                    "done"
                    if action == "proceed"
                    else ("blocked" if action in {"skip", "stop"} else "waiting")
                )
                detail = {
                    "action": gate.get("action"),
                    "reason": gate.get("reason"),
                    "confidence": gate.get("confidence"),
                }
            elif run_status == "running":
                status = "waiting"
                detail = {}
            elif meta:
                # Cycle finished with partial coverage — mark upstream as done heuristically.
                status = "done" if self._stage_implied_done(key, meta) else "waiting"
                detail = {}
            else:
                status = "waiting"
                detail = {}

            if run_status == "running" and status == "waiting" and not nodes:
                status = "processing"
            elif (
                run_status == "running"
                and status == "waiting"
                and nodes
                and nodes[-1]["status"] == "done"
            ):
                # First waiting after done becomes processing.
                if not any(n["status"] == "processing" for n in nodes):
                    status = "processing"

            nodes.append(
                {
                    "id": key,
                    "label": label,
                    "status": status,
                    "detail": detail,
                }
            )
        return nodes

    @staticmethod
    def _stage_implied_done(key: str, meta: dict[str, Any]) -> bool:
        ai_filter = meta.get("ai_security_filter") if isinstance(meta.get("ai_security_filter"), dict) else {}
        mapping = {
            "research": bool(meta.get("research_count")),
            "ai_security": bool(ai_filter.get("accepted_count"))
            or ai_filter.get("status") in {"ok", "disabled"},
            "candidate": bool(meta.get("candidate_count")),
            "selection": bool(meta.get("selected_ids")),
            "debate": bool(meta.get("debated_ids")),
            "memory": bool(meta.get("memory")),
            "consistency": bool(meta.get("consistency")),
            "intelligence": bool(meta.get("intelligence")),
            "adaptive": bool(meta.get("adaptive_learning")),
            "learning": bool(meta.get("controlled_learning")),
            "generation": (meta.get("final_generation") or {}).get("status")
            in {"success", "skipped", "failed"},
            "publishing": bool(meta.get("publication")),
            "evaluation": bool(meta.get("evaluation")),
            "decision": bool(meta.get("decision")),
        }
        return bool(mapping.get(key))

    def _activity(self, obs: dict[str, Any], meta: dict[str, Any]) -> list[dict[str, Any]]:
        events = list(obs.get("events") or [])
        out: list[dict[str, Any]] = []
        for event in events[-30:]:
            if not isinstance(event, dict):
                continue
            reason = str(event.get("reason") or event.get("event_type") or "event")
            out.append(
                {
                    "id": event.get("event_id"),
                    "stage": event.get("stage"),
                    "type": event.get("event_type"),
                    "text": reason[:180],
                    "status": event.get("status"),
                    "order": event.get("order"),
                    "timestamp": event.get("timestamp"),
                }
            )
        if not out and meta:
            if meta.get("research_count"):
                out.append(
                    {
                        "text": f"Research found {meta.get('research_count')} relevant signals",
                        "stage": "research",
                        "type": "research_completed",
                    }
                )
            if meta.get("candidate_count"):
                out.append(
                    {
                        "text": f"{meta.get('candidate_count')} candidate topics generated",
                        "stage": "candidate",
                        "type": "candidate_generated",
                    }
                )
            pub = meta.get("publication") or {}
            if pub.get("status") == "published":
                out.append(
                    {
                        "text": "Post published",
                        "stage": "publishing",
                        "type": "publishing_completed",
                    }
                )
        return list(reversed(out))[:40]

    def _post_card(self, post: Any, evaluation: dict[str, Any] | None) -> dict[str, Any]:
        try:
            sources = json.loads(post.sources_json or "[]")
        except Exception:
            sources = []
        if not isinstance(sources, list):
            sources = []
        domains = [_domain(str(u)) for u in sources if u][:6]
        quality = None
        label = None
        if evaluation and evaluation.get("status") == "success":
            score = evaluation.get("overall_score")
            if isinstance(score, (int, float)):
                quality = int(round(float(score) * 100))
            label = evaluation.get("quality_class")
        return {
            "id": post.id,
            "text": post.text,
            "rationale": post.rationale,
            "topic": post.topic,
            "createdAt": _iso(post.created_at),
            "sources": [str(s) for s in sources[:8]],
            "domains": domains,
            "quality": quality,
            "qualityLabel": label,
        }

    def _memory_cards(self, memories: list[Any]) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for item in memories[:24]:
            value = _load_json(item.value_json)
            supporting = value.get("supporting_source_urls") or value.get("supporting") or []
            contradicting = (
                value.get("contradicting_source_urls") or value.get("contradicting") or []
            )
            if not isinstance(supporting, list):
                supporting = []
            if not isinstance(contradicting, list):
                contradicting = []
            confidence = value.get("confidence")
            if confidence is None:
                confidence = item.salience
            try:
                confidence_f = float(confidence)
            except Exception:
                confidence_f = float(item.salience or 0.0)
            cards.append(
                {
                    "id": item.id,
                    "kind": item.kind,
                    "key": item.key or value.get("key") or "memory",
                    "text": str(value.get("text") or value.get("value") or "")[:240],
                    "stance": value.get("stance"),
                    "confidence": max(0.0, min(1.0, confidence_f)),
                    "supporting": len(supporting),
                    "contradicting": len(contradicting),
                    "updatedAt": _iso(item.updated_at),
                }
            )
        return cards

    def _intelligence(
        self,
        intelligence: dict[str, Any],
        adaptive: dict[str, Any],
        topics: list[Any],
    ) -> dict[str, Any]:
        contradiction = intelligence.get("contradiction") or {}
        story_counts = {"new": 0, "continuing": 0, "revived": 0, "uncertain": 0}
        domains: dict[str, int] = {}
        for topic in topics[:40]:
            scores = _load_json(topic.scores_json)
            meta = scores.get("meta") if isinstance(scores.get("meta"), dict) else {}
            story = str(meta.get("story_type") or "").lower()
            if "continu" in story:
                story_counts["continuing"] += 1
            elif "reviv" in story:
                story_counts["revived"] += 1
            elif "uncertain" in story:
                story_counts["uncertain"] += 1
            elif story:
                story_counts["new"] += 1
            for url in list(scores.get("source_urls") or [])[:5]:
                if isinstance(url, str) and url:
                    d = _domain(url)
                    domains[d] = domains.get(d, 0) + 1

        return {
            "sourceDiversity": {
                "topicsAnnotated": intelligence.get("topics_annotated") or 0,
                "domains": [
                    {"domain": k, "count": v}
                    for k, v in sorted(domains.items(), key=lambda kv: -kv[1])[:8]
                ],
            },
            "storyTracking": story_counts,
            "contradictions": {
                "detected": bool(contradiction.get("detected")),
                "summary": (
                    "Contradiction signal present"
                    if contradiction.get("detected")
                    else "No contradictions detected."
                ),
                "memoryKey": contradiction.get("memory_key"),
            },
            "adaptiveSignals": {
                "overused": list(adaptive.get("overused_topics") or [])[:5],
                "undercovered": list(adaptive.get("undercovered_topics") or [])[:5],
                "status": adaptive.get("status"),
            },
        }

    def _decision(
        self,
        decision: dict[str, Any],
        obs: dict[str, Any],
    ) -> dict[str, Any]:
        gates = []
        for item in list(decision.get("trace") or []):
            if not isinstance(item, dict):
                continue
            gates.append(
                {
                    "gate": item.get("gate"),
                    "action": item.get("action"),
                    "status": (
                        "passed"
                        if item.get("action") == "proceed"
                        else "blocked"
                        if item.get("action") in {"skip", "stop"}
                        else str(item.get("action"))
                    ),
                    "reason": item.get("reason"),
                    "confidence": item.get("confidence"),
                    "evidence": item.get("evidence") or {},
                }
            )
        return {
            "finalAction": decision.get("final_action"),
            "status": decision.get("status"),
            "gatesPassed": decision.get("gates_passed"),
            "gatesBlocked": decision.get("gates_blocked"),
            "gates": gates,
            "why": obs.get("why") or {},
        }

    def _evaluation(self, evaluation: dict[str, Any]) -> dict[str, Any]:
        dims = evaluation.get("dimensions") or evaluation.get("scores") or {}
        if not isinstance(dims, dict):
            dims = {}
        overall = evaluation.get("overall_score")
        quality = None
        if isinstance(overall, (int, float)):
            quality = int(round(float(overall) * 100))
        feedback = evaluation.get("quality_feedback") or {}
        improve: list[str] = []
        keep: list[str] = []
        if isinstance(feedback, dict):
            actions = feedback.get("actions") or {}
            if isinstance(actions, dict):
                for key, action in actions.items():
                    label = str(key).replace("_", " ")
                    if action == "improve":
                        improve.append(label)
                    elif action == "maintain":
                        keep.append(label)
        for item in list(evaluation.get("improvement_actions") or [])[:5]:
            improve.append(str(item))
        for item in list(evaluation.get("strengths") or [])[:5]:
            keep.append(str(item))
        return {
            "status": evaluation.get("status"),
            "score": quality,
            "label": evaluation.get("quality_class"),
            "dimensions": {
                "evidence": dims.get("evidence_quality"),
                "relevance": dims.get("relevance_score"),
                "novelty": dims.get("novelty_score"),
                "reasoning": dims.get("reasoning_quality"),
                "sources": dims.get("source_diversity_score"),
                "consistency": dims.get("consistency_score"),
            },
            "improve": improve[:6],
            "keep": keep[:6],
        }
