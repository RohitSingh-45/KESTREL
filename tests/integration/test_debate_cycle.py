"""Integration tests for Module 6 self-debate in the generation cycle."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.contradictions.detector import ContradictionLLMResponse
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _ThreeConnector:
    name = "fixture-three"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Prompt injection CVE",
                summary="Critical agent exploit.",
                url="https://arxiv.org/abs/a",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="a",
            ),
            ResearchItem(
                title="Jailbreak defenses",
                summary="Enterprise controls.",
                url="https://www.cisa.gov/b",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="b",
            ),
            ResearchItem(
                title="Model poisoning advisory",
                summary="Supply-chain risk.",
                url="https://www.nist.gov/c",
                source_name="NIST",
                published_at=now,
                discovered_at=now,
                content_hash="c",
            ),
            ResearchItem(
                title="LLM data leakage via training extraction",
                summary="Sensitive data leakage from language models.",
                url="https://example.com/d",
                source_name="research",
                published_at=now,
                discovered_at=now,
                content_hash="d",
            ),
        ]


def _candidates() -> CandidateTopicList:
    return CandidateTopicList.model_validate(
        {
            "candidates": [
                {
                    "title": "Critical prompt injection CVE in AI agents",
                    "angle": "Exploitability",
                    "why_now": "Fresh CVE-style disclosure",
                    "relevance_to_persona": "Core AI Security",
                    "source_urls": ["https://arxiv.org/abs/a"],
                    "confidence": 0.95,
                },
                {
                    "title": "Enterprise jailbreak defense guidance",
                    "angle": "Mitigations",
                    "why_now": "New defensive guidance",
                    "relevance_to_persona": "AI Security operations",
                    "source_urls": ["https://www.cisa.gov/b"],
                    "confidence": 0.9,
                },
                {
                    "title": "Model poisoning supply-chain advisory",
                    "angle": "Supply chain",
                    "why_now": "Fresh advisory",
                    "relevance_to_persona": "Model security",
                    "source_urls": ["https://www.nist.gov/c"],
                    "confidence": 0.88,
                },
                {
                    "title": "LLM training data leakage risks",
                    "angle": "Privacy",
                    "why_now": "Extraction research",
                    "relevance_to_persona": "AI Security privacy",
                    "source_urls": ["https://example.com/d"],
                    "confidence": 0.4,
                    "ai_security_domain": "data_leakage",
                },
            ]
        }
    )


def _debate_ok(url: str, decision: str = "proceed", confidence: float = 0.86) -> DebateLLMResponse:
    return DebateLLMResponse.model_validate(
        {
            "for_argument": (
                "This topic is timely and relevant to AI Security with grounded "
                "evidence from the supplied research."
            ),
            "against_argument": (
                "Evidence depth may be limited, novelty versus prior topics is "
                "uncertain, and practical impact could be overstated."
            ),
            "evidence_points": ["Source supports the core claim."],
            "weaknesses": ["Summary is brief."],
            "decision": decision,
            "confidence": confidence,
            "source_urls": [url],
        }
    )


class _DebateMockLLM:
    available = True

    def __init__(self) -> None:
        self.call_count = 0
        self.debate_calls = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            self.debate_calls += 1
            prompt = str(kwargs.get("user_prompt") or "")
            if "https://www.cisa.gov/b" in prompt:
                # Demonstrate scoring vs debate separation: reject via debate.
                return _debate_ok("https://www.cisa.gov/b", decision="reject", confidence=0.8)
            if "https://www.nist.gov/c" in prompt:
                return _debate_ok("https://www.nist.gov/c")
            return _debate_ok("https://arxiv.org/abs/a")
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate(
                {
                    "memories": [
                        {
                            "kind": "belief",
                            "key": "prompt injection",
                            "value": "Prompt injection is a significant risk for AI agents.",
                            "subject": "prompt injection",
                            "predicate": "is a significant security risk for",
                            "object": "AI agents",
                            "stance": "concerned",
                            "confidence": 0.84,
                            "relation": "supporting",
                            "source_urls": ["https://arxiv.org/abs/a"],
                        }
                    ]
                }
            )
        if schema is ContradictionLLMResponse:
            return ContradictionLLMResponse.model_validate({"contradictions": []})
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Fresh evidence on prompt injection against AI agents reinforces "
                        "why defensive controls remain a priority in AI Security."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives that were less timely or less security-specific."
                    ),
                    "source_urls": ["https://arxiv.org/abs/a"],
                }
            )
        return _candidates()


class _PartialFailLLM:
    available = True

    def __init__(self) -> None:
        self.call_count = 0
        self.debate_calls = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            self.debate_calls += 1
            prompt = str(kwargs.get("user_prompt") or "")
            # Fail the middle selected debate (rank/order dependent: 2nd call).
            if self.debate_calls == 2:
                raise TimeoutError("debate timeout")
            if "https://www.cisa.gov/b" in prompt:
                return _debate_ok("https://www.cisa.gov/b")
            if "https://www.nist.gov/c" in prompt:
                return _debate_ok("https://www.nist.gov/c")
            return _debate_ok("https://arxiv.org/abs/a")
        if schema is MemoryExtractionResponse:
            return MemoryExtractionResponse.model_validate({"memories": []})
        if schema is ContradictionLLMResponse:
            return ContradictionLLMResponse.model_validate({"contradictions": []})
        if schema is GeneratedPost:
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "Prompt injection research continues to matter for AI-agent security "
                        "operations and defensive monitoring."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance with grounded evidence, preferred "
                        "over alternatives with weaker security specificity."
                    ),
                    "source_urls": ["https://arxiv.org/abs/a"],
                }
            )
        return _candidates()


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


def _service(llm: object) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_ThreeConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_debate_pipeline_persists_and_preserves_scores(engine: AsyncEngine) -> None:
    agent_id = "debate-persist-agent"
    await _create_agent(agent_id)
    llm = _DebateMockLLM()
    result = await _service(llm).execute(agent_id)
    assert result == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        meta = json.loads(runs[0].meta_json or "{}")
        assert meta["debate_llm_calls"] == 3
        assert len(meta["debated_ids"]) == 3
        assert len(meta["rejected_ids"]) == 1
        assert meta["memory"]["status"] == "success"

        selected = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        rejected = await TopicRepository(session).list_by_agent(agent_id, status="rejected")
        assert len(selected) == 3
        assert len(rejected) == 1
        assert rejected[0].debate_json is None

        decisions = set()
        for topic in selected:
            scores = json.loads(topic.scores_json or "{}")
            debate = json.loads(topic.debate_json or "{}")
            assert scores["decision"] == "selected"
            assert debate["final_score"] == scores["final_score"]
            assert debate["rank"] == scores["rank"]
            assert debate["for_argument"]
            assert debate["against_argument"]
            assert debate["decision"] in {"proceed", "reject", "uncertain"}
            decisions.add(debate["decision"])
            # Scores remain Module 5 selection metadata (not overwritten by debate).
            assert "proceed" not in scores["decision"]

        assert "reject" in decisions or "proceed" in decisions
        assert meta["publication"]["status"] == "published"
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1

    # 1 candidate-generation + 3 debate + 1 memory extraction + up to 1 final post
    assert llm.call_count >= 5
    assert llm.debate_calls == 3


@pytest.mark.asyncio
async def test_partial_debate_failure_keeps_cycle_healthy(engine: AsyncEngine) -> None:
    agent_id = "debate-partial-agent"
    await _create_agent(agent_id)
    llm = _PartialFailLLM()
    assert await _service(llm).execute(agent_id) == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "success"
        meta = json.loads(runs[0].meta_json or "{}")
        assert len(meta["debate_errors"]) == 1

        selected = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert len(selected) == 3
        errored = 0
        ok = 0
        for topic in selected:
            debate = json.loads(topic.debate_json or "{}")
            scores = json.loads(topic.scores_json or "{}")
            assert scores["decision"] == "selected"
            if debate.get("error"):
                errored += 1
                assert debate["for_argument"] == ""
                assert debate["against_argument"] == ""
            else:
                ok += 1
                assert debate["for_argument"]
        assert ok == 2
        assert errored == 1
        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_debate_feed_returns_published_post(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    await _service(_DebateMockLLM()).execute(agent_id)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert len(feed.json()["posts"]) == 1


@pytest.mark.asyncio
async def test_debate_metadata_survives_restart(engine: AsyncEngine) -> None:
    agent_id = "debate-restart-agent"
    await _create_agent(agent_id)
    await _service(_DebateMockLLM()).execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        before_payloads = {
            t.id: (t.scores_json, t.debate_json) for t in before
        }
        assert len(before_payloads) == 3

    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        after = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert len(after) == 3
        for topic in after:
            scores_json, debate_json = before_payloads[topic.id]
            assert topic.scores_json == scores_json
            assert topic.debate_json == debate_json
            assert json.loads(topic.debate_json or "{}")["decision"] in {
                "proceed",
                "reject",
                "uncertain",
            }
