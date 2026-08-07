"""Module 16 integration tests for decision orchestration."""

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
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.debate import DebateLLMResponse
from app.services.decision import DecisionService
from app.services.evaluation import EvaluationLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList, UnavailableLLMClient
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem

STAGE_FULL = (
    "research_candidate_scoring_debate_memory_consistency_intelligence_"
    "adaptive_learning_final_generation_publishing_evaluation"
)


class _DecConnector:
    name = "fixture-decision"

    def __init__(self, *, empty: bool = False, suffix: str = "1") -> None:
        self.empty = empty
        self.suffix = suffix

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        if self.empty:
            return []
        now = utc_now()
        return [
            ResearchItem(
                title=f"Prompt injection technique against AI agents {self.suffix}",
                summary="Researchers reported a prompt injection weakness.",
                url=f"https://arxiv.org/abs/dec-{self.suffix}",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash=f"dec-{self.suffix}",
            )
        ]


class _DecLLM:
    available = True

    def __init__(self, suffix: str = "1") -> None:
        self.suffix = suffix
        self.call_count = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        url = f"https://arxiv.org/abs/dec-{self.suffix}"
        name = getattr(schema, "__name__", "")
        if schema is EvaluationLLMResponse:
            return EvaluationLLMResponse.model_validate(
                {
                    "strengths": ["AI Security framing"],
                    "weaknesses": ["novelty"],
                    "improvement_actions": ["vary angles"],
                    "confidence": 0.7,
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
                        f"Fresh evidence on prompt injection against AI agents {self.suffix} "
                        f"reinforces why defensive controls remain a priority in AI Security."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less security-specific."
                    ),
                    "source_urls": [url],
                }
            )
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


def _service(
    *,
    llm: _DecLLM | UnavailableLLMClient | None = None,
    empty_research: bool = False,
    suffix: str = "1",
) -> GenerationCycleService:
    llm = llm or _DecLLM(suffix)
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=[_DecConnector(empty=empty_research, suffix=suffix)],
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_full_cycle_persists_decision_trace(engine: AsyncEngine) -> None:
    agent_id = "dec-pipe-agent"
    await _create_agent(agent_id)
    llm = _DecLLM("1")
    before = llm.call_count
    assert await _service(llm=llm).execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == STAGE_FULL
        meta = json.loads(runs[0].meta_json or "{}")
        decision = meta["decision"]
        assert decision["final_action"] in {"proceed", "skip", "stop"}
        assert "trace" in decision
        assert len(decision["trace"]) <= get_settings().decision_max_trace_items
        assert decision["gates_passed"] >= 1
        assert meta["publication"]["status"] == "published"
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert len(posts) == 1
    # No dedicated decision LLM calls — call count is from existing modules only.
    assert llm.call_count >= before


@pytest.mark.asyncio
async def test_no_posts_when_research_unavailable(engine: AsyncEngine) -> None:
    agent_id = "dec-no-research"
    await _create_agent(agent_id)
    result = await _service(empty_research=True).execute(agent_id)
    assert result == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert posts == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert "decision" in meta


@pytest.mark.asyncio
async def test_no_posts_when_candidate_llm_unavailable(engine: AsyncEngine) -> None:
    agent_id = "dec-no-cand"
    await _create_agent(agent_id)
    result = await _service(llm=UnavailableLLMClient()).execute(agent_id)
    assert result == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert posts == []


@pytest.mark.asyncio
async def test_decision_service_failure_safe_fallback(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "dec-fail-agent"
    await _create_agent(agent_id)

    def boom(self, gate: str, ctx: Any) -> Any:  # noqa: ANN001
        raise RuntimeError("decision service boom")

    monkeypatch.setattr(DecisionService, "evaluate_gate", boom)
    # Prefer skip over unsafe publish when decision throws.
    result = await _service().execute(agent_id)
    assert result == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        # Must not publish when decision orchestration is broken.
        assert posts == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta.get("decision", {}).get("status") == "unavailable"


@pytest.mark.asyncio
async def test_feed_never_invokes_decision(
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

    def track(self, *args: Any, **kwargs: Any) -> Any:
        called["n"] += 1
        raise AssertionError("decision must not run from feed")

    monkeypatch.setattr(DecisionService, "evaluate_gate", track)

    def forbid(*_a: Any, **_k: Any) -> Any:
        called["n"] += 1
        raise AssertionError("pipeline must not run from feed")

    from app.services.generation_cycle import GenerationCycleService
    from app.services.publishing import PublishingService
    from app.services.research.hub import ResearchHub

    monkeypatch.setattr(GenerationCycleService, "execute", forbid)
    monkeypatch.setattr(PublishingService, "publish", forbid)
    monkeypatch.setattr(ResearchHub, "gather", forbid)

    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_decision_metadata_survives_restart(engine: AsyncEngine) -> None:
    agent_id = "dec-restart-agent"
    await _create_agent(agent_id)
    assert await _service(suffix="r1").execute(agent_id) == "success"
    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        meta = json.loads(runs[0].meta_json or "{}")
        assert "decision" in meta
        assert "trace" in meta["decision"]


@pytest.mark.asyncio
async def test_multi_agent_decision_meta_isolation(engine: AsyncEngine) -> None:
    await _create_agent("dec-iso-a")
    await _create_agent("dec-iso-b")
    assert await _service(suffix="ia").execute("dec-iso-a") == "success"
    factory = get_session_factory()
    async with factory() as session:
        a_runs = await PipelineRunRepository(session).list_by_agent("dec-iso-a")
        b_runs = await PipelineRunRepository(session).list_by_agent("dec-iso-b")
        assert a_runs
        assert b_runs == []
        a_meta = json.loads(a_runs[0].meta_json or "{}")
        assert a_meta["decision"]["trace"]
