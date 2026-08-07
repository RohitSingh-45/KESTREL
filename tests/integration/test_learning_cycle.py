"""Module 15 integration tests for controlled learning in the generation cycle."""

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
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.evaluation import EvaluationLLMResponse, EvaluationStore
from app.services.evaluation.models import PostEvaluation
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.learning import ControlledLearningService, LearningStore
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem

STAGE_FULL = (
    "research_candidate_scoring_debate_memory_consistency_intelligence_"
    "adaptive_learning_final_generation_publishing_evaluation"
)


class _LearnConnector:
    name = "fixture-learn"

    def __init__(self, suffix: str = "1") -> None:
        self.suffix = suffix

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title=f"Prompt injection technique against AI agents {self.suffix}",
                summary="Researchers reported a prompt injection weakness.",
                url=f"https://arxiv.org/abs/learn-{self.suffix}",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash=f"learn-{self.suffix}",
            )
        ]


class _LearnLLM:
    available = True

    def __init__(self, suffix: str = "1") -> None:
        self.suffix = suffix
        self.reflection_calls = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        schema = kwargs.get("schema")
        url = f"https://arxiv.org/abs/learn-{self.suffix}"
        if schema is EvaluationLLMResponse:
            self.reflection_calls += 1
            return EvaluationLLMResponse.model_validate(
                {
                    "strengths": ["AI Security framing"],
                    "weaknesses": ["novelty could improve"],
                    "improvement_actions": ["vary angles"],
                    "confidence": 0.75,
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
                        f"Fresh evidence on prompt injection against AI agents in cycle "
                        f"{self.suffix} reinforces why defensive controls remain a priority "
                        f"in AI Security."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less security-specific."
                    ),
                    "source_urls": [url],
                }
            )
        # ContradictionLLMResponse and other schemas: return empty-safe structure when possible.
        name = getattr(schema, "__name__", "")
        if name == "ContradictionLLMResponse":
            return schema.model_validate({"contradictions": []})
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": (
                            f"Prompt injection attacks against autonomous AI agents "
                            f"{self.suffix}"
                        ),
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


def _service(llm: _LearnLLM | None = None) -> GenerationCycleService:
    llm = llm or _LearnLLM()
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=[_LearnConnector(llm.suffix)],
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_pipeline_runs_controlled_learning(engine: AsyncEngine) -> None:
    agent_id = "learn-pipe-agent"
    await _create_agent(agent_id)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == STAGE_FULL
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["controlled_learning"]["status"] == "success"
        assert meta["controlled_learning"]["llm_calls"] == 0
        assert meta["summary"]["controlled_learning"] == "success"
        assert meta["publication"]["status"] == "published"
        assert meta["evaluation"]["status"] == "success"


@pytest.mark.asyncio
async def test_learning_failure_does_not_kill_cycle(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "learn-fail-agent"
    await _create_agent(agent_id)

    async def boom(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("learning boom")

    monkeypatch.setattr(ControlledLearningService, "run", boom)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta["controlled_learning"]["status"] == "failed"
        assert meta["publication"]["status"] == "published"


@pytest.mark.asyncio
async def test_learning_does_not_create_posts(engine: AsyncEngine) -> None:
    agent_id = "learn-nopost-agent"
    await _create_agent(agent_id)
    assert await _service().execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1


@pytest.mark.asyncio
async def test_feed_never_invokes_learning(
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
        raise AssertionError("learning must not run from feed")

    monkeypatch.setattr(ControlledLearningService, "run", track)
    monkeypatch.setattr(ControlledLearningService, "observe_evaluation", track)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_multi_agent_learning_isolation(engine: AsyncEngine) -> None:
    await _create_agent("learn-iso-a")
    await _create_agent("learn-iso-b")
    factory = get_session_factory()
    async with factory() as session:
        for i in range(3):
            await EvaluationStore(session).save_evaluation(
                PostEvaluation(
                    post_id=f"iso-a-{i}",
                    agent_id="learn-iso-a",
                    overall_score=0.8,
                    quality_class="good",
                    evidence_quality=0.9,
                    novelty_score=0.4,
                    source_diversity_score=0.5,
                    reasoning_quality=0.8,
                    rationale_quality=0.8,
                    relevance_score=0.9,
                    consistency_score=0.9,
                    feedback={"novelty": "improve"},
                    status="success",
                )
            )
        await session.commit()

    async with factory() as session:
        ctx = await ControlledLearningService(
            min_observations=3, min_confidence=0.5
        ).run(session, agent_id="learn-iso-a")
        await session.commit()
        assert ctx.status == "success"
        a = await LearningStore(session).list_signals("learn-iso-a")
        b = await LearningStore(session).list_signals("learn-iso-b")
        assert a
        assert b == []


@pytest.mark.asyncio
async def test_second_cycle_uses_prior_learning(engine: AsyncEngine) -> None:
    agent_id = "learn-second-agent"
    await _create_agent(agent_id)
    assert await _service(_LearnLLM("1")).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        # Seed enough evaluations so learning can activate strategies.
        for i in range(4):
            await EvaluationStore(session).save_evaluation(
                PostEvaluation(
                    post_id=f"seed-{i}",
                    agent_id=agent_id,
                    overall_score=0.84,
                    quality_class="good",
                    evidence_quality=0.92,
                    novelty_score=0.42,
                    source_diversity_score=0.55,
                    reasoning_quality=0.8,
                    rationale_quality=0.8,
                    relevance_score=0.9,
                    consistency_score=0.9,
                    feedback={"novelty": "improve", "evidence_discipline": "maintain"},
                    status="success",
                )
            )
        await session.commit()

    assert await _service(_LearnLLM("2")).execute(agent_id) == "success"
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta["controlled_learning"]["status"] == "success"
        signals = await LearningStore(session).list_signals(agent_id)
        assert signals
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 2


@pytest.mark.asyncio
async def test_learning_order_before_final(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "learn-order-agent"
    await _create_agent(agent_id)
    order: list[str] = []
    original_run = ControlledLearningService.run

    async def track_learn(self, *args: Any, **kwargs: Any) -> Any:
        order.append("learning")
        return await original_run(self, *args, **kwargs)

    from app.services.final_post import FinalPostGenerator

    original_generate = FinalPostGenerator.generate

    async def track_final(self, *args: Any, **kwargs: Any) -> Any:
        order.append("final")
        return await original_generate(self, *args, **kwargs)

    monkeypatch.setattr(ControlledLearningService, "run", track_learn)
    monkeypatch.setattr(FinalPostGenerator, "generate", track_final)
    assert await _service().execute(agent_id) == "success"
    assert "learning" in order
    assert "final" in order
    assert order.index("learning") < order.index("final")
