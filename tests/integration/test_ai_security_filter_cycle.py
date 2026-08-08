"""Module 20 — cycle skips when research has no AI-security findings."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.agents import AgentRepository
from app.db.repositories.pipeline_runs import PipelineRunRepository
from app.db.session import get_session_factory
from app.orchestration.locks import AgentLockManager, utc_now
from app.services.ai_security import AI_SECURITY_NO_FINDING_REASON
from app.services.candidates import CandidateGenerationService
from app.services.generation_cycle import GenerationCycleService
from app.services.llm.base import UnavailableLLMClient
from app.services.research.base import SourceConnector
from app.services.research.hub import ResearchHub
from app.services.research.models import ResearchItem


class _GenericCyberConnector(SourceConnector):
    name = "generic-cyber"

    async def fetch(self, query: str, *, max_items: int = 20) -> list[ResearchItem]:
        now = utc_now()
        return [
            ResearchItem(
                title="Hospital hit by ransomware campaign",
                summary="Classic ransomware encrypts hospital systems.",
                url="https://example.com/ransomware",
                source_name="fixture",
                discovered_at=now,
                content_hash="generic-1",
            ),
            ResearchItem(
                title="Phishing email steals passwords",
                summary="Credential phishing with no AI/LLM angle.",
                url="https://example.com/phishing",
                source_name="fixture",
                discovered_at=now,
                content_hash="generic-2",
            ),
        ]


@pytest.mark.asyncio
async def test_cycle_skips_when_no_ai_security_finding(engine: AsyncEngine) -> None:
    agent_id = "ai-sec-filter-agent"
    factory = get_session_factory()
    async with factory() as session:
        await AgentRepository(session).create_agent(
            agent_id=agent_id,
            name="Kestrel",
            domain="AI Security",
            status="running",
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        await session.commit()

    llm = UnavailableLLMClient()
    service = GenerationCycleService(
        session_factory=factory,
        lock_manager=AgentLockManager(),
        lease_seconds=300,
        research_hub=ResearchHub(connectors=[_GenericCyberConnector()], max_items=20),
        candidate_service=CandidateGenerationService(llm),  # type: ignore[arg-type]
        llm_client=llm,  # type: ignore[arg-type]
    )
    assert await service.execute(agent_id) == "skipped"

    async with factory() as session:
        runs = await PipelineRunRepository(session).list_by_agent(agent_id)
        assert runs[0].status == "skipped"
        assert runs[0].stage == "ai_security_filter"
        assert runs[0].error == AI_SECURITY_NO_FINDING_REASON
