"""Module 14 integration tests for post-publish evaluation."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.session import get_session_factory
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.adaptive_learning import AdaptiveLearningService
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.evaluation import EvaluationLLMResponse, EvaluationStore, PostEvaluator
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem

STAGE_FULL = (
    "research_candidate_scoring_debate_memory_consistency_intelligence_"
    "adaptive_learning_final_generation_publishing_evaluation"
)


class _EvalConnector:
    name = "fixture-eval"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection technique against AI agents",
                summary="Researchers reported a prompt injection weakness.",
                url="https://arxiv.org/abs/eval-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="eval-1",
            )
        ]


class _EvalLLM:
    available = True

    def __init__(self) -> None:
        self.call_count = 0
        self.reflection_calls = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        url = "https://arxiv.org/abs/eval-1"
        if schema is EvaluationLLMResponse:
            self.reflection_calls += 1
            return EvaluationLLMResponse.model_validate(
                {
                    "strengths": ["Clear AI Security framing"],
                    "weaknesses": ["Could improve source diversity"],
                    "improvement_actions": ["Seek non-arXiv corroboration"],
                    "confidence": 0.8,
                }
            )
        if schema is DebateLLMResponse:
            return DebateLLMResponse.model_validate(
                {
                    "for_argument": (
                        "Topic is timely AI Security evidence with grounded sources that "
                        "support further consideration."
                    ),
                    "against_argument": (
                        "Evidence depth may be limited and practical impact could be overstated."
                    ),
                    "evidence_points": ["Source describes an AI agent security issue."],
                    "weaknesses": ["Limited operational detail."],
                    "decision": "proceed",
                    "confidence": 0.86,
                    "source_urls": [url],
                }
            )
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "theme",
                            "key": "prompt injection",
                            "value": "Prompt injection remains a recurring AI-agent concern.",
                            "confidence": 0.8,
                            "relation": "supporting",
                            "source_urls": [url],
                        }
                    ]
                }
            )
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Fresh evidence on prompt injection against AI agents reinforces "
                        "why defensive controls remain a priority in AI Security."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less security-specific."
                    ),
                    "source_urls": [url],
                }
            )
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": "Prompt injection attacks against autonomous AI agents",
                        "angle": "Agent exploitability",
                        "why_now": "Fresh disclosure",
                        "relevance_to_persona": "Core AI Security",
                        "source_urls": [url],
                        "confidence": 0.93,
                    }
                ]
            }
        )


async def _create_agent(agent_id: str) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=now,
            updated_at=now,
        )
        await session.commit()


def _service(llm: _EvalLLM | None = None) -> GenerationCycleService:
    llm = llm or _EvalLLM()
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_EvalConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_pipeline_runs_evaluation_after_publish(engine: AsyncEngine) -> None:
    agent_id = "eval-pipe-agent"
    await _create_agent(agent_id)
    llm = _EvalLLM()
    assert await _service(llm).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == STAGE_FULL
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["publication"]["status"] == "published"
        assert meta["evaluation"]["status"] == "success"
        assert meta["summary"]["evaluation"] == "success"
        assert 0.0 <= meta["evaluation"]["overall_score"] <= 1.0
        assert meta["evaluation"]["quality_class"] in {
            "excellent",
            "good",
            "acceptable",
            "weak",
            "failed",
        }
        assert llm.reflection_calls == 1
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        stored = await EvaluationStore(session).get_evaluation(agent_id, posts[0].id)
        assert stored is not None
        assert stored.feedback


@pytest.mark.asyncio
async def test_evaluation_failure_keeps_published_post(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "eval-fail-agent"
    await _create_agent(agent_id)

    async def boom(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("evaluation boom")

    monkeypatch.setattr(PostEvaluator, "evaluate", boom)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta["publication"]["status"] == "published"
        assert meta["evaluation"]["status"] == "failed"


@pytest.mark.asyncio
async def test_evaluation_does_not_create_duplicate_posts(engine: AsyncEngine) -> None:
    agent_id = "eval-nodup-agent"
    await _create_agent(agent_id)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1


@pytest.mark.asyncio
async def test_one_reflection_llm_call_maximum(engine: AsyncEngine) -> None:
    agent_id = "eval-budget-agent"
    await _create_agent(agent_id)
    llm = _EvalLLM()
    assert await _service(llm).execute(agent_id) == "success"
    assert llm.reflection_calls == 1


@pytest.mark.asyncio
async def test_feed_never_invokes_evaluation(
    client: AsyncClient,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    called = {"n": 0}

    async def track(*_a: Any, **_k: Any) -> Any:
        called["n"] += 1
        raise AssertionError("evaluation must not run from feed")

    monkeypatch.setattr(PostEvaluator, "evaluate", track)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_multi_agent_evaluation_isolation(engine: AsyncEngine) -> None:
    await _create_agent("eval-iso-a")
    await _create_agent("eval-iso-b")
    assert await _service().execute("eval-iso-a") == "success"
    factory = get_session_factory()
    async with factory() as session:
        a = await EvaluationStore(session).list_evaluations("eval-iso-a")
        b = await EvaluationStore(session).list_evaluations("eval-iso-b")
        assert a
        assert b == []


@pytest.mark.asyncio
async def test_second_cycle_adaptive_sees_quality_feedback(engine: AsyncEngine) -> None:
    agent_id = "eval-fb-agent"
    await _create_agent(agent_id)
    assert await _service().execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        topics = []
        ctx = await AdaptiveLearningService().run(
            session,
            agent_id=agent_id,
            posts=posts,
            topics=topics,
        )
        await session.commit()
        assert ctx.status == "success"
        assert isinstance(ctx.quality_feedback, dict)
        # Feedback keys are produced from the prior evaluation persist path.
        assert ctx.quality_feedback
