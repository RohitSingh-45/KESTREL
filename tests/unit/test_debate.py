"""Unit tests for Module 6 self-debate engine."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.debate import (
    DebateDecisionEnum,
    DebateLLMResponse,
    DebateResult,
    SelfDebateService,
    apply_debate_guards,
    ground_debate_urls,
    is_strawman_against,
)
from app.services.llm.base import CandidateTopic
from app.services.research.base import normalize_url
from app.services.research.models import ResearchItem
from app.services.scoring import ScoredCandidate, TopicScores


def _scores(final_score: float = 0.9) -> TopicScores:
    return TopicScores(
        recency=0.9,
        persona_fit=0.9,
        importance=0.9,
        novelty=0.9,
        source_quality=0.9,
        evidence_quality=0.9,
        final_score=final_score,
        llm_confidence=0.9,
    )


def _candidate(url: str = "https://arxiv.org/abs/1") -> CandidateTopic:
    return CandidateTopic.model_validate(
        {
            "title": "Prompt injection risk in agent tools",
            "angle": "Exploit path",
            "why_now": "Fresh advisory",
            "relevance_to_persona": "Core AI Security",
            "source_urls": [url],
            "confidence": 0.9,
        }
    )


def _scored(
    *,
    topic_id: str = "t1",
    final_score: float = 0.9,
    url: str = "https://arxiv.org/abs/1",
) -> ScoredCandidate:
    candidate = _candidate(url)
    return ScoredCandidate(
        topic_id=topic_id,
        title=candidate.title,
        candidate=candidate,
        scores=_scores(final_score),
        rank=1,
        decision="selected",
        top_k=3,
    )


def _item(url: str = "https://arxiv.org/abs/1") -> ResearchItem:
    now = datetime(2026, 8, 7, tzinfo=timezone.utc)
    return ResearchItem(
        title="Paper",
        summary="Details a prompt injection vulnerability.",
        url=url,
        source_name="arXiv",
        published_at=now,
        discovered_at=now,
        content_hash="h1",
    )


def test_debate_schema_valid_accepted() -> None:
    raw = DebateLLMResponse.model_validate(
        {
            "for_argument": "Important AI Security disclosure with clear evidence.",
            "against_argument": (
                "Evidence is thin on exploitability and may overlap prior topics."
            ),
            "evidence_points": ["Source describes an injection path."],
            "weaknesses": ["Limited incident data."],
            "decision": "proceed",
            "confidence": 0.87,
            "source_urls": ["https://arxiv.org/abs/1"],
        }
    )
    assert raw.decision == DebateDecisionEnum.PROCEED
    assert raw.confidence == 0.87


def test_invalid_decision_rejected() -> None:
    with pytest.raises(ValidationError):
        DebateLLMResponse.model_validate(
            {
                "for_argument": "Important topic",
                "against_argument": "Evidence weak and potentially overstated here.",
                "evidence_points": [],
                "weaknesses": [],
                "decision": "maybe",
                "confidence": 0.5,
                "source_urls": [],
            }
        )


def test_confidence_bounds_clamped() -> None:
    raw = DebateLLMResponse.model_validate(
        {
            "for_argument": "Important topic with timely AI Security relevance.",
            "against_argument": "Source quality and novelty remain open questions.",
            "evidence_points": [],
            "weaknesses": [],
            "decision": "uncertain",
            "confidence": 1.7,
            "source_urls": [],
        }
    )
    assert raw.confidence == 1.0

    result = DebateResult(
        topic_id="t1",
        for_argument="x",
        against_argument="y",
        evidence_points=[],
        weaknesses=[],
        decision=DebateDecisionEnum.REJECT,
        confidence=-3,
        source_urls=[],
    )
    assert result.confidence == 0.0


def test_source_grounding_removes_unknown() -> None:
    allowed_key = normalize_url("https://arxiv.org/abs/1") or "https://arxiv.org/abs/1"
    grounded = ground_debate_urls(
        ["https://arxiv.org/abs/1", "https://invented.example/nope"],
        {allowed_key},
        {allowed_key: "https://arxiv.org/abs/1"},
    )
    assert grounded == ["https://arxiv.org/abs/1"]


def test_strong_for_against_preserved() -> None:
    for_arg = "Critical AI agent injection path with defensive urgency."
    against = (
        "Evidence may overstate severity; source lacks exploit details and "
        "overlaps prior jailbreak coverage."
    )
    raw = DebateLLMResponse.model_validate(
        {
            "for_argument": for_arg,
            "against_argument": against,
            "evidence_points": ["Advisory mentions agent tool exposure."],
            "weaknesses": ["No CVE id."],
            "decision": "proceed",
            "confidence": 0.8,
            "source_urls": ["https://arxiv.org/abs/1"],
        }
    )
    assert raw.for_argument == for_arg
    assert raw.against_argument == against


def test_strawman_detection() -> None:
    assert is_strawman_against("Some people may disagree.")
    assert is_strawman_against("short")
    assert not is_strawman_against(
        "Evidence strength is weak and the source may exaggerate practical risk."
    )


def test_low_score_cannot_proceed() -> None:
    decision = apply_debate_guards(
        decision=DebateDecisionEnum.PROCEED,
        confidence=0.9,
        grounded_urls=["https://arxiv.org/abs/1"],
        final_score=0.4,
        against_argument="Evidence is incomplete and novelty is questionable overall.",
        min_confidence=0.65,
        min_score_for_proceed=0.60,
    )
    assert decision == DebateDecisionEnum.UNCERTAIN


def test_low_confidence_becomes_uncertain() -> None:
    decision = apply_debate_guards(
        decision=DebateDecisionEnum.PROCEED,
        confidence=0.2,
        grounded_urls=["https://arxiv.org/abs/1"],
        final_score=0.9,
        against_argument="Evidence is incomplete and novelty is questionable overall.",
        min_confidence=0.65,
        min_score_for_proceed=0.60,
    )
    assert decision == DebateDecisionEnum.UNCERTAIN


def test_missing_source_guard_rejects() -> None:
    decision = apply_debate_guards(
        decision=DebateDecisionEnum.PROCEED,
        confidence=0.9,
        grounded_urls=[],
        final_score=0.9,
        against_argument="Evidence is incomplete and novelty is questionable overall.",
        min_confidence=0.65,
        min_score_for_proceed=0.60,
    )
    assert decision == DebateDecisionEnum.REJECT


@pytest.mark.asyncio
async def test_llm_unavailable_no_fake_debate() -> None:
    class _Down:
        available = False

        async def chat(self, **_kwargs: object) -> str:
            raise RuntimeError("down")

        async def structured_output(self, **_kwargs: object) -> object:
            raise RuntimeError("down")

    service = SelfDebateService(_Down())  # type: ignore[arg-type]
    result = await service.debate_topic(
        _scored(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        previous_titles=[],
    )
    assert result.error == "llm_unavailable"
    assert result.for_argument == ""
    assert result.against_argument == ""
    assert result.decision == DebateDecisionEnum.UNCERTAIN
    assert service.call_count == 0


@pytest.mark.asyncio
async def test_debate_timeout_returns_error_metadata() -> None:
    class _Timeout:
        available = True

        async def chat(self, **_kwargs: object) -> str:
            raise TimeoutError("timeout")

        async def structured_output(self, **_kwargs: object) -> object:
            raise TimeoutError("timeout")

    service = SelfDebateService(_Timeout())  # type: ignore[arg-type]
    result = await service.debate_topic(
        _scored(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        previous_titles=[],
    )
    assert result.error
    assert "timeout" in result.error.lower()
    assert result.for_argument == ""
    assert result.decision == DebateDecisionEnum.UNCERTAIN


@pytest.mark.asyncio
async def test_debate_success_applies_grounding() -> None:
    class _Ok:
        available = True

        async def chat(self, **_kwargs: object) -> str:
            return "{}"

        async def structured_output(self, **_kwargs: object) -> object:
            return DebateLLMResponse.model_validate(
                {
                    "for_argument": "Timely AI Security relevance with grounded evidence.",
                    "against_argument": (
                        "Source may be incomplete and impact could be overstated "
                        "relative to stronger candidates."
                    ),
                    "evidence_points": ["Paper describes injection risk."],
                    "weaknesses": ["Limited operational detail."],
                    "decision": "proceed",
                    "confidence": 0.88,
                    "source_urls": [
                        "https://arxiv.org/abs/1",
                        "https://invented.example/x",
                    ],
                }
            )

    service = SelfDebateService(_Ok())  # type: ignore[arg-type]
    result = await service.debate_topic(
        _scored(),
        persona_name="Kestrel",
        persona_domain="AI Security",
        research_items=[_item()],
        previous_titles=[],
    )
    assert result.error is None
    assert result.source_urls == ["https://arxiv.org/abs/1"]
    assert result.decision == DebateDecisionEnum.PROCEED
    assert service.call_count == 1
