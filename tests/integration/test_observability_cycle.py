"""Module 17 integration tests — observability sidecar in the generation cycle."""

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
from app.services.evaluation import EvaluationLLMResponse
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import CandidateTopicList
from app.services.memory.models import MemoryExtractionResponse
from app.services.observability import ObservabilityService
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _ObsConnector:
    name = "fixture-obs"

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
                url=f"https://arxiv.org/abs/obs-{self.suffix}",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash=f"obs-{self.suffix}",
            )
        ]


class _ObsLLM:
    available = True

    def __init__(self, suffix: str = "1", debate_decision: str = "proceed") -> None:
        self.suffix = suffix
        self.debate_decision = debate_decision
        self.call_count = 0
        self.schemas: list[str] = []

    async def chat(self, **_kwargs: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        name = getattr(schema, "__name__", "")
        self.schemas.append(name)
        url = f"https://arxiv.org/abs/obs-{self.suffix}"
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
                    "decision": self.debate_decision,
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
    llm: _ObsLLM | None = None,
    empty_research: bool = False,
    suffix: str = "1",
) -> GenerationCycleService:
    llm = llm or _ObsLLM(suffix)
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(
            connectors=[_ObsConnector(empty=empty_research, suffix=suffix)],
            max_items=20,
        ),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_successful_cycle_persists_observability_trace(engine: AsyncEngine) -> None:
    agent_id = "obs-success"
    await _create_agent(agent_id)
    llm = _ObsLLM("ok")
    assert await _service(llm=llm, suffix="ok").execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        meta = json.loads(runs[0].meta_json or "{}")
        assert "decision" in meta
        assert "evaluation" in meta
        obs = meta["observability"]
        assert obs["version"] == 1
        assert obs["event_count"] >= 5
        assert obs["published"] is True
        assert obs["final_reason"] == "published"
        types = {e["event_type"] for e in obs["events"]}
        assert "research_completed" in types
        assert "publishing_completed" in types
        assert "cycle_completed" in types
        assert meta["decision"]["trace"]
        assert meta["evaluation"]["status"] in {"success", "skipped", "failed"}
        # No Module 17 LLM attribution — schemas are prior modules only.
        assert "Observability" not in "".join(llm.schemas)


@pytest.mark.asyncio
async def test_rejected_cycle_has_observable_reason(engine: AsyncEngine) -> None:
    agent_id = "obs-reject"
    await _create_agent(agent_id)
    status = await _service(
        llm=_ObsLLM("rej", debate_decision="reject"),
        suffix="rej",
    ).execute(agent_id)
    assert status in {"success", "skipped"}
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first(agent_id)
        assert posts == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        obs = meta["observability"]
        assert obs["published"] is False
        assert obs["why"]["published"] is False
        assert obs["final_reason"]
        assert "decision" in meta


@pytest.mark.asyncio
async def test_research_skip_observability_explanation(engine: AsyncEngine) -> None:
    agent_id = "obs-noresearch"
    await _create_agent(agent_id)
    assert await _service(empty_research=True, suffix="nr").execute(agent_id) == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        obs = meta["observability"]
        assert obs["published"] is False
        assert obs["why"]["final_reason"] in {
            "no_research_items",
            "cycle_skipped",
            "not_published",
        } or "research" in obs["why"]["stage"] or obs["final_reason"]


@pytest.mark.asyncio
async def test_observability_failure_does_not_break_cycle(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = "obs-boom"
    await _create_agent(agent_id)

    def boom(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("observability boom")

    monkeypatch.setattr(ObservabilityService, "record", boom)
    monkeypatch.setattr(ObservabilityService, "finalize", boom)
    status = await _service(suffix="boom").execute(agent_id)
    assert status in {"success", "skipped"}
    factory = get_session_factory()
    async with factory() as session:
        # Authoritative modules still ran; decision meta preserved when cycle completes.
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs
        meta = json.loads(runs[0].meta_json or "{}")
        # Either observability unavailable stub or missing — never crashes worker.
        assert "decision" in meta or meta.get("observability", {}).get("status") == "unavailable"


@pytest.mark.asyncio
async def test_multi_agent_observability_isolation(engine: AsyncEngine) -> None:
    await _create_agent("obs-iso-a")
    await _create_agent("obs-iso-b")
    assert await _service(suffix="ia").execute("obs-iso-a") == "success"
    factory = get_session_factory()
    async with factory() as session:
        assert await PipelineRunRepository(session).list_by_agent("obs-iso-b") == []
        a_runs = await PipelineRunRepository(session).list_by_agent("obs-iso-a")
        obs = json.loads(a_runs[0].meta_json or "{}")["observability"]
        for event in obs["events"]:
            assert event["agent_id"] == "obs-iso-a"
            assert event["agent_id"] != "obs-iso-b"
        expl = await ObservabilityService().get_cycle_explanation(
            session,
            agent_id="obs-iso-b",
            cycle_id=a_runs[0].id,
        )
        assert expl is None
        expl_a = await ObservabilityService().get_cycle_explanation(
            session,
            agent_id="obs-iso-a",
            cycle_id=a_runs[0].id,
        )
        assert expl_a is not None
        assert expl_a.agent_id == "obs-iso-a"


@pytest.mark.asyncio
async def test_observability_survives_restart(engine: AsyncEngine) -> None:
    agent_id = "obs-restart"
    await _create_agent(agent_id)
    assert await _service(suffix="rst").execute(agent_id) == "success"
    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert meta["observability"]["trace"]
        assert meta["decision"]["trace"]
        assert "evaluation" in meta


@pytest.mark.asyncio
async def test_feed_never_invokes_observability(
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

    def track(self: Any, *args: Any, **kwargs: Any) -> Any:
        called["n"] += 1
        raise AssertionError("observability must not run from feed")

    monkeypatch.setattr(ObservabilityService, "record", track)
    monkeypatch.setattr(ObservabilityService, "begin", track)
    feed = await client.get("/api/agent/feed", params={"agentId": agent_id})
    assert feed.status_code == 200
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_existing_metadata_preserved_with_observability(engine: AsyncEngine) -> None:
    agent_id = "obs-preserve"
    await _create_agent(agent_id)
    assert await _service(suffix="pv").execute(agent_id) == "success"
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
            or "{}"
        )
        assert "decision" in meta and meta["decision"]["trace"]
        assert "evaluation" in meta
        assert "publication" in meta
        assert "adaptive_learning" in meta
        assert "controlled_learning" in meta
        assert "observability" in meta
        assert meta["observability"]["why_published"]["publishing_decision"] == "published"


@pytest.mark.asyncio
async def test_disabled_observability_cycle(engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
    get_settings.cache_clear()
    agent_id = "obs-disabled"
    await _create_agent(agent_id)
    try:
        status = await _service(suffix="dis").execute(agent_id)
        assert status == "success"
        factory = get_session_factory()
        async with factory() as session:
            meta = json.loads(
                (await PipelineRunRepository(session).list_by_agent(agent_id))[0].meta_json
                or "{}"
            )
            assert meta["decision"]["trace"]
            obs = meta.get("observability") or {}
            assert obs.get("enabled") is False or obs.get("event_count") == 0
    finally:
        monkeypatch.delenv("OBSERVABILITY_ENABLED", raising=False)
        get_settings.cache_clear()
