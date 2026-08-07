"""Unit tests for Module 16 DecisionService."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.db.repositories.agents import AgentRepository
from app.db.session import get_session_factory
from app.orchestration.locks import utc_now
from app.services.decision import DecisionService
from app.services.decision.evaluator import build_decision_context


def test_decision_service_zero_llm_calls() -> None:
    service = DecisionService()
    service.begin(agent_id="a1", cycle_id="c1")
    ctx = service.build_context(
        agent_id="a1",
        cycle_id="c1",
        research_items=[SimpleNamespace(url="https://arxiv.org/abs/1")],
        candidates=[SimpleNamespace()],
        selected_count=1,
    )
    result = service.evaluate_gate("research", ctx)
    assert result.action == "proceed"
    assert service.llm_calls == 0
    meta = service.meta_payload()
    assert meta["gates_passed"] >= 1
    assert "trace" in meta


def test_decision_service_failure_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DecisionService()
    service.begin(agent_id="a1", cycle_id="c1")

    def boom(_ctx, _policy):  # noqa: ANN001
        raise RuntimeError("decision boom")

    monkeypatch.setattr(
        "app.services.decision.service.research_gate",
        boom,
    )
    # Patch mapping via evaluate path — force gate fn failure.
    monkeypatch.setattr(service, "_gate_fn", lambda _g: boom)
    ctx = service.build_context(agent_id="a1", research_items=[])
    result = service.evaluate_gate("research", ctx)
    assert result.action == "skip"
    assert result.metadata.get("status") == "unavailable"


def test_publishing_gate_failure_prefers_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DecisionService()
    service.begin(agent_id="a1", cycle_id="c1")

    def boom(_ctx, _policy):  # noqa: ANN001
        raise RuntimeError("publish decision boom")

    monkeypatch.setattr(service, "_gate_fn", lambda _g: boom)
    ctx = service.build_context(
        agent_id="a1",
        generated_post=SimpleNamespace(
            rationale="Selected for AI Security evidence.",
            source_urls=["https://arxiv.org/abs/1"],
        ),
        final_validation_passed=True,
    )
    result = service.evaluate_gate("publishing", ctx)
    assert result.action == "stop"
    assert result.blocking is True


def test_build_context_counts_debate_and_consistency() -> None:
    debate_proceed = SimpleNamespace(decision=SimpleNamespace(value="proceed"), error=None)
    debate_reject = SimpleNamespace(decision=SimpleNamespace(value="reject"), error=None)
    consistency_ok = SimpleNamespace(
        decision=SimpleNamespace(value="approve"), overall_score=0.9
    )
    consistency_bad = SimpleNamespace(
        decision=SimpleNamespace(value="reject"), overall_score=0.2
    )
    ctx = build_decision_context(
        agent_id="a1",
        research_items=[SimpleNamespace(url="https://arxiv.org/a")],
        candidates=[1, 2],
        selected_count=2,
        debated_pairs=[
            (SimpleNamespace(), debate_proceed),
            (SimpleNamespace(), debate_reject),
        ],
        consistency_triples=[
            (SimpleNamespace(), debate_proceed, consistency_ok, []),
            (SimpleNamespace(), debate_reject, consistency_bad, []),
        ],
    )
    assert ctx.debate_proceed_count == 1
    assert ctx.consistency_approve_count == 1


@pytest.mark.asyncio
async def test_multi_agent_decision_isolation(engine) -> None:
    factory = get_session_factory()
    async with factory() as session:
        now = utc_now()
        for agent_id in ("dec-a", "dec-b"):
            await AgentRepository(session).create_agent(
                agent_id=agent_id,
                name="Kestrel",
                domain="AI Security",
                status="running",
                created_at=now,
                updated_at=now,
            )
        await session.commit()

    a = DecisionService()
    a.begin(agent_id="dec-a", cycle_id="c-a")
    a.evaluate_gate(
        "research",
        a.build_context(
            agent_id="dec-a",
            research_items=[SimpleNamespace(url="https://arxiv.org/a")],
        ),
    )
    b = DecisionService()
    b.begin(agent_id="dec-b", cycle_id="c-b")
    assert a.trace.agent_id == "dec-a"
    assert b.trace.agent_id == "dec-b"
    assert all(d.gate == "research" for d in a.trace.decisions)
    assert b.trace.decisions == []
