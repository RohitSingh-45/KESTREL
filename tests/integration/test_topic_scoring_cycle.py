"""Integration tests for Module 5 topic funnel scoring."""

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
from app.main import create_app
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.generation_cycle import GenerationCycleService
from app.services.contradictions.detector import ContradictionLLMResponse
from app.services.debate import DebateLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from tests.conftest import _cleanup_test_db


class _MultiFixtureConnector:
    name = "fixture-multi"

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Critical LLM prompt injection CVE",
                summary="Exploit path against AI agents.",
                url="https://arxiv.org/abs/sec-1",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="h1",
            ),
            ResearchItem(
                title="Model poisoning via tainted fine-tuning data",
                summary="Data poisoning attack against LLM fine-tuning pipelines.",
                url="https://arxiv.org/abs/sec-2",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash="h2",
            ),
            ResearchItem(
                title="Jailbreak defenses for enterprise LLMs",
                summary="Security controls and monitoring for jailbreak attacks.",
                url="https://www.cisa.gov/ai-1",
                source_name="CISA",
                published_at=now,
                discovered_at=now,
                content_hash="h3",
            ),
            ResearchItem(
                title="Training data extraction and LLM data leakage",
                summary="Membership inference and sensitive data leakage from language models.",
                url="https://example.com/leakage",
                source_name="research",
                published_at=now,
                discovered_at=now,
                content_hash="h4",
            ),
        ]


class _MultiMockLLM:
    available = True
    call_count = 0

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        if schema is DebateLLMResponse:
            # Use the first allowed-looking URL from the prompt when possible.
            user_prompt = str(kwargs.get("user_prompt") or "")
            url = "https://arxiv.org/abs/sec-1"
            if "https://www.cisa.gov/ai-1" in user_prompt:
                url = "https://www.cisa.gov/ai-1"
            elif "https://arxiv.org/abs/sec-2" in user_prompt:
                url = "https://arxiv.org/abs/sec-2"
            elif "https://example.com/leakage" in user_prompt:
                url = "https://example.com/leakage"
            return DebateLLMResponse.model_validate(
                {
                    "for_argument": (
                        "Topic has clear AI Security relevance and timely evidence "
                        "from the supplied research that supports discussion."
                    ),
                    "against_argument": (
                        "Source depth may be limited, novelty versus prior Kestrel "
                        "topics is uncertain, and practical impact could be overstated "
                        "relative to stronger candidates."
                    ),
                    "evidence_points": ["Research item supports the candidate claim."],
                    "weaknesses": ["Summary length limits certainty."],
                    "decision": "proceed",
                    "confidence": 0.84,
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
                            "value": "Prompt injection is a recurring AI-agent concern.",
                            "confidence": 0.8,
                            "relation": "supporting",
                            "source_urls": ["https://arxiv.org/abs/sec-1"],
                        }
                    ]
                }
            )
        if schema is ContradictionLLMResponse:
            return ContradictionLLMResponse.model_validate({"contradictions": []})
        if schema is GeneratedPost:
            user_prompt = str(kwargs.get("user_prompt") or "")
            url = "https://arxiv.org/abs/sec-1"
            for candidate in (
                "https://www.cisa.gov/ai-1",
                "https://arxiv.org/abs/sec-2",
                "https://example.com/leakage",
                "https://arxiv.org/abs/sec-1",
            ):
                if candidate in user_prompt:
                    url = candidate
                    break
            return GeneratedPost.model_validate(
                {
                    "text": (
                        "A critical AI-security path in LLM agents shows why "
                        "defensive monitoring remains central to AI Security practice."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance, recent evidence, and stronger "
                        "novelty than lower-ranked alternatives with weaker security fit."
                    ),
                    "source_urls": [url],
                }
            )
        return CandidateTopicList.model_validate(
            {
                "candidates": [
                    {
                        "title": "Critical prompt injection vulnerability in AI agents",
                        "angle": "Exploitability",
                        "why_now": "CVE-style disclosure",
                        "relevance_to_persona": "Core AI Security",
                        "source_urls": ["https://arxiv.org/abs/sec-1"],
                        "confidence": 0.9,
                    },
                    {
                        "title": "Enterprise jailbreak defense guidance",
                        "angle": "Mitigations",
                        "why_now": "New defensive guidance",
                        "relevance_to_persona": "AI Security operations",
                        "source_urls": ["https://www.cisa.gov/ai-1"],
                        "confidence": 0.85,
                    },
                    {
                        "title": "Model poisoning in fine-tuning supply paths",
                        "angle": "Training integrity",
                        "why_now": "Poisoned dataset advisory",
                        "relevance_to_persona": "AI Security model risk",
                        "source_urls": ["https://arxiv.org/abs/sec-2"],
                        "confidence": 0.55,
                        "ai_security_domain": "model_poisoning",
                    },
                    {
                        "title": "LLM training data leakage risks",
                        "angle": "Privacy",
                        "why_now": "Extraction research",
                        "relevance_to_persona": "AI Security privacy",
                        "source_urls": ["https://example.com/leakage"],
                        "confidence": 0.45,
                        "ai_security_domain": "data_leakage",
                    },
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


def _service() -> GenerationCycleService:
    llm = _MultiMockLLM()
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_MultiFixtureConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_scoring_cycle_selects_top_k_and_rejects_rest(engine: AsyncEngine) -> None:
    agent_id = "score-cycle-agent"
    await _create_agent(agent_id)
    result = await _service().execute(agent_id)
    assert result == "success"

    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].stage == "research_candidate_scoring_debate_memory_consistency_intelligence_adaptive_learning_final_generation_publishing_evaluation"
        assert runs[0].status == "success"

        selected = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        rejected = await TopicRepository(session).list_by_agent(agent_id, status="rejected")
        assert len(selected) == 3
        assert len(rejected) == 1

        for topic in selected + rejected:
            payload = json.loads(topic.scores_json or "{}")
            assert payload["version"] == 1
            assert "final_score" in payload
            assert "rank" in payload
            assert payload["decision"] in {"selected", "rejected"}
            assert set(payload["scores"]) == {
                "recency",
                "persona_fit",
                "importance",
                "novelty",
                "source_quality",
                "evidence_quality",
                "ai_security_relevance",
            }
            assert payload.get("ai_security_domain") in {
                "prompt_injection",
                "jailbreak",
                "data_leakage",
                "model_poisoning",
                "ai_vulnerability",
                "ai_supply_chain",
            }

        for topic in selected:
            debate = json.loads(topic.debate_json or "{}")
            assert debate["version"] == 1
            assert debate["for_argument"]
            assert debate["against_argument"]
            assert debate["decision"] in {"proceed", "reject", "uncertain"}

        for topic in rejected:
            assert topic.debate_json is None

        rejected_payload = json.loads(rejected[0].scores_json or "{}")
        assert rejected_payload["decision"] == "rejected"
        assert rejected_payload["rank"] == 4

        assert len(await PostRepository(session).list_by_agent_newest_first(agent_id)) == 1


@pytest.mark.asyncio
async def test_novelty_decreases_on_repeated_topic(engine: AsyncEngine) -> None:
    agent_id = "novelty-agent"
    await _create_agent(agent_id)
    service = _service()
    await service.execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        first = await TopicRepository(session).list_by_agent(agent_id)
        first_by_title = {
            t.title: json.loads(t.scores_json or "{}")["scores"]["novelty"] for t in first
        }

    await service.execute(agent_id)
    async with factory() as session:
        second = await TopicRepository(session).list_by_agent(agent_id)
        # Newest batch should have reduced novelty for repeated titles.
        newest = sorted(second, key=lambda t: t.created_at, reverse=True)[:4]
        for topic in newest:
            payload = json.loads(topic.scores_json or "{}")
            if topic.title in first_by_title:
                assert payload["scores"]["novelty"] < first_by_title[topic.title]


@pytest.mark.asyncio
async def test_feed_has_post_after_scoring_cycle(
    client: AsyncClient,
    engine: AsyncEngine,
) -> None:
    init = await client.post(
        "/api/agent/init",
        json={"persona": {"name": "Kestrel", "domain": "AI Security"}},
    )
    agent_id = init.json()["agentId"]
    await _service().execute(agent_id)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert len(feed.json()["posts"]) == 1


@pytest.mark.asyncio
async def test_scored_topics_survive_restart(engine: AsyncEngine) -> None:
    agent_id = "restart-score-agent"
    await _create_agent(agent_id)
    await _service().execute(agent_id)

    factory = get_session_factory()
    async with factory() as session:
        before = await TopicRepository(session).count_by_agent(agent_id)
        assert before >= 4

    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        after = await TopicRepository(session).count_by_agent(agent_id)
        assert after == before
        selected = await TopicRepository(session).list_by_agent(agent_id, status="selected")
        assert len(selected) == 3
        assert all(t.debate_json for t in selected)
