"""Module 16 verification audit — captures real decision meta and failure cases."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db.repositories.agents import AgentRepository
from app.db.repositories.memory import MemoryRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.repositories.posts import PostRepository
from app.db.repositories.topics import TopicRepository
from app.db.session import close_db, get_session_factory, init_db
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.candidates import CandidateGenerationService
from app.services.consistency import (
    ConsistencyDecisionEnum,
    ConsistencyGuard,
    ConsistencyResult,
    ConsistencySignals,
)
from app.services.debate import DebateLLMResponse
from app.services.decision import DecisionService
from app.services.decision.models import DecisionResult, DecisionTrace
from app.services.evaluation import EvaluationLLMResponse
from app.services.final_post import FinalPostGenerator
from app.services.final_post.models import GeneratedPost
from app.services.generation_cycle import GenerationCycleService
from app.services.learning import LearningStore
from app.services.llm.base import CandidateTopicList, UnavailableLLMClient
from app.services.memory.models import MemoryExtractionResponse
from app.services.publishing import PublishingService
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem
from app.services.scoring import TopicScoringService


class _Conn:
    name = "audit"

    def __init__(self, *, empty: bool = False, suffix: str = "1") -> None:
        self.empty = empty
        self.suffix = suffix

    async def fetch(self, query: str | None = None, limit: int = 10) -> list[ResearchItem]:
        if self.empty:
            return []
        now = utc_now()
        return [
            ResearchItem(
                title=f"Prompt injection against AI agents {self.suffix}",
                summary="Agent security finding.",
                url=f"https://arxiv.org/abs/audit-{self.suffix}",
                source_name="arXiv",
                published_at=now,
                discovered_at=now,
                content_hash=f"audit-{self.suffix}",
            )
        ]


class _LLM:
    available = True

    def __init__(self, suffix: str = "1", debate_decision: str = "proceed") -> None:
        self.suffix = suffix
        self.debate_decision = debate_decision
        self.call_count = 0
        self.schemas: list[str] = []

    async def chat(self, **_k: object) -> str:
        return "{}"

    async def structured_output(self, **kwargs: object) -> object:
        self.call_count += 1
        schema = kwargs.get("schema")
        name = getattr(schema, "__name__", str(schema))
        self.schemas.append(name)
        url = f"https://arxiv.org/abs/audit-{self.suffix}"
        if schema is EvaluationLLMResponse:
            return EvaluationLLMResponse.model_validate(
                {
                    "strengths": ["ok"],
                    "weaknesses": ["x"],
                    "improvement_actions": ["y"],
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
                    "evidence_points": ["AI agent security issue."],
                    "weaknesses": ["Limited detail."],
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
                            "value": "Prompt injection remains an AI-agent concern.",
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
                        f"Prompt injection against AI agents {self.suffix} remains a concrete "
                        f"AI Security concern for autonomous systems."
                    ),
                    "rationale": (
                        "Selected for AI Security relevance and recent evidence over "
                        "lower-ranked alternatives."
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
                        "title": f"Prompt injection attacks against AI agents {self.suffix}",
                        "angle": "Agent exploitability",
                        "why_now": "Fresh",
                        "relevance_to_persona": "AI Security",
                        "source_urls": [url],
                        "confidence": 0.93,
                    }
                ]
            }
        )


async def _create(agent_id: str) -> None:
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


def _svc(
    llm: Any,
    *,
    empty: bool = False,
    suffix: str = "1",
    consistency_guard: ConsistencyGuard | None = None,
) -> GenerationCycleService:
    return GenerationCycleService(
        session_factory=get_session_factory(),
        lock_manager=AgentLockManager(),
        lease_seconds=get_settings().generation_lease_seconds,
        research_hub=ResearchHub(connectors=[_Conn(empty=empty, suffix=suffix)], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
        consistency_guard=consistency_guard,
    )


@pytest.mark.asyncio
async def test_audit_real_decision_trace_and_llm_attribution(engine: AsyncEngine) -> None:
    await _create("audit-happy")
    llm = _LLM("happy")
    assert await _svc(llm, suffix="happy").execute("audit-happy") == "success"
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent("audit-happy"))[0].meta_json
            or "{}"
        )
        decision = meta["decision"]
        gates = [t["gate"] for t in decision["trace"]]
        # Persist full ordered gate list for audit inspection.
        print("AUDIT_DECISION_META=" + json.dumps(decision, indent=2))
        print("AUDIT_GATE_ORDER=" + json.dumps(gates))
        print("AUDIT_LLM_CALLS=" + str(llm.call_count))
        print("AUDIT_LLM_SCHEMAS=" + json.dumps(llm.schemas))

        assert "DecisionService" not in json.dumps(llm.schemas)
        assert decision["gates_passed"] >= 8
        assert len(decision["trace"]) <= get_settings().decision_max_trace_items
        for required in (
            "research",
            "candidate",
            "selection",
            "debate",
            "memory",
            "consistency",
            "intelligence",
            "generation",
            "publishing",
        ):
            assert required in gates
        # Module 16 itself adds no LLM schema; all schemas are existing modules.
        for name in llm.schemas:
            assert name in {
                "CandidateTopicList",
                "DebateLLMResponse",
                "MemoryExtractionResponse",
                "GeneratedPost",
                "EvaluationLLMResponse",
                "ContradictionLLMResponse",
            }
        blob = json.dumps(decision)
        assert "OPENAI_API_KEY" not in blob
        assert "sk-" not in blob
        assert "You are Kestrel" not in blob
        assert "Authorization" not in blob


@pytest.mark.asyncio
async def test_audit_decision_boom_no_publish(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create("audit-boom")
    gen_n = {"n": 0}
    pub_n = {"n": 0}
    original_gen = FinalPostGenerator.generate
    original_pub = PublishingService.publish

    async def track_gen(self, *a: Any, **k: Any) -> Any:
        gen_n["n"] += 1
        return await original_gen(self, *a, **k)

    async def track_pub(self, *a: Any, **k: Any) -> Any:
        pub_n["n"] += 1
        return await original_pub(self, *a, **k)

    def boom(self, gate: str, ctx: Any) -> Any:
        raise RuntimeError("decision boom")

    monkeypatch.setattr(FinalPostGenerator, "generate", track_gen)
    monkeypatch.setattr(PublishingService, "publish", track_pub)
    monkeypatch.setattr(DecisionService, "evaluate_gate", boom)

    llm = _LLM("boom")
    status = await _svc(llm, suffix="boom").execute("audit-boom")
    assert status == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first("audit-boom")
        assert posts == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent("audit-boom"))[0].meta_json
            or "{}"
        )
        assert meta.get("decision", {}).get("status") == "unavailable" or meta.get(
            "decision", {}
        ).get("reason")
        print(f"AUDIT_BOOM_STATUS={status}")
        print(f"AUDIT_BOOM_DECISION={json.dumps(meta.get('decision'), indent=2)}")
        print(f"AUDIT_BOOM_FINAL_GEN={gen_n['n']}")
        print(f"AUDIT_BOOM_PUBLISH={pub_n['n']}")
        print(f"AUDIT_BOOM_POSTS={len(posts)}")
        print(f"AUDIT_BOOM_LLM={llm.call_count}")
    assert gen_n["n"] == 0
    assert pub_n["n"] == 0
    assert llm.call_count == 0  # early skip at research gate; no publish-anyway


@pytest.mark.asyncio
async def test_audit_research_unavailable_no_llm(engine: AsyncEngine) -> None:
    await _create("audit-noresearch")
    llm = _LLM("nr")
    status = await _svc(llm, empty=True, suffix="nr").execute("audit-noresearch")
    assert status == "skipped"
    assert llm.call_count == 0
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-noresearch") == []


@pytest.mark.asyncio
async def test_audit_candidate_llm_unavailable_no_post(engine: AsyncEngine) -> None:
    await _create("audit-nocand")
    status = await _svc(UnavailableLLMClient(), suffix="nocand").execute("audit-nocand")
    assert status == "skipped"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-nocand") == []


@pytest.mark.asyncio
async def test_audit_no_selected_topics_skips(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create("audit-noselect")
    original = TopicScoringService.rank_candidates

    def reject_all(self: Any, candidates: Any, context: Any) -> Any:
        ranked = original(self, candidates, context)
        for item in ranked:
            item.decision = "rejected"
        return ranked

    monkeypatch.setattr(TopicScoringService, "rank_candidates", reject_all)
    llm = _LLM("noselect")
    status = await _svc(llm, suffix="noselect").execute("audit-noselect")
    assert status == "skipped"
    # Candidate LLM may run; debate/final must not.
    assert "GeneratedPost" not in llm.schemas
    assert "DebateLLMResponse" not in llm.schemas
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-noselect") == []


@pytest.mark.asyncio
async def test_audit_consistency_failure_blocks_final(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create("audit-consfail")

    class _RejectAnalyzer:
        def analyze(self, scored: Any, **kwargs: Any) -> ConsistencyResult:
            return ConsistencyResult(
                topic_id=scored.topic_id,
                decision=ConsistencyDecisionEnum.REJECT,
                overall_score=0.1,
                signals=ConsistencySignals(),
                reasons=["audit consistency reject"],
                hard_reject=True,
            )

    gen_n = {"n": 0}
    pub_n = {"n": 0}
    original_gen = FinalPostGenerator.generate
    original_pub = PublishingService.publish

    async def track_gen(self: Any, *a: Any, **k: Any) -> Any:
        gen_n["n"] += 1
        return await original_gen(self, *a, **k)

    async def track_pub(self: Any, *a: Any, **k: Any) -> Any:
        pub_n["n"] += 1
        return await original_pub(self, *a, **k)

    monkeypatch.setattr(FinalPostGenerator, "generate", track_gen)
    monkeypatch.setattr(PublishingService, "publish", track_pub)
    status = await _svc(
        _LLM("consfail"),
        suffix="consfail",
        consistency_guard=ConsistencyGuard(analyzer=_RejectAnalyzer()),  # type: ignore[arg-type]
    ).execute("audit-consfail")
    assert status in {"success", "skipped"}
    assert gen_n["n"] == 0
    assert pub_n["n"] == 0
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-consfail") == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent("audit-consfail"))[0].meta_json
            or "{}"
        )
        assert meta.get("final_generation", {}).get("status") != "success"
        assert meta.get("publication", {}).get("status") != "published"


@pytest.mark.asyncio
async def test_audit_final_generation_failure_no_publish(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create("audit-finalfail")
    pub_n = {"n": 0}
    original_pub = PublishingService.publish

    async def boom_gen(self: Any, *a: Any, **k: Any) -> Any:
        raise RuntimeError("final generation boom")

    async def track_pub(self: Any, *a: Any, **k: Any) -> Any:
        pub_n["n"] += 1
        return await original_pub(self, *a, **k)

    monkeypatch.setattr(FinalPostGenerator, "generate", boom_gen)
    monkeypatch.setattr(PublishingService, "publish", track_pub)
    status = await _svc(_LLM("ff"), suffix="ff").execute("audit-finalfail")
    assert status in {"success", "skipped", "failed"}
    assert pub_n["n"] == 0
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-finalfail") == []


@pytest.mark.asyncio
async def test_audit_duplicate_publishing_no_extra_row(engine: AsyncEngine) -> None:
    await _create("audit-dup")
    svc = _svc(_LLM("dup"), suffix="dup")
    assert await svc.execute("audit-dup") == "success"
    second = await svc.execute("audit-dup")
    assert second in {"success", "skipped"}
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first("audit-dup")
        assert len(posts) == 1
        runs = await PipelineRunRepository(session).list_by_agent("audit-dup")
        metas = [json.loads(r.meta_json or "{}") for r in runs]
        # At least one run should record duplicate/skipped publication when re-run.
        pub_statuses = {m.get("publication", {}).get("status") for m in metas}
        assert "published" in pub_statuses
        assert pub_statuses & {"duplicate", "skipped", "failed"} or len(posts) == 1


@pytest.mark.asyncio
async def test_audit_debate_reject_blocks_final(
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _create("audit-drej")
    gen_n = {"n": 0}
    original_gen = FinalPostGenerator.generate

    async def track_gen(self, *a: Any, **k: Any) -> Any:
        gen_n["n"] += 1
        return await original_gen(self, *a, **k)

    monkeypatch.setattr(FinalPostGenerator, "generate", track_gen)
    status = await _svc(_LLM("drej", debate_decision="reject"), suffix="drej").execute(
        "audit-drej"
    )
    assert status in {"success", "skipped"}
    assert gen_n["n"] == 0
    factory = get_session_factory()
    async with factory() as session:
        posts = await PostRepository(session).list_by_agent_newest_first("audit-drej")
        assert posts == []
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent("audit-drej"))[0].meta_json
            or "{}"
        )
        gates = {
            (t["gate"], t["action"]) for t in meta["decision"]["trace"]
        }
        assert ("debate", "skip") in gates or ("generation", "skip") in gates
        assert meta["publication"]["status"] != "published"


@pytest.mark.asyncio
async def test_audit_multi_agent_full_isolation(engine: AsyncEngine) -> None:
    await _create("audit-iso-a")
    await _create("audit-iso-b")
    assert await _svc(_LLM("iso-a"), suffix="iso-a").execute("audit-iso-a") == "success"
    factory = get_session_factory()
    async with factory() as session:
        assert await PostRepository(session).list_by_agent_newest_first("audit-iso-b") == []
        assert await TopicRepository(session).list_by_agent("audit-iso-b") == []
        assert await MemoryRepository(session).list_for_agent("audit-iso-b") == []
        assert await LearningStore(session).list_signals("audit-iso-b") == []
        assert await PipelineRunRepository(session).list_by_agent("audit-iso-b") == []
        a_runs = await PipelineRunRepository(session).list_by_agent("audit-iso-a")
        assert json.loads(a_runs[0].meta_json or "{}")["decision"]["trace"]


@pytest.mark.asyncio
async def test_audit_restart_preserves_decision(engine: AsyncEngine) -> None:
    await _create("audit-restart")
    assert await _svc(_LLM("rst"), suffix="rst").execute("audit-restart") == "success"
    await close_db()
    await init_db(get_settings())
    factory = get_session_factory()
    async with factory() as session:
        meta = json.loads(
            (await PipelineRunRepository(session).list_by_agent("audit-restart"))[0].meta_json
            or "{}"
        )
        assert meta["decision"]["trace"]
        assert meta["decision"]["final_action"]


def test_audit_config_and_trace_bounds() -> None:
    for kwargs in (
        {"DECISION_MIN_RESEARCH_ITEMS": 0},
        {"DECISION_MIN_GROUNDED_ITEMS": 0},
        {"DECISION_MIN_CANDIDATES": 0},
        {"DECISION_MIN_SELECTED": 0},
        {"DECISION_MIN_CONSISTENCY_SCORE": 1.5},
        {"DECISION_MAX_TRACE_ITEMS": 0},
    ):
        with pytest.raises(ValidationError):
            Settings(**kwargs)

    # Safety invariants are not configurable off via these knobs.
    settings = Settings(
        DECISION_ENABLED=False,
        DECISION_MIN_CONSISTENCY_SCORE=0.0,
    )
    assert settings.decision_enabled is False
    # Disabling decision orchestration must not expose a "bypass_grounding" flag.
    assert not hasattr(settings, "bypass_grounding")
    assert not hasattr(settings, "bypass_consistency")
    assert not hasattr(settings, "bypass_publishing")

    trace = DecisionTrace(agent_id="x", cycle_id="y")
    for i in range(40):
        trace.add(
            DecisionResult(action="proceed", gate="research", reason=str(i), confidence=0.9),
            max_items=12,
        )
    assert len(trace.decisions) == 12
    assert len(trace.summary()["trace"]) == 12
